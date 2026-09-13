"""Источник spreadsheet: книга Excel разбирается полистно.

Главное свойство, которое проверяем: два разных листа одной книги дают два разных
типа записей и не смешиваются, а справочник — не даёт ничего. Всё проверяется
чистыми функциями, без подъёма приложения: источник не ходит ни в базу, ни в LLM.
"""

import io

import pandas as pd
import pytest

from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord, FeedbackRecord
from app.ingest.registry import IngestContext
from app.ingest.sources.spreadsheet import SpreadsheetSource, classify_sheet


def _book(sheets: dict[str, pd.DataFrame]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    return buffer.getvalue()


def _mixed_book() -> bytes:
    """Книга, как её выдают организаторы: вопросы, отзывы и справочник рядом."""
    return _book(
        {
            "Вопросы": pd.DataFrame(
                {
                    "Вопрос": [
                        "Как зарегистрироваться?",
                        "Как подписать контракт?",
                        "Где найти УПД?",
                        "Как сменить реквизиты?",
                        "Куда жаловаться?",
                    ],
                    "Ответ": [
                        "Нужна ЭП.",
                        "В карточке контракта.",
                        "В разделе исполнения.",
                        "В профиле организации.",
                        "В ФАС.",
                    ],
                }
            ),
            "Отзывы": pd.DataFrame(
                {
                    "Оценка": [5, 2, 4],
                    "Комментарий": ["Быстро помогли", "Долго ждал ответа", "Всё понятно"],
                    "Специалист": ["Мария", "Иван", "Мария"],
                }
            ),
        }
    )


async def _parse(data: bytes, ctx: IngestContext | None = None, name: str = "dump.xlsx"):
    source = SpreadsheetSource()
    blob = FileBlob(name=name, data=data)
    return [record async for record in source.parse(blob, ctx or IngestContext())]


def test_sheet_kind_is_recognised():
    assert classify_sheet(["Вопрос", "Ответ"], [["Как?", "Так."]]) == "qa"
    assert classify_sheet(["Оценка", "Комментарий"], [["5", "Спасибо"]]) == "feedback"
    # Справочник организаций: detect_columns и на нём назовёт две разные колонки,
    # поэтому лист обязан отсеиваться до разбора, а не после.
    assert classify_sheet(["ИНН", "КПП"], [["7701", "770101"]]) == "unknown"


@pytest.mark.asyncio
async def test_two_sheets_give_two_kinds_of_records():
    source = SpreadsheetSource()
    blob = FileBlob(name="dump.xlsx", data=_mixed_book())
    assert await source.sniff(blob) > 0.7

    records = await _parse(blob.data)
    articles = [r for r in records if isinstance(r, ArticleRecord)]
    feedback = [r for r in records if isinstance(r, FeedbackRecord)]
    assert len(articles) == 5
    assert len(feedback) == 3

    assert articles[0].question == "Как зарегистрироваться?"
    assert articles[0].origin.locator == "лист «Вопросы», строка 1"
    assert articles[0].origin.source_name == "spreadsheet"
    # Пара «вопрос — ответ» написана человеком: её можно отдавать дословно.
    assert articles[0].origin.authored is True
    # Категории в книге нет — сходит имя листа, иначе статьи потеряют тему.
    assert articles[0].category == "Вопросы"

    assert feedback[1].rating == 2
    assert feedback[1].comment == "Долго ждал ответа"
    assert feedback[1].operator == "Иван"
    assert feedback[1].origin.locator == "лист «Отзывы», строка 2"


@pytest.mark.asyncio
async def test_category_from_params_wins_over_sheet_name():
    records = await _parse(_mixed_book(), IngestContext(params={"category": "Регистрация"}))
    articles = [r for r in records if isinstance(r, ArticleRecord)]
    assert {r.category for r in articles} == {"Регистрация"}


@pytest.mark.asyncio
async def test_broken_rows_and_reference_sheet_are_skipped():
    data = _book(
        {
            "Вопросы": pd.DataFrame(
                {
                    "Вопрос": ["Как войти?", "Как выйти?", "", "Где акты?", "Как вернуть товар?"],
                    "Ответ": ["Через ЭП.", "", "Ответ без вопроса", "В исполнении.", "Через заявку."],
                }
            ),
            "Отзывы": pd.DataFrame(
                {
                    "Оценка": ["5", "отлично", "3"],
                    "Комментарий": ["Спасибо", "Ничего не понял", "Сойдёт"],
                }
            ),
            "Организации": pd.DataFrame(
                {
                    "ИНН": ["7701000001", "7702000002"],
                    "КПП": ["770101001", "770201001"],
                }
            ),
        }
    )
    records = await _parse(data)
    articles = [r for r in records if isinstance(r, ArticleRecord)]
    feedback = [r for r in records if isinstance(r, FeedbackRecord)]

    # Полупустая строка — это не половина статьи, а мусор в выдаче.
    assert [r.question for r in articles] == ["Как войти?", "Где акты?", "Как вернуть товар?"]
    # Номера строк остаются номерами листа: по локатору статью надо найти глазами.
    assert [r.origin.locator for r in articles] == [
        "лист «Вопросы», строка 1",
        "лист «Вопросы», строка 4",
        "лист «Вопросы», строка 5",
    ]
    # Нечисловая оценка — не ноль и не единица, такую строку считать нельзя.
    assert [r.rating for r in feedback] == [5, 3]
    # Колонки специалиста в книге нет: отзыв всё равно ценен, просто без адресата.
    assert {r.operator for r in feedback} == {""}
    assert not [r for r in records if "Организации" in r.origin.locator]


@pytest.mark.asyncio
async def test_single_row_sheet_is_not_lost():
    """Лист с одной строкой — это тоже данные, а не шум.

    Выгрузка редкого раздела приезжает одной парой «вопрос — ответ», и порог
    «минимум две строки» выбросил бы её молча: от справочника лист защищает
    улика в classify_sheet, а не количество строк.
    """
    data = _book(
        {
            "Вопросы": pd.DataFrame({"Вопрос": ["Как войти?"], "Ответ": ["Через ЭП."]}),
            "Отзывы": pd.DataFrame({"Оценка": [5], "Комментарий": ["Спасибо"]}),
            "Организации": pd.DataFrame({"ИНН": ["7701000001"], "КПП": ["770101001"]}),
        }
    )
    records = await _parse(data)
    assert [r.question for r in records if isinstance(r, ArticleRecord)] == ["Как войти?"]
    assert [r.rating for r in records if isinstance(r, FeedbackRecord)] == [5]
    assert not [r for r in records if "Организации" in r.origin.locator]


@pytest.mark.asyncio
async def test_broken_rating_does_not_take_the_whole_book_down():
    """Одна сломанная ячейка стоит одной строки, а не всей книги.

    `int(float("inf"))` бросает OverflowError мимо ValueError, а исключение из
    генератора реестр считает отказом источника: из-за одной ячейки книга ушла
    бы целиком в запасной разбор моделью.
    """
    data = _book(
        {
            "Отзывы": pd.DataFrame(
                {
                    "Оценка": ["inf", "4", "1e400"],
                    "Комментарий": ["сломанная выгрузка", "нормально", "тоже бесконечность"],
                }
            ),
        }
    )
    records = await _parse(data)
    assert [r.rating for r in records] == [4]


@pytest.mark.asyncio
async def test_sniff_takes_only_readable_books():
    source = SpreadsheetSource()
    assert await source.sniff(FileBlob(name="qa.xlsx", data=_mixed_book())) == 0.9
    assert await source.sniff(FileBlob(name="qa.md", data=b"| a | b |")) == 0.0
    # Битую книгу пусть пробует кто-то другой: падать на распознавании нельзя,
    # реестр спрашивает sniff у всех источников подряд.
    assert await source.sniff(FileBlob(name="qa.xlsx", data=b"not an excel at all")) == 0.0
