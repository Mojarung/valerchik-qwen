"""Замер на эталонном наборе: `python -m app.rag.evaluate golden.json [опции]`.

Поиск: попадание нужного фрагмента в первые k (страница документа или ключевые факты).
Агент (--agent): доля ответов, факты эталона в ответе, честные отказы не по теме,
картинки там, где в разделе-источнике есть скриншоты, задержка.
Опыты: --embed-url/--alias — другая модель эмбеддингов в своей коллекции (--reindex
пересобирает её), --grid — перебор весов слияния, --rerank — реранкер.
"""

import argparse
import asyncio
import json
import os
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from app.adapters.embeddings import TEIEmbedder
from app.adapters.vectorstore import QdrantStore
from app.config import get_settings
from app.models import KBArticle
from app.rag.agent import run_support_agent
from app.rag.index import vector_docs
from app.rag.search import RagRetriever
from app.rag.text import lemmas
from app.runtime import AppRuntime
from app.services.agent import AgentRequest

KS = (1, 3, 6)
AGENT_CONCURRENCY = 4
FACT_SHARE = 0.5
SAME_SOLUTION = 0.5
IMAGE_CONCURRENCY = 16
# (вес словарного поиска, rrf_k, решений СТП в выдаче)
GRID = [(lexical, 60, tickets) for lexical in (0.5, 0.7) for tickets in (2, 3, 4, 6)]
_SPACE = re.compile(r"\s+")
_KEY = re.compile(r"[^a-zа-я]+")  # тот же ключ решения, что в app/rag/tickets.py


def _flat(text: str) -> str:
    return _SPACE.sub(" ", text.lower().replace("ё", "е")).strip()


def _facts_hit(facts: list[str], text: str) -> float:
    if not facts:
        return 0.0
    flat = _flat(text)
    return sum(1 for f in facts if _flat(f) in flat) / len(facts)


def _solution_key(text: str) -> str:
    return _KEY.sub(" ", text.lower().replace("ё", "е")).strip()


def _audience(item: dict[str, Any]) -> str:
    return "" if item.get("audience") in (None, "", "any") else str(item["audience"])


def _query(item: dict[str, Any]) -> str:
    return "\n".join([*item.get("history", [])[-1:], item["q"]])


def _in_source(item: dict[str, Any], origin: dict[str, Any]) -> bool:
    return (
        item["source"] == "pdf"
        and origin.get("file") == item.get("doc")
        and bool(set(origin.get("pages") or []) & set(item.get("pages") or []))
    )


class Corpus:
    """Происхождение и картинки активных фрагментов — для сверки с эталоном."""

    def __init__(self, rows: list[tuple[str, dict[str, Any] | None, list[Any] | None]]) -> None:
        self.origins = {article_id: origin or {} for article_id, origin, _ in rows}
        self.images = {article_id: len(images or []) for article_id, _, images in rows}

    def match(self, item: dict[str, Any], doc: Any) -> bool:
        if item.get("answer_key"):
            # Реальный вопрос из выгрузки СТП: верно то самое типовое решение или равное ему
            # по смыслу — поддержка одно и то же решение писала разными словами
            if _solution_key(doc.answer).startswith(item["answer_key"]):
                return True
            expected, found = set(lemmas(item["answer"])), set(lemmas(doc.answer[: len(item["answer"])]))
            return bool(expected) and len(expected & found) / len(expected | found) >= SAME_SOLUTION
        if _in_source(item, self.origins.get(doc.article_id, {})):
            return True
        return _facts_hit(item.get("facts", []), f"{doc.question}\n{doc.answer}") >= FACT_SHARE

    def source_has_images(self, item: dict[str, Any]) -> bool:
        return any(
            self.images.get(article_id) and _in_source(item, origin)
            for article_id, origin in self.origins.items()
        )


async def _corpus(rt: AppRuntime) -> Corpus:
    async with rt.db.session_factory() as db:
        rows = (
            await db.execute(
                select(KBArticle.id, KBArticle.origin, KBArticle.images).where(KBArticle.status == "active")
            )
        ).all()
    return Corpus([tuple(row) for row in rows])


