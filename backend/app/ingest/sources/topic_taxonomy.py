"""Справочник тем и подтем обращений.

Это не таблица FAQ: в ней нет готовых ответов, только иерархия классификации.
Каждую исходную пару сохраняем без переформулирования: подтема становится
текстом записи, тема — её категорией. Записи — справочник (kbkind.REFERENCE),
а не ответы: пока подтема лежала статьёй с ответом-названием темы, на
«разблокируйте кабинет» бот находил её с высокой уверенностью и отвечал
«Блокировка личного кабинета» — дословно, как авторский текст. Импорт не
сочиняет отсутствующие в файле инструкции, и поиск не выдаёт их за ответ.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.ingest.blob import SHEET_SUFFIXES, FileBlob
from app.ingest.records import ArticleRecord, Provenance
from app.ingest.registry import IngestContext, register
from app.services import kbkind

CONFIDENCE = 0.96
TOPIC_HEADER = "тема обращений"
SUBTOPIC_HEADER = "подтема обращений"


def _text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() == "nan" else text


def _header_index(row: list[str], name: str) -> int:
    target = name.casefold()
    return next((index for index, value in enumerate(row) if target in value.casefold()), -1)


@dataclass(frozen=True)
class _Layout:
    header_row: int
    topic: int
    subtopic: int


def _layout(headers: list[str], rows: list[list[str]]) -> _Layout | None:
    for row_number, row in enumerate([headers, *rows]):
        topic = _header_index(row, TOPIC_HEADER)
        subtopic = _header_index(row, SUBTOPIC_HEADER)
        if topic >= 0 and subtopic >= 0 and topic != subtopic:
            return _Layout(row_number, topic, subtopic)
    return None


@register
class TopicTaxonomySource:
    name = "topic_taxonomy"
    priority = 25

    async def sniff(self, blob: FileBlob) -> float:
        if blob.suffix not in SHEET_SUFFIXES:
            return 0.0
        try:
            sheets = blob.tables
        except Exception:  # noqa: BLE001 — повреждённую книгу попробует другой источник
            return 0.0
        for _, frame in sheets:
            headers = [_text(column) for column in frame.columns]
            rows = [[_text(cell) for cell in row] for row in frame.values.tolist()]
            if _layout(headers, rows) is not None:
                return CONFIDENCE
        return 0.0

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[ArticleRecord]:
        for sheet_name, frame in blob.tables:
            headers = [_text(column) for column in frame.columns]
            rows = [[_text(cell) for cell in row] for row in frame.values.tolist()]
            layout = _layout(headers, rows)
            if layout is None:
                continue
            topic = ""
            # header_row=0 означает заголовки DataFrame; данные начинаются с rows[0].
            start = max(0, layout.header_row)
            for row_index, row in enumerate(rows[start:], start=start + 2):
                row_topic = row[layout.topic] if layout.topic < len(row) else ""
                subtopic = row[layout.subtopic] if layout.subtopic < len(row) else ""
                if row_topic:
                    topic = row_topic
                if not topic or not subtopic:
                    continue
                yield ArticleRecord(
                    question=subtopic,
                    answer=topic,
                    category=topic,
                    audience=ctx.params.get("audience") or "all",
                    keywords=[topic, subtopic],
                    origin=Provenance(
                        file=blob.name,
                        locator=f"лист «{sheet_name}», строка {row_index}",
                        source_name=self.name,
                        authored=True,
                        kind=kbkind.REFERENCE,
                    ),
                )
