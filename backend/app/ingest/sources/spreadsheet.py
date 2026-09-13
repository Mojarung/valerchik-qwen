"""Excel: каждый лист книги разбирается отдельно и по-своему.

Склеивать листы нельзя: в одной книге приезжают «Вопросы», «Отзывы» и справочник
организаций. Общая простыня превращает три набора данных в кашу, из которой не
собрать ни один: у отзыва нет вопроса, у справочника нет ответа, а колонки не
совпадают даже по количеству. Поэтому вид определяется у каждого листа свой, и
лист, который не опознан, пропускается целиком — лучше потерять справочник, чем
завести статьи «7701 — 770101».
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

from app.ingest.blob import SHEET_SUFFIXES, FileBlob
from app.ingest.records import ArticleRecord, FeedbackRecord, Provenance, Record
from app.ingest.registry import IngestContext, register
from app.ingest.tables import (
    _ANSWER_COLUMNS,
    _QUESTION_COLUMNS,
    MAX_QUESTION_CHARS,
    QUESTION_MARK_SHARE,
    detect_columns,
    detect_line_column,
    parse_line,
)

RATING_COLUMNS = ("оценка", "рейтинг", "балл", "звёзд", "звезд", "rating", "score")
COMMENT_COLUMNS = ("комментарий", "отзыв", "текст", "comment", "review", "feedback")
OPERATOR_COLUMNS = ("специалист", "оператор", "сотрудник", "operator", "agent")
# Сколько полных пар «вопрос — ответ» должно быть на листе, чтобы считать его
# выгрузкой статей. Порог по КОЛИЧЕСТВУ, а не по доле: доля наказывала лист за
# чужие пустые строки, и лист с сотней готовых пар уходил в «непонятно» целиком,
# если рядом лежали двести закрытых без ответа. Единицы достаточно: от
# справочника ИНН/КПП лист защищает проверка колонок выше, а не это число.
MIN_FILLED_PAIRS = 1

log = logging.getLogger(__name__)


def _has(headers: list[str], names: tuple[str, ...]) -> int:
    """Индекс первой колонки, чьё имя содержит одно из слов, иначе -1."""
    for index, header in enumerate(headers):
        low = header.strip().lower()
        if any(name in low for name in names):
            return index
    return -1


def _asks_questions(rows: list[list[str]], index: int) -> bool:
    """В колонке действительно спрашивают, а не перечисляют коды и названия."""
    values = [row[index].strip() for row in rows if index < len(row) and row[index].strip()]
    if not values:
        return False
    asked = sum(1 for value in values if value.endswith("?") and len(value) <= MAX_QUESTION_CHARS)
    return asked / len(values) >= QUESTION_MARK_SHARE


def classify_sheet(headers: list[str], rows: list[list[str]]) -> str:
    """Что за лист: «qa», «feedback» или «unknown» (пропустить).

    Отзыв узнаётся по паре колонок «оценка + комментарий»: оценка без текста —
    это статистика, текст без оценки — это переписка, и ни то ни другое в
    FeedbackRecord не ложится.

    С вопросами сложнее. `detect_columns` называет пару колонок всегда — он
    написан для таблицы, про которую уже решили, что она выгрузка статей, и на
    справочнике ИНН/КПП честно ответит «вопрос — нулевая, ответ — первая».
    Решение принимаем здесь, поэтому нужна своя улика: либо колонки названы как
    вопрос или ответ, либо в колонке вопроса и правда спрашивают.
    """
    if _has(headers, RATING_COLUMNS) >= 0 and _has(headers, COMMENT_COLUMNS) >= 0:
        return "feedback"
    if not rows:
        return "unknown"
    question_at, answer_at = detect_columns(headers, rows)
    if question_at == answer_at:
        return "unknown"
    named = _has(headers, _QUESTION_COLUMNS) >= 0 or _has(headers, _ANSWER_COLUMNS) >= 0
    if not named and not _asks_questions(rows, question_at):
        return "unknown"
    # Доля заполненных ответов решала судьбу ЛИСТА целиком, и это неверная
    # единица измерения: в реальной выгрузке обращений строк без ответа легко
    # больше половины (закрыто без ответа, дубль, спам), и лист с сотней готовых
    # пар уходил в «непонятно» весь, молча. Пустые строки отбрасывает разбор
    # построчно и считает их в лог; здесь достаточно убедиться, что заполненные
    # пары вообще есть.
    filled = sum(1 for row in rows if answer_at < len(row) and row[answer_at].strip())
    return "qa" if filled >= MIN_FILLED_PAIRS else "unknown"


@dataclass(frozen=True)
class _Sheet:
    """Лист книги: имя, заголовки и строки текстом, файл — для локатора записи."""

    name: str
    headers: list[str]
    rows: list[list[str]]
    file: str

    @classmethod
    def read(cls, name: str, frame: Any, file: str) -> _Sheet:
        return cls(
            name=name,
            headers=[str(column) for column in frame.columns],
            rows=[[str(cell) for cell in row] for row in frame.values.tolist()],
            file=file,
        )

    @property
    def place(self) -> str:
        """Номер строки — внутри листа: у книги сквозной нумерации нет, а
        проверять локатор человек будет, открыв именно этот лист."""
        return f"лист «{self.name}»"


@register
class SpreadsheetSource:
    name = "spreadsheet"
    priority = 20

    async def sniff(self, blob: FileBlob) -> float:
        if blob.suffix not in SHEET_SUFFIXES:
            return 0.0
        try:
            sheets = blob.tables
        except Exception:  # noqa: BLE001 — битая книга: пусть пробует markitdown
            return 0.0
        return 0.9 if sheets else 0.0

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[Record]:
        for sheet_name, frame in blob.tables:
            sheet = _Sheet.read(sheet_name, frame, blob.name)
            # Отсекаем только лист без строк. Порог «хотя бы две» стоил бы нам
            # настоящих данных: лист с одной парой «вопрос — ответ» встречается в
            # выгрузках, а от справочника его защищает не количество строк, а
            # улика в classify_sheet — имя колонки или вопросительный знак.
            if not sheet.rows:
                continue
            kind = classify_sheet(sheet.headers, sheet.rows)
            if kind == "unknown":
                # Молча пропущенный лист — это потеря, которую видно только по
                # несовпадению чисел «на входе» и «на выходе» в разведке.
                log.info(
                    "Лист «%s» файла %s не опознан: строк %d, заголовки %s",
                    sheet.name,
                    sheet.file,
                    len(sheet.rows),
                    sheet.headers[:6],
                )
            elif kind == "qa":
                for record in self._qa_records(sheet, ctx):
                    yield record
            elif kind == "feedback":
                for review in self._feedback_records(sheet):
                    yield review

    def _qa_records(self, sheet: _Sheet, ctx: IngestContext) -> Iterator[ArticleRecord]:
        """Лист «вопрос — ответ» → статьи."""
        headers, rows, sheet_name, place, file = (
            sheet.headers,
            sheet.rows,
            sheet.name,
            sheet.place,
            sheet.file,
        )
        question_at, answer_at = detect_columns(headers, rows)
        # Линию, если она проставлена в выгрузке, берём из данных: угадывать
        # классификатором то, что уже написано в файле, — терять точность
        # ровно в маршрутизации, за которую кейс и спрашивают.
        line_at = detect_line_column(headers)
        dropped = 0
        for index, row in enumerate(rows, start=1):
            question = row[question_at].strip() if question_at < len(row) else ""
            answer = row[answer_at].strip() if answer_at < len(row) else ""
            # Половина пары бесполезна: статья без ответа отвечает пустотой,
            # а ответ без вопроса не находится поиском.
            if not question or not answer:
                dropped += 1
                continue
            yield ArticleRecord(
                question=question,
                answer=answer,
                # Своей категории у книги нет — имя листа единственное, что
                # отделяет вопросы про регистрацию от вопросов про оплату.
                category=ctx.params.get("category") or sheet_name,
                line=(parse_line(row[line_at]) if 0 <= line_at < len(row) else None)
                or ctx.params.get("line"),
                audience=ctx.params.get("audience") or "all",
                origin=Provenance(
                    file=file,
                    locator=f"{place}, строка {index}",
                    source_name=self.name,
                    authored=True,
                ),
            )
        if dropped:
            log.info(
                "Лист «%s» файла %s: пропущено строк без пары — %d из %d",
                sheet_name,
                file,
                dropped,
                len(rows),
            )

    def _feedback_records(self, sheet: _Sheet) -> Iterator[FeedbackRecord]:
        """Лист отзывов → оценки специалистов."""
        headers, rows, place, file = sheet.headers, sheet.rows, sheet.place, sheet.file
        rating_at = _has(headers, RATING_COLUMNS)
        comment_at = _has(headers, COMMENT_COLUMNS)
        operator_at = _has(headers, OPERATOR_COLUMNS)
        for index, row in enumerate(rows, start=1):
            try:
                rating = int(float(row[rating_at]))
            except ValueError, IndexError, OverflowError:
                # «отлично» и пустая ячейка — не ноль: посчитав их числом,
                # мы испортим среднюю оценку специалиста. OverflowError —
                # про «inf» из сломанной выгрузки: он летит мимо ValueError
                # и уносит с собой весь разбор книги, а не одну строку.
                continue
            yield FeedbackRecord(
                rating=rating,
                comment=row[comment_at].strip() if comment_at < len(row) else "",
                operator=row[operator_at].strip() if 0 <= operator_at < len(row) else "",
                origin=Provenance(
                    file=file,
                    locator=f"{place}, строка {index}",
                    source_name=self.name,
                    authored=True,
                ),
            )