async def eval_search(rt: AppRuntime, items: list[dict[str, Any]], corpus: Corpus) -> dict[str, Any]:
    ranks: dict[str, list[int | None]] = defaultdict(list)
    tops: dict[str, list[float]] = defaultdict(list)
    latency: list[float] = []
    misses = []
    for item in items:
        started = time.perf_counter()
        docs = await rt.retriever.search(_query(item), top_k=max(KS), audience=_audience(item))
        latency.append(time.perf_counter() - started)
        tops[item["source"]].append(docs[0].score if docs else 0.0)
        if item["source"] == "ood":
            continue
        rank = next((i for i, d in enumerate(docs) if corpus.match(item, d)), None)
        ranks[item["source"]].append(rank)
        if rank is None:
            misses.append({"id": item["id"], "q": item["q"], "got": [d.question for d in docs[:3]]})
    report: dict[str, Any] = {}
    for source, got in ranks.items():
        report[source] = {
            f"hit@{k}": round(sum(1 for r in got if r is not None and r < k) / len(got), 3) for k in KS
        }
        report[source]["mrr"] = round(sum(1 / (r + 1) for r in got if r is not None) / len(got), 3)
        report[source]["n"] = len(got)
    report["top_score"] = {s: round(statistics.mean(v), 3) for s, v in tops.items()}
    report["latency_ms_p50"] = round(statistics.median(latency) * 1000)
    report["misses"] = misses
    return report


async def eval_grid(rt: AppRuntime, items: list[dict[str, Any]], corpus: Corpus) -> list[dict[str, Any]]:
    retriever = rt.retriever
    rows = []
    for lexical, rrf_k, tickets in GRID:
        retriever.lexical_weight, retriever.rrf_k, retriever.max_tickets = lexical, rrf_k, tickets
        report = await eval_search(rt, items, corpus)
        row: dict[str, Any] = {"lexical": lexical, "rrf_k": rrf_k, "tickets": tickets}
        for source in ("pdf", "stp", "stp_real"):
            if source in report:
                row.update({f"{source}@{k}": report[source][f"hit@{k}"] for k in (1, 3, 6)})
        rows.append(row)
    rows.sort(key=lambda r: -sum(v for k, v in r.items() if k.endswith(("@3", "@6"))))
    return rows


async def _ask(rt: AppRuntime, item: dict[str, Any]) -> dict[str, Any]:
    history: list[tuple[str, str]] = []
    for previous in item.get("history", []):
        before = await run_support_agent(
            rt.dyn,
            rt.retriever,
            rt.classifier,
            AgentRequest(previous, history=list(history), audience=_audience(item), timeout_s=90),
        )
        history += [("user", previous), ("assistant", before.answer)]
    started = time.perf_counter()
    outcome = await run_support_agent(
        rt.dyn,
        rt.retriever,
        rt.classifier,
        AgentRequest(item["q"], history=history, audience=_audience(item), timeout_s=90),
    )
    return {
        "id": item["id"],
        "source": item["source"],
        "q": item["q"],
        "answered": outcome.answered,
        "facts": _facts_hit(item.get("facts", []), outcome.answer),
        "latency": round(time.perf_counter() - started, 2),
        "answer": outcome.answer,
        "cited": [c["article_id"] for c in outcome.citations],
        "searches": [s.get("query") for s in outcome.tool_trace if s.get("tool") == "search_kb"],
    }


def _images_report(
    items: list[dict[str, Any]], results: list[dict[str, Any]], corpus: Corpus
) -> dict[str, Any]:
    by_id = {item["id"]: item for item in items}
    expected: list[bool] = []
    unexpected: list[bool] = []
    for r in results:
        if not r["answered"]:
            continue
        shown = any(corpus.images.get(article_id) for article_id in r["cited"])
        (expected if corpus.source_has_images(by_id[r["id"]]) else unexpected).append(shown)
    return {
        "answers_with_screenshot_source": len(expected),
        "shown_when_expected": round(sum(expected) / len(expected), 3) if expected else None,
        "shown_when_not_expected": round(sum(unexpected) / len(unexpected), 3) if unexpected else None,
    }


async def eval_agent(rt: AppRuntime, items: list[dict[str, Any]], corpus: Corpus) -> dict[str, Any]:
    gate = asyncio.Semaphore(AGENT_CONCURRENCY)

    async def one(item: dict[str, Any]) -> dict[str, Any]:
        async with gate:
            return await _ask(rt, item)

    results = await asyncio.gather(*(one(item) for item in items))
    report: dict[str, Any] = {}
    for source in sorted({r["source"] for r in results}):
        rows = [r for r in results if r["source"] == source]
        if source == "ood":
            report[source] = {
                "refused": round(sum(not r["answered"] for r in rows) / len(rows), 3),
                "n": len(rows),
            }
        else:
            report[source] = {
                "answered": round(sum(r["answered"] for r in rows) / len(rows), 3),
                "facts": round(statistics.mean(r["facts"] for r in rows), 3),
                "n": len(rows),
            }
    report["images"] = _images_report(items, results, corpus)
    latencies = sorted(r["latency"] for r in results)
    report["latency_s_p50"] = statistics.median(latencies)
    report["latency_s_p90"] = latencies[int(len(latencies) * 0.9)]
    report["results"] = results
    return report


