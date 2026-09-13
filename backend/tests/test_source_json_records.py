"""Источник json_records: синонимы ключей, массив в глубине, JSONL.

Проверяем чистыми функциями и самим источником — поднимать приложение незачем,
разбор файла ни от базы, ни от модели не зависит.
"""

import json

import pytest

from app.ingest.blob import FileBlob
from app.ingest.registry import IngestContext
from app.ingest.sources.json_records import JsonRecordsSource, map_fields


def _blob(name: str, payload) -> FileBlob:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return FileBlob(name=name, data=text.encode("utf-8"))


async def _records(blob: FileBlob, ctx: IngestContext | None = None) -> list:
    source = JsonRecordsSource()
    return [record async for record in source.parse(blob, ctx or IngestContext())]


def test_fields_are_mapped_by_synonyms():
    assert map_fields({"q": "Как?", "a": "Так."}) == ("Как?", "Так.")
    assert map_fields({"title": "Как?", "body": "Так."}) == ("Как?", "Так.")
    assert map_fields({"текст_обращения": "Как?", "решение": "Так."}) == ("Как?", "Так.")
    # Строка справочника организаций — не пара «вопрос — ответ», брать её нельзя
    assert map_fields({"inn": "7701", "kpp": "770101"}) is None


def test_key_case_and_container_values_are_handled():
    assert map_fields({"ВОПРОС": " Как? ", "Ответ": "Так."}) == ("Как?", "Так.")
    # Под "text" приезжает служебная структура — это не ответ, а внутренности выгрузки
    assert map_fields({"question": "Как?", "text": {"blocks": []}}) is None
    assert map_fields({"question": "Как?", "answer": ""}) is None


def test_nan_never_becomes_an_answer():
    """NaN и Infinity JSON пишет литералами, а Python их разбирает молча.

    Пустая ячейка выгрузки не должна превращаться в ответ «nan»: провенанс ставит
    authored=True, и такой текст бот отдал бы пользователю дословно.
    """
    payload = '[{"question": "Как?", "answer": NaN}, {"question": "Где?", "answer": Infinity}]'
    records = json.loads(payload)
    assert map_fields(records[0]) is None
    assert map_fields(records[1]) is None


def test_answer_split_into_paragraphs_is_kept():
    # Список строк — это разбитый на шаги ответ, а не служебная структура
    assert map_fields({"q": "Как?", "a": ["Шаг 1.", "Шаг 2."]}) == ("Как?", "Шаг 1.\nШаг 2.")
    # А список объектов — внутренности выгрузки, из них ответ не собрать
    assert map_fields({"q": "Как?", "a": [{"block": 1}]}) is None


@pytest.mark.asyncio
async def test_nested_array_under_items():
    blob = _blob(
        "kb.json",
        {
            "items": [
                {"question": "Как зарегистрироваться?", "answer": "Нужна ЭП."},
                {"question": "Как подписать контракт?", "answer": "В карточке."},
            ]
        },
    )
    source = JsonRecordsSource()
    assert await source.sniff(blob) > 0.7

    records = await _records(blob)
    assert [r.question for r in records] == ["Как зарегистрироваться?", "Как подписать контракт?"]
    assert records[0].origin.locator == "$.items[0]"
    assert records[1].origin.locator == "$.items[1]"
    assert records[0].origin.source_name == "json_records"
    assert records[0].origin.file == "kb.json"
    # Пара написана человеком — такой ответ можно отдавать дословно
    assert records[0].origin.authored is True


@pytest.mark.asyncio
async def test_array_found_deeper_in_wrapper():
    blob = _blob("export.json", {"data": {"faq": [{"вопрос": "Как?", "ответ": "Так."}]}})
    records = await _records(blob)
    assert records[0].origin.locator == "$.data.faq[0]"


@pytest.mark.asyncio
async def test_jsonl_one_object_per_line():
    lines = "\n".join(
        json.dumps(item, ensure_ascii=False)
        for item in [
            {"q": "Где заявки?", "a": "В личном кабинете."},
            {"q": "Как продлить?", "a": "Кнопкой в карточке."},
        ]
    )
    blob = _blob("kb.jsonl", lines)
    source = JsonRecordsSource()
    assert await source.sniff(blob) > 0.7

    records = await _records(blob)
    assert [r.answer for r in records] == ["В личном кабинете.", "Кнопкой в карточке."]
    assert records[1].origin.locator == "$[1]"


@pytest.mark.asyncio
async def test_service_rows_do_not_shift_locator():
    """Пропущенная служебная строка не должна сдвигать номера у остальных."""
    blob = _blob(
        "kb.json",
        {
            "items": [
                {"inn": "7701", "kpp": "770101"},
                {"question": "Как?", "answer": "Так."},
            ]
        },
    )
    records = await _records(blob)
    assert len(records) == 1
    assert records[0].origin.locator == "$.items[1]"


@pytest.mark.asyncio
async def test_meta_from_item_wins_over_params():
    blob = _blob(
        "kb.json",
        [
            {"q": "Как?", "a": "Так.", "category": "Контракты", "line": "2", "audience": "supplier"},
            {"q": "Куда?", "a": "Туда."},
        ],
    )
    ctx = IngestContext(params={"category": "Общее", "line": 1, "audience": "all"})
    records = await _records(blob, ctx)
    assert (records[0].category, records[0].line, records[0].audience) == ("Контракты", 2, "supplier")
    assert (records[1].category, records[1].line, records[1].audience) == ("Общее", 1, "all")


@pytest.mark.asyncio
async def test_leading_service_rows_do_not_hide_the_array():
    """Блок служебных строк в начале выгрузки не должен отправлять весь файл в отказ."""
    payload = [{"inn": str(i)} for i in range(30)] + [{"q": "Как?", "a": "Так."}]
    blob = _blob("kb.json", payload)
    assert await JsonRecordsSource().sniff(blob) > 0.7
    records = await _records(blob)
    assert [r.origin.locator for r in records] == ["$[30]"]


@pytest.mark.asyncio
async def test_sniff_refuses_alien_and_broken_files():
    source = JsonRecordsSource()
    # Не наш суффикс — за файл не беремся, даже если внутри лежит подходящий JSON
    assert await source.sniff(_blob("kb.csv", [{"q": "Как?", "a": "Так."}])) == 0.0
    assert await source.sniff(_blob("kb.json", "{не json,,,")) == 0.0
    assert await source.sniff(_blob("kb.json", "")) == 0.0
    # JSON разобрался, но пар в нем нет — это чужая выгрузка
    assert await source.sniff(_blob("orgs.json", {"orgs": [{"inn": "7701"}]})) == 0.0
