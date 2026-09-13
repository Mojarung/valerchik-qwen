"""Загрузка документа в базу знаний: разбор → фрагменты в БД (прошлая версия в архив) → индекс."""

import asyncio
import io
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import openpyxl
from sqlalchemy import select, update

from app.ingest.pipeline import document_key
from app.models import KBArticle, new_id
from app.rag.generic import parse_document, parse_json, parse_table
from app.rag.index import vector_docs
from app.rag.passages import PIPELINE, Passage
from app.rag.pdf import doc_title, parse_pdf
from app.rag.questions import attach
from app.rag.tickets import is_ticket_export, parse_tickets

if TYPE_CHECKING:
    from app.runtime import AppRuntime

ProgressCallback = Callable[[int, int], Awaitable[None]]
_SLUG = re.compile(r"[^a-z0-9а-яё]+")


def parse_file(filename: str, data: bytes) -> list[Passage]:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return parse_pdf(data, filename)
    if lower.endswith((".xlsx", ".xlsm")):
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
        try:
            header = next(book.active.iter_rows(max_row=1, values_only=True), ())
        finally:
            book.close()
        return parse_tickets(data) if is_ticket_export(header) else parse_table(data, filename)
    if lower.endswith(".json"):
        return parse_json(data, filename)
    return parse_document(data, filename)


def _slug(title: str) -> str:
    return _SLUG.sub("-", title.lower()).strip("-")[:48] or "doc"


async def _store_images(rt: AppRuntime, passages: list[Passage], title: str) -> dict[str, str]:
    urls: dict[str, str] = {}
    slug = _slug(title)
    for passage in passages:
        for figure in passage.figures:
            if figure.digest not in urls:
                urls[figure.digest] = await rt.media.put(
                    f"kb/rag/{slug}/{figure.digest}.webp", figure.data, "image/webp"
                )
    return urls


def _article(passage: Passage, urls: dict[str, str], doc_key: str, title: str, status: str) -> KBArticle:
    return KBArticle(
        id=new_id(),
        question=passage.title[:1000],
        answer=passage.text,
        category=passage.category,
        keywords=[],
        images=[
            {"url": urls[f.digest], "caption": f.caption, "page": f.page + 1, "figure": ""}
            for f in passage.figures
            if f.digest in urls
        ],
        line=1,
        audience=passage.audience,
        status=status,
        source="import",
        origin={
            "pipeline": PIPELINE,
            "kind": passage.kind,
            "file": title,
            "pages": passage.pages,
            "examples": passage.examples,
            **passage.extra,
        },
        doc_key=doc_key,
    )


async def save_passages(
    rt: AppRuntime, passages: list[Passage], title: str, status: str = "active"
) -> dict[str, Any]:
    doc_key = document_key(title)
    urls = await _store_images(rt, passages, title)
    async with rt.db.session_factory() as db:
        old = list(
            (
                await db.execute(
                    select(KBArticle.id).where(KBArticle.doc_key == doc_key, KBArticle.status != "archived")
                )
            ).scalars()
        )
        if old:
            await db.execute(update(KBArticle).where(KBArticle.id.in_(old)).values(status="archived"))
        rows = [_article(p, urls, doc_key, title, status) for p in passages]
        db.add_all(rows)
        ids = [row.id for row in rows]
        await db.commit()
    return {"created": len(ids), "replaced": len(old), "images": len(urls), "ids": ids, "old_ids": old}


async def index_ids(rt: AppRuntime, article_ids: list[str]) -> int:
    if not article_ids:
        return 0
    async with rt.db.session_factory() as db:
        articles = list(
            (
                await db.execute(
                    select(KBArticle).where(KBArticle.id.in_(article_ids), KBArticle.status == "active")
                )
            ).scalars()
        )
    docs = await vector_docs(rt.embedder, articles)
    await rt.store.ensure_ready(rt.embedder.dim)
    await rt.store.upsert(docs)
    return len(articles)


@dataclass(frozen=True)
class IngestOptions:
    """Как загружать: название документа (по умолчанию — имя файла), статус статей,
    индексировать ли сразу (сборка базы индексирует всё одним проходом в конце) и прогресс."""

    title: str = ""
    status: str = "active"
    index: bool = True
    on_progress: ProgressCallback | None = None


async def ingest_bytes(
    rt: AppRuntime, filename: str, data: bytes, options: IngestOptions | None = None
) -> dict[str, Any]:
    options = options or IngestOptions()
    progress = options.on_progress
    title = options.title or doc_title(filename)
    passages = await asyncio.to_thread(parse_file, filename, data)
    if not passages:
        return {
            "created": 0,
            "skipped": 0,
            "error": (
                f"В файле «{filename}» не найдено ни одного фрагмента знаний: "
                "формат не распознан или текста в нём нет"
            ),
        }
    await attach(rt.dyn, passages)
    if progress:
        await progress(1, 3)
    result = await save_passages(rt, passages, title, options.status)
    if progress:
        await progress(2, 3)
    if options.index:
        await rt.store.delete(result["old_ids"])
        if options.status == "active":
            await index_ids(rt, result["ids"])
        await rt.retriever.refresh_from_db(rt.db.session_factory)
        await rt.kb.notify_changed()
    if progress:
        await progress(3, 3)
    return {
        "created": result["created"],
        "replaced": result["replaced"],
        "images": result["images"],
        "skipped": 0,
        "title": title,
    }