async def _runtime(args: argparse.Namespace) -> AppRuntime:
    rt = AppRuntime(get_settings())
    await rt.db.create_all()
    await rt.dyn.load()
    # Свой ключ LLM для замера: параллельные прогоны не делят лимит запросов с боевым сервисом
    if key := os.environ.get("EVAL_LLM_API_KEY"):
        rt.dyn.override("llm.api_key", key)
    if args.rerank is not None:
        rt.dyn.override("retrieval.use_reranker", args.rerank)
    if args.embed_url:
        embedder = TEIEmbedder(args.embed_url)
        store = QdrantStore(rt.settings.qdrant_url, alias=args.alias)
        if args.reindex:
            await _reindex(rt, embedder, store, args)
        rt.retriever = RagRetriever(embedder, store, rt.dyn)
    if args.dense_only:
        rt.retriever.lexical_weight = 0.0
    await rt.retriever.refresh_from_db(rt.db.session_factory)
    return rt


async def _reindex(
    rt: AppRuntime, embedder: TEIEmbedder, store: QdrantStore, args: argparse.Namespace
) -> None:
    """Опытная коллекция: все активные фрагменты или только виды из --kinds; --no-questions —
    без вопросов и формулировок, чтобы сравнивать модели на одних и тех же текстах фрагментов."""
    async with rt.db.session_factory() as db:
        articles = list((await db.execute(select(KBArticle).where(KBArticle.status == "active"))).scalars())
    kinds = {k for k in args.kinds.split(",") if k}
    if kinds:
        articles = [a for a in articles if (a.origin or {}).get("kind") in kinds]
    if args.no_questions:
        for article in articles:  # объекты отсоединены от сессии: в базу это не уходит
            article.origin = {
                k: v for k, v in (article.origin or {}).items() if k not in ("questions", "examples")
            }
    started = time.perf_counter()
    docs = await vector_docs(embedder, articles)
    await store.ensure_ready(embedder.dim)
    await store.reindex(docs)
    took = time.perf_counter() - started
    print(f"reindex {args.alias}: {len(articles)} статей, {len(docs)} точек за {took:.0f} с")


async def check_images(rt: AppRuntime, base_url: str) -> dict[str, Any]:
    """Картинки фрагментов сохранены и отдаются: каждый адрес запрашивается у сервиса."""
    async with rt.db.session_factory() as db:
        found = (await db.execute(select(KBArticle.images).where(KBArticle.status == "active"))).all()
    rows = [row for row in found if row[0]]
    urls = sorted({image["url"] for (images,) in rows for image in images or [] if image.get("url")})
    captioned = sum(1 for (images,) in rows for image in images or [] if image.get("caption"))
    gate = asyncio.Semaphore(IMAGE_CONCURRENCY)
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:

        async def served(url: str) -> bool:
            async with gate:
                try:
                    resp = await client.get(url)
                except httpx.HTTPError:
                    return False
                is_image = resp.headers.get("content-type", "").startswith("image/")
                return resp.status_code == httpx.codes.OK and is_image

        results = await asyncio.gather(*(served(url) for url in urls))
    broken = [url for url, ok in zip(urls, results, strict=True) if not ok]
    return {
        "fragments_with_images": len(rows),
        "images": len(urls),
        "captioned_share": round(captioned / max(1, sum(len(images or []) for (images,) in rows)), 3),
        "served_ok": len(urls) - len(broken),
        "broken_examples": broken[:5],
    }


async def main(args: argparse.Namespace) -> None:
    items = json.loads(args.golden.read_text(encoding="utf-8"))
    rt = await _runtime(args)
    corpus = await _corpus(rt)
    if args.images:
        print(json.dumps(await check_images(rt, args.images), ensure_ascii=False, indent=1))
    report: dict[str, Any] = {}
    if args.grid:
        report["grid"] = await eval_grid(rt, items, corpus)
        for row in report["grid"][:8]:
            print(json.dumps(row, ensure_ascii=False))
    else:
        report["search"] = await eval_search(rt, items, corpus)
        print(
            json.dumps(
                {k: v for k, v in report["search"].items() if k != "misses"}, ensure_ascii=False, indent=1
            )
        )
    if args.agent:
        report["agent"] = await eval_agent(rt, items, corpus)
        print(
            json.dumps(
                {k: v for k, v in report["agent"].items() if k != "results"}, ensure_ascii=False, indent=1
            )
        )
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    await rt.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("golden", type=Path)
    parser.add_argument("--agent", action="store_true")
    parser.add_argument("--grid", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--rerank", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--embed-url", default="")
    parser.add_argument("--alias", default="exp_current")
    parser.add_argument("--reindex", action="store_true")
    parser.add_argument("--kinds", default="", help="виды фрагментов опытной коллекции через запятую")
    parser.add_argument("--no-questions", action="store_true")
    parser.add_argument("--dense-only", action="store_true")
    parser.add_argument("--images", default="", help="адрес API для проверки всех картинок фрагментов")
    asyncio.run(main(parser.parse_args()))
