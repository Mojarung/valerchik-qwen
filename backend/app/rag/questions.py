"""Вопросы пользователей к фрагментам инструкций (doc2query).

Человек пишет вопрос, а фрагмент — это кусок методички: тексты разного рода.
При импорте модель один раз пишет к каждому фрагменту вопросы, какими их задают
в чате поддержки; они индексируются рядом с фрагментом (app/rag/index.py) и
попадают в лексический индекс. В ответе это не стоит ничего — только в импорте.

Дозаполнить уже загруженную базу: `python -m app.rag.questions [--reindex]`.
"""

import argparse
import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModelSettings
from sqlalchemy import select

from app.models import KBArticle
from app.rag.passages import KIND_FAQ, KIND_SECTION, PIPELINE, Passage
from app.services.agent import build_model, llm_configured

if TYPE_CHECKING:
    from app.dynamic_settings import DynamicSettings
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

KINDS = frozenset({KIND_SECTION, KIND_FAQ})
QUESTIONS = 4
CONCURRENCY = 8
TIMEOUT_S = 90
SOURCE_CHARS = 2500
MIN_QUESTION_CHARS = 8
MAX_QUESTION_CHARS = 160
BATCH = 50

PROMPT = f"""Ты помогаешь построить поиск по базе знаний Портала поставщиков Москвы (zakupki.mos.ru).
По фрагменту инструкции напиши {QUESTIONS} разных вопроса, которые пользователь портала мог бы задать \
в чате поддержки и на которые этот фрагмент отвечает.
Пиши как живые люди: коротко, разговорно и по-разному (как сделать, где найти, почему не получается, \
что это значит, какой срок). Можно с сокращениями (УПД, СТЕ, КС, ЭП, МЧД, ЭДО).
Не упоминай «фрагмент», «инструкцию» и номера разделов. Выведи только вопросы, по одному в строке."""

_LEADING = re.compile(r"^\s*(?:\d{1,2}[.)]|[-•*—])\s*")


def _parse(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        question = _LEADING.sub("", line).strip().strip("«»\"'")
        if MIN_QUESTION_CHARS <= len(question) <= MAX_QUESTION_CHARS and question not in found:
            found.append(question)
    return found[:QUESTIONS]


async def generate(dyn: DynamicSettings, sources: list[tuple[str, str]]) -> list[list[str]]:
    """Вопросы к каждому (заголовок с разделом, текст). Без модели — пустые списки."""
    if not sources or not llm_configured(dyn):
        return [[] for _ in sources]
    agent: Agent[None, str] = Agent(
        build_model(dyn),
        instructions=PROMPT,
        model_settings=OpenAIChatModelSettings(openai_reasoning_effort="low", max_tokens=600),
    )
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(heading: str, text: str) -> list[str]:
        async with gate:
            try:
                result = await asyncio.wait_for(agent.run(f"{heading}\n\n{text[:SOURCE_CHARS]}"), TIMEOUT_S)
            except Exception:  # noqa: BLE001 — без вопросов фрагмент всё равно ищется по тексту
                log.warning("Вопросы к фрагменту «%s» не получены", heading[:80])
                return []
            return _parse(result.output or "")

    return list(await asyncio.gather(*(one(heading, text) for heading, text in sources)))


async def attach(dyn: DynamicSettings, passages: list[Passage]) -> int:
    """Вопросы к фрагментам разделов и FAQ импортируемого документа — в passage.extra."""
    targets = [p for p in passages if p.kind in KINDS]
    found = await generate(dyn, [(p.category, p.text) for p in targets])
    for passage, questions in zip(targets, found, strict=True):
        if questions:
            passage.extra["questions"] = questions
    return sum(1 for questions in found if questions)


async def fill(rt: AppRuntime) -> list[str]:
    """Дописать вопросы активным фрагментам, у которых их ещё нет. Возвращает id дописанных."""
    async with rt.db.session_factory() as db:
        rows = list((await db.execute(select(KBArticle).where(KBArticle.status == "active"))).scalars())
        todo = [
            r
            for r in rows
            if (r.origin or {}).get("pipeline") == PIPELINE
            and (r.origin or {}).get("kind") in KINDS
            and not (r.origin or {}).get("questions")
        ]
        done: list[str] = []
        for start in range(0, len(todo), BATCH):
            batch = todo[start : start + BATCH]
            found = await generate(rt.dyn, [(r.category, r.answer) for r in batch])
            for row, questions in zip(batch, found, strict=True):
                if questions:
                    row.origin = {**(row.origin or {}), "questions": questions}
                    done.append(row.id)
            await db.commit()
            log.info("Вопросы: %d / %d фрагментов", start + len(batch), len(todo))
    return done


async def main(reindex: bool) -> None:
    from app.config import get_settings
    from app.runtime import AppRuntime

    rt = AppRuntime(get_settings())
    await rt.db.create_all()
    await rt.dyn.load()
    from app.rag.ingest import index_ids

    started = time.perf_counter()
    ids = await fill(rt)
    log.info("Фрагментов с новыми вопросами: %d за %.0f с", len(ids), time.perf_counter() - started)
    if reindex and ids:
        started = time.perf_counter()
        log.info("Переиндексировано: %d за %.0f с", await index_ids(rt, ids), time.perf_counter() - started)
        await rt.kb.notify_changed()
    await rt.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reindex", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main(args.reindex))
