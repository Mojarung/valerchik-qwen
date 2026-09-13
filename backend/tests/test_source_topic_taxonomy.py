"""Источник topic_taxonomy: справочник тем и подтем — справочник, а не ответы.

Пока подтема лежала статьёй с ответом-названием темы, на «разблокируйте кабинет»
бот находил «Блокировка личного кабинета» с высокой уверенностью и отвечал
названием темы дословно. Теперь каждая запись справочника помечена видом
REFERENCE, и поиск ответов её не видит.
"""

import io

import openpyxl
import pytest

from app.ingest.blob import FileBlob
from app.ingest.registry import IngestContext
from app.ingest.sources.topic_taxonomy import CONFIDENCE, TopicTaxonomySource
from app.services import kbkind


def _book(rows: list[list[str | None]]) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Темы"
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# Так справочник выглядит в файле: тема записана один раз, ниже — пустые ячейки
TAXONOMY = _book(
    [
        ["Тема обращений", "Подтема обращений"],
        ["Работа с личным кабинетом", "Блокировка личного кабинета"],
        [None, "Изменение данных пользователя"],
        ["Работа с СТЕ", "Ошибка в карточке СТЕ"],
    ]
)


@pytest.mark.asyncio
async def test_sniff_takes_only_the_taxonomy():
    source = TopicTaxonomySource()
    assert await source.sniff(FileBlob(name="Темы_подтемы.xlsx", data=TAXONOMY)) == CONFIDENCE
    qa = _book([["Вопрос", "Ответ"], ["Как войти?", "Через ЭП."]])
    assert await source.sniff(FileBlob(name="qa.xlsx", data=qa)) == 0.0


@pytest.mark.asyncio
async def test_every_pair_becomes_a_reference_record():
    source = TopicTaxonomySource()
    blob = FileBlob(name="Темы_подтемы.xlsx", data=TAXONOMY)
    records = [record async for record in source.parse(blob, IngestContext(params={"audience": "supplier"}))]

    assert [(r.question, r.category) for r in records] == [
        ("Блокировка личного кабинета", "Работа с личным кабинетом"),
        # Пустая ячейка темы — та же тема, что строкой выше
        ("Изменение данных пользователя", "Работа с личным кабинетом"),
        ("Ошибка в карточке СТЕ", "Работа с СТЕ"),
    ]
    # Справочник, а не ответ: отвечать названием темы поиск не станет
    assert {r.origin.kind for r in records} == {kbkind.REFERENCE}
    assert {r.origin.as_dict()["kind"] for r in records} == {kbkind.REFERENCE}
    assert {r.audience for r in records} == {"supplier"}
    assert records[0].origin.locator == "лист «Темы», строка 2"
