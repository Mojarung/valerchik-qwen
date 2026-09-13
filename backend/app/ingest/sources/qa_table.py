"""Готовая выгрузка «вопрос — ответ»: markdown-таблица, CSV, таблица внутри документа.

Такие данные человек уже разложил по колонкам: вопрос отделён от ответа его руками.
Гонять их через нарезку на абзацы и генерацию вопросов — значит терять авторские
формулировки и придумывать к готовому ответу свой вопрос. Поэтому источник ничего
не сочиняет, а записи едут с authored=True: их ответ можно отдать пользователю
дословно, в отличие от кусков инструкции, которые писались не как ответ.
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator

from app.ingest.blob import SHEET_SUFFIXES, FileBlob
from app.ingest.records import ArticleRecord, Provenance
from app.ingest.registry import IngestContext, register
from app.ingest.tables import parse_article_table

# Уверенность растёт со строками: пять строк могут оказаться случайной табличкой
# внутри методички, две сотни — уже наверняка выгрузка из чужой базы знаний.
# Потолок ниже единицы, чтобы источник со специфичным распознаванием (json, pdf)
# мог перебить нас на своём формате.
BASE_CONFIDENCE = 0.6
MAX_CONFIDENCE = 0.95
ROWS_FOR_FULL_CONFIDENCE = 200

CSV_SUFFIXES = (".csv", ".tsv")
# Разделители по убыванию вероятности для наших данных: Excel с русской локалью
# сохраняет CSV через «;», выгрузки из тикет-систем — через табуляцию, и только
# англоязычные инструменты пишут запятую.
CSV_DELIMITERS = (";", "\t", ",")


def _cell(value: str) -> str:
    """Ячейка CSV → ячейка markdown-таблицы.

    Вертикальная черта разорвала бы разметку колонок, а перевод строки внутри
    закавыченной ячейки — саму строку таблицы. Перенос отдаём литеральными «\\n»:
    parse_article_table возвращает их в ответ настоящим переносом, так что
    многострочные ответы поддержки не теряют форматирование.
    """
    return value.strip().replace("|", "/").replace("\r\n", "\\n").replace("\n", "\\n")


_MIN_TABLE_ROWS = 2  # шапка и хотя бы одна строка данных


def _csv_as_markdown(text: str) -> str:
    """CSV → markdown-таблица. Не сваливаем на markitdown, потому что он знает только запятую.

    Русский Excel сохраняет «Вопрос;Ответ» — markitdown кладёт такую строку в одну
    колонку, вопрос слипается с ответом, распознавание колонок видит одну колонку,
    и готовая выгрузка молча проезжает мимо источника в запасной разбор моделью.
    Если разделитель не нашёлся, отдаём текст как есть: это не таблица.
    """
    header = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max(CSV_DELIMITERS, key=header.count)
    if header.count(delimiter) == 0:
        return text
    rows = [
        row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(cell.strip() for cell in row)
    ]
    if len(rows) < _MIN_TABLE_ROWS:
        return text
    width = max(len(row) for row in rows)
    lines = []
    for index, row in enumerate(rows):
        cells = [_cell(cell) for cell in row] + [""] * (width - len(row))
        lines.append("| " + " | ".join(cells) + " |")
        if index == 0:
            lines.append("|" + "---|" * width)
    return "\n".join(lines)


def _table_text(blob: FileBlob) -> str:
    """Текст, в котором ищем таблицу."""
    if blob.suffix in CSV_SUFFIXES:
        return _csv_as_markdown(blob.decoded())
    return blob.text


@register
class QaTableSource:
    name = "qa_table"
    # Тай-брейк при равной уверенности: выше разбора документа и запасного разбора
    # моделью — если файл и правда выгрузка, резать его на абзацы незачем.
    priority = 10

    async def sniff(self, blob: FileBlob) -> float:
        # Книги Excel забирает spreadsheet: он читает листы по отдельности, а нам
        # markitdown склеил бы «Вопросы», «Отзывы» и справочник организаций в одну
        # простыню, из которой не собрать ни один из трёх наборов данных.
        if blob.is_binary or blob.suffix in SHEET_SUFFIXES:
            return 0.0
        try:
            rows = parse_article_table(_table_text(blob), allow_small_explicit_qa=blob.suffix in CSV_SUFFIXES)
        except Exception:  # noqa: BLE001 — «не мой формат», а не поломка конвейера
            return 0.0
        if not rows:
            return 0.0
        return min(MAX_CONFIDENCE, BASE_CONFIDENCE + len(rows) / ROWS_FOR_FULL_CONFIDENCE)

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[ArticleRecord]:
        # Пустая выдача вместо исключения: для реестра это тот же отказ, но он
        # не роняет разбор остальных файлов папки.
        rows = (
            parse_article_table(_table_text(blob), allow_small_explicit_qa=blob.suffix in CSV_SUFFIXES) or []
        )
        for index, row in enumerate(rows, start=1):
            yield ArticleRecord(
                question=row["question"],
                answer=row["answer"],
                # Категория и аудитория приезжают снаружи — из формы загрузки или
                # имени папки: в самой таблице их обычно нет.
                category=ctx.params.get("category") or "",
                # А вот линию, если она в выгрузке проставлена, берём из данных:
                # это первоисточник, и угадывать классификатором написанное в файле
                # значит терять точность ровно в маршрутизации обращений — том, за
                # что кейс и спрашивает. Нечитаемое значение вернёт None, и линию
                # тогда подскажет классификатор, как раньше.
                line=row.get("line") or ctx.params.get("line"),
                audience=ctx.params.get("audience") or "all",
                # Номер строки выгрузки, дошедшей до записи: строки без ответа по
                # существу parse_article_table отбрасывает, поэтому это не номер
                # строки в файле, а порядковый номер статьи в таблице.
                origin=Provenance(
                    file=blob.name, locator=f"строка {index}", source_name=self.name, authored=True
                ),
            )
