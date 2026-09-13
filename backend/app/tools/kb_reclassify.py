"""Починка уже импортированной базы: мусор — не ответ, глоссарий — отвечает.

    uv run python -m app.tools.kb_reclassify [--apply] [--index] [--database-url URL]

Правила при импорте (ingest.chunking.answerless_heading, ingest.tables.glossary_terms)
чинят будущие загрузки, но в базе уже лежит то, что импортировано по старым:

- статьи-подписи «Рисунок 82 - Экспорт оферт» и оглавления «СОДЕРЖАНИЕ»: ответа
  в них нет, а слот кандидата и место в топ-5 они занимают наравне с инструкцией
  — им проставляется `origin['kind'] = 'reference'`;
- глоссарий «Таблица 1 - Перечень терминов и сокращений» одной статьёй, где все
  определения лежат в теле: «что такое УПД» не находит его, потому что 75 %
  лексической уверенности даёт совпадение с заголовком статьи. Каждая строка
  таблицы становится отдельной статьёй «Что означает УПД?», а сама таблица —
  справочником. Статье-определению прописываются формулировки (сам термин,
  «что такое УПД», «расшифровка УПД») — по ним поиск узнаёт короткий запрос
  «упд», которым ищет ReAct-агент; заведённым прошлым прогоном они дописываются.

Без `--apply` ничего не пишет: печатает, сколько строк и каких затронет. Смена
вида перевекторизации не требует (вид поиск читает из БД, Retriever._payloads),
а вот новым статьям-терминам вектор нужен — за ним `--index`, он поднимает
рантайм и индексирует созданное, как это делает админка.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import Counter
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db import Database
from app.ingest.chunking import answerless_heading
from app.ingest.tables import glossary_terms
from app.models import KBArticle
from app.services import kbkind, phrasings

log = logging.getLogger(__name__)

# Сколько примеров показать в отчёте
MAX_SAMPLES = 10
ACTOR = "kb_reclassify"


def _as_reference(article: KBArticle) -> None:
    # origin — JSON-колонка: новый словарь, иначе SQLAlchemy не заметит правку
    # на месте и коммит уйдёт пустым.
    article.origin = {**(article.origin or {}), "kind": kbkind.REFERENCE}


async def reclassify(database_url: str, apply: bool) -> dict[str, Any]:
    """Подписи и оглавления → reference; глоссарий → статья на термин.

    Возвращает отчёт: сколько статей просмотрено, сколько помечено, сколько
    определений заведено и какие. С apply=False только считает.
    """
    db = Database(database_url)
    try:
        await db.create_all()
        async with db.session_factory() as session:
            articles = (await session.scalars(select(KBArticle))).all()
            known = {(a.question or "").strip().casefold() for a in articles}
            junk = [
                a
                for a in articles
                if answerless_heading(a.question or "") and kbkind.of(a.origin) == kbkind.ANSWER
            ]
            glossaries = [(a, glossary_terms(a.question or "", a.answer or "")) for a in articles]
            glossaries = [(a, terms) for a, terms in glossaries if terms]
            by_question = {(a.question or "").strip().casefold(): a for a in articles}

            created: list[str] = []
            enriched: list[str] = []
            for article, terms in glossaries:
                for term in terms:
                    key = term.question.strip().casefold()
                    existing = by_question.get(key)
                    if key in known:
                        # Термин уже заведён прошлым прогоном: дописываем ему
                        # формулировки, если их тогда не было, — по ним поиск и
                        # узнаёт голое «упд» в вопросе человека.
                        if existing is not None and not phrasings.of(existing.origin):
                            enriched.append(term.question)
                            if apply:
                                existing.origin = {
                                    **(existing.origin or {}),
                                    phrasings.ORIGIN_KEY: term.phrasings,
                                }
                        continue
                    known.add(key)
                    created.append(term.question)
                    if apply:
                        session.add(
                            KBArticle(
                                question=term.question,
                                answer=term.answer,
                                category=article.category,
                                line=article.line,
                                audience=article.audience,
                                status=article.status,
                                source="manual",
                                origin={
                                    "source": ACTOR,
                                    "file": (article.origin or {}).get("file", ""),
                                    phrasings.ORIGIN_KEY: term.phrasings,
                                },
                            )
                        )
            if apply:
                for article in junk:
                    _as_reference(article)
                for article, _pairs in glossaries:
                    # Разобранная таблица больше не ответ: определения теперь
                    # лежат в отдельных статьях и не соперничают с ней.
                    _as_reference(article)
                await session.commit()
        return {
            "articles": len(articles),
            "junk": len(junk),
            "junk_by_status": dict(Counter(a.status or "" for a in junk)),
            "glossaries": len(glossaries),
            "terms": len(created),
            "enriched": len(enriched),
            "applied": apply,
            "junk_samples": [a.question for a in junk[:MAX_SAMPLES]],
            # Полным списком, а не примерами: по нему индексируются новые статьи
            "term_questions": created + enriched,
        }
    finally:
        await db.dispose()


async def index_new_terms(questions: list[str]) -> int:
    """Векторы для заведённых определений — как это делает админка при создании."""
    from app.runtime import AppRuntime

    rt = AppRuntime(get_settings())
    await rt.start()
    try:
        async with rt.db.session_factory() as session:
            ids = list(
                (await session.scalars(select(KBArticle.id).where(KBArticle.question.in_(questions)))).all()
            )
        return await rt.kb.index_many(ids)
    finally:
        await rt.stop()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="записать изменения (без флага — сухой прогон)")
    parser.add_argument("--index", action="store_true", help="с --apply: проиндексировать новые определения")
    parser.add_argument("--database-url", default="", help="пусто — база из настроек приложения")
    args = parser.parse_args(argv)

    url = args.database_url or get_settings().effective_database_url
    report = asyncio.run(reclassify(url, args.apply))
    if args.apply and args.index and report["term_questions"]:
        report["indexed"] = asyncio.run(index_new_terms(list(report["term_questions"])))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
