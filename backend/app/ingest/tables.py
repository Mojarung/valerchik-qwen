"""Готовая выгрузка «вопрос — ответ»: распознавание и разбор таблицы.

Не всякий «неформализованный» источник нужно резать: выгрузка из чужой базы
знаний приходит таблицей, где вопрос и ответ уже разделены человеком.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.ingest.chunking import _MEANINGFUL, CAPTION, _normalized_text

log = logging.getLogger(__name__)

# Не всякий «неформализованный» источник нужно резать: выгрузка из чужой БЗ приходит
# таблицей «заголовок — описание», где вопрос и ответ уже разделены человеком.
# Гонять такую через нарезку и LLM — терять готовую структуру и врать формулировками.

_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_QUESTION_COLUMNS = ("вопрос", "заголовок", "тема", "название", "проблема", "question", "title")
_ANSWER_COLUMNS = ("ответ", "описание", "решение", "текст", "answer", "description", "body")
MIN_TABLE_ROWS = 5
# Доля документа, которую должна занимать таблица, чтобы считаться выгрузкой статей
TABLE_DOMINANCE = 0.5


# Подпись к рисунку короткая: длиннее семидесяти символов это уже ответ
_CAPTION_MAX_CHARS = 70


def _useless_answer(question: str, answer: str) -> bool:
    """Строка выгрузки, из которой статья не получится.

    В колонке ответа у таблиц из методичек регулярно приезжает номер страницы
    («60»), подпись к иллюстрации («Рисунок 68 – История изменений») или кусок
    самого вопроса. Такая статья не просто бесполезна: попав в выдачу, она
    отвечает пользователю числом — а уверенность у точного совпадения по
    вопросу высокая, и до честного отказа дело не дойдёт.
    """
    text = answer.strip()
    if not _MEANINGFUL.search(text):
        return True
    if CAPTION.match(text) and len(text) < _CAPTION_MAX_CHARS:
        return True
    return _normalized_text(text) in _normalized_text(question)


def _split_row(line: str) -> list[str]:
    # Строку сюда отдают уже проверенной, но пустой Match здесь стоил бы 500-й:
    # выгрузка чужого формата отличается от нашей одной лишней колонкой.
    matched = _TABLE_ROW.match(line)
    if matched is None:
        return []
    return [cell.strip() for cell in matched.group(1).split("|")]


def _pick_column(headers: list[str], names: tuple[str, ...], default: int) -> int:
    for i, header in enumerate(headers):
        if any(n in header.lower() for n in names):
            return i
    return default


QUESTION_MARK_SHARE = 0.5
MAX_QUESTION_CHARS = 200
# Линия поддержки, если её проставили в выгрузке. Это данные из первых рук:
# угадывать классификатором то, что уже написано в файле, значит терять точность
# ровно в том, за что кейс и спрашивают — в маршрутизации обращений.
LINE_COLUMNS = ("линия", "лини", "line", "уровень", "приоритет")
MAX_LINE = 9


def detect_line_column(headers: list[str]) -> int:
    """Индекс колонки с номером линии поддержки, либо -1."""
    return _pick_column(headers, LINE_COLUMNS, -1)


# Словом линию пишут не реже, чем цифрой: «первая линия», «вторая линия»
_LINE_WORDS = {"перв": 1, "втор": 2, "трет": 3, "четверт": 4, "пят": 5}


def parse_line(value: str) -> int | None:
    """«Л2», «2», «вторая линия» → 2. Мусор → None, линию подскажет классификатор."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    digits = re.search(r"\d+", text)
    if digits:
        number = int(digits.group())
        return number if 1 <= number <= MAX_LINE else None
    for stem, number in _LINE_WORDS.items():
        if text.startswith(stem):
            return number
    return None


def detect_columns(headers: list[str], rows: list[list[str]]) -> tuple[int, int]:
    """Какая колонка — вопрос, какая — ответ. Имя заголовка это только подсказка.

    Выгрузки приезжают с колонками «Текст обращения» и «Решение», которых нет ни в
    одном списке синонимов, и на хакатоне такая приедет наверняка. Поэтому решает
    содержимое: колонка, где больше половины непустых ячеек короткие и кончаются
    вопросительным знаком, — вопрос; самая длинная текстовая колонка из
    оставшихся — ответ. Имя проверяется первым: когда оно совпало со списком
    синонимов, содержимое уже не спрашиваем.
    """
    if not headers:
        return 0, 0
    by_name_q = _pick_column(headers, _QUESTION_COLUMNS, -1)
    by_name_a = _pick_column(headers, _ANSWER_COLUMNS, -1)
    if by_name_q >= 0 and by_name_a >= 0 and by_name_q != by_name_a:
        return by_name_q, by_name_a

    columns = list(range(len(headers)))
    question_at = by_name_q
    if question_at < 0:
        best_share = 0.0
        question_at = 0
        for index in columns:
            values = [row[index].strip() for row in rows if index < len(row) and row[index].strip()]
            if not values:
                continue
            share = sum(
                1 for value in values if value.endswith("?") and len(value) <= MAX_QUESTION_CHARS
            ) / len(values)
            if share > best_share:
                best_share, question_at = share, index
        if best_share < QUESTION_MARK_SHARE:
            question_at = by_name_a if by_name_a == 1 else 0

    answer_at = by_name_a
    # Совпадение по имени бывает ложным, и именно на реальных выгрузках: колонка
    # «Текст обращения» — это вопрос, но слово «текст» стоит в синонимах ответа и
    # срабатывает первым. Схлопнись обе колонки в одну — лист молча уехал бы в
    # «непонятно», а данные потерялись бы целиком. Содержимое сильнее имени.
    if answer_at < 0 or answer_at == question_at:
        lengths = {
            index: sum(len(row[index]) for row in rows if index < len(row))
            for index in columns
            if index != question_at
        }
        answer_at = max(lengths, key=lambda index: lengths[index]) if lengths else min(1, len(headers) - 1)
    return question_at, answer_at


def _pair(cells: list[str], question_at: int, answer_at: int) -> tuple[str, str] | None:
    """Вопрос и ответ из строки таблицы; None — пары нет.

    Пустые ячейки из pandas приезжают строкой «NaN» — такие строки пропускаем,
    статья без ответа бесполезна.
    """
    if max(question_at, answer_at) >= len(cells):
        return None
    question = cells[question_at].strip()
    # «\n» в выгрузке — литеральные два символа, а не перенос строки
    answer = cells[answer_at].strip().replace("\\n", "\n").strip()
    if not question or not answer or answer.lower() in ("nan", "none", "-"):
        return None
    return question, answer


def _read_table(lines: list[str], start: int) -> tuple[list[dict[str, Any]], int, int]:
    """Таблица с заголовком в строке start → (статьи, символов в теле, строка после таблицы)."""
    end = start + 2
    while end < len(lines) and _TABLE_ROW.match(lines[end]):
        end += 1
    headers = _split_row(lines[start])
    # Колонки определяем по всему телу таблицы: заголовок «Текст обращения»
    # ни в одном списке синонимов не значится, а содержимое выдаёт его сразу.
    body = [_split_row(line) for line in lines[start + 2 : end]]
    question_at, answer_at = detect_columns(headers, body)
    line_at = detect_line_column(headers)
    rows: list[dict[str, Any]] = []
    skipped = 0
    for cells in body:
        pair = _pair(cells, question_at, answer_at)
        if pair is None:
            continue
        if _useless_answer(*pair):
            skipped += 1
            continue
        # Значения разного типа: вопрос и ответ — строки, линия — число.
        row: dict[str, object] = {"question": pair[0], "answer": pair[1]}
        line = parse_line(cells[line_at]) if 0 <= line_at < len(cells) else None
        if line:
            row["line"] = line
        rows.append(row)
    if skipped:
        log.info("Выгрузка: пропущено строк без ответа по существу — %d", skipped)
    return rows, sum(len(line) for line in lines[start + 2 : end]), end


# Заголовок таблицы-глоссария: «Таблица 1 - Перечень терминов и сокращений»
_GLOSSARY_TITLE = re.compile(
    r"(перечень\s+терминов|термин\w*\s+и\s+сокращени|сокращени\w*\s+и\s+термин|глоссарий|"
    r"список\s+сокращений)",
    re.I,
)
# Термин слева — сокращение или короткое словосочетание, не абзац
_TERM_MAX_CHARS = 60
# Определение короче — это не расшифровка, а номер страницы или обрывок
_DEFINITION_MIN_CHARS = 8
# Заголовки колонок глоссария: строку с ними в статьи не превращаем
_GLOSSARY_HEADERS = ("термин", "сокращение", "аббревиатура", "расшифровка", "определение", "значение")
# Строка глоссария — «термин | определение»: одной колонки не хватает
_GLOSSARY_COLUMNS = 2
# Нумерованный глоссарий: первая колонка — «14», «21.», а термин и определение правее
_ROW_NUMBER = re.compile(r"^\d{1,3}[.)]?$")
_WITH_NUMBER_COLUMNS = 3
# В термине должна быть буква: «14» и «21.» — это номер строки, а не сокращение
_LETTER = re.compile(r"[А-Яа-яЁёA-Za-z]")


def _term_and_definition(cells: list[str]) -> tuple[str, str] | None:
    """Термин и его расшифровка из строки таблицы; None — строка не про термин."""
    term, definition = cells[0].strip(), cells[1].strip()
    if _ROW_NUMBER.match(term) and len(cells) >= _WITH_NUMBER_COLUMNS:
        term, definition = cells[1].strip(), cells[2].strip()
    # «44- ФЗ» и «Прайс - листы» приезжают из PDF с пробелами вокруг дефиса
    term = re.sub(r"\s*-\s*", "-", " ".join(term.split())).rstrip(".:;")
    if term.lower() in _GLOSSARY_HEADERS or definition.lower() in _GLOSSARY_HEADERS:
        return None
    if not _LETTER.search(term) or len(term) > _TERM_MAX_CHARS:
        return None
    if len(definition) < _DEFINITION_MIN_CHARS or not _MEANINGFUL.search(definition):
        return None
    return term, definition


@dataclass(frozen=True)
class GlossaryTerm:
    """Статья-определение из строки глоссария."""

    question: str
    answer: str
    # Как этот же термин спрашивают люди. Голый «УПД» здесь главный: спрашивают
    # «что такое УПД», а ReAct-агент ищет коротко — «упд», и без отдельной точки
    # векторного индекса на сам термин определение проигрывает статье
    # «11.5.8 Формирование УПД», где то же слово стоит в заголовке раздела.
    phrasings: list[str]


def glossary_terms(heading: str, body: str) -> list[GlossaryTerm]:
    """Таблица терминов → статьи-определения, по одной на термин.

    Глоссарий методички приезжает одной статьёй, где термин лежит в теле таблицы:
    «Что такое УПД?» такая статья не выигрывает, потому что 75 % лексической
    уверенности даёт совпадение с заголовком (TITLE_SHARE), а в заголовке стоит
    «Таблица 1 - Перечень терминов и сокращений». Разобранная по строкам, она
    отвечает: термин оказывается в заголовке статьи-определения.

    Пусто, если заголовок не про термины или строк с определениями в теле нет.
    """
    if not _GLOSSARY_TITLE.search(heading or ""):
        return []
    terms: list[GlossaryTerm] = []
    for line in body.splitlines():
        cells = _split_row(line)
        if len(cells) < _GLOSSARY_COLUMNS or _TABLE_SEPARATOR.match(line):
            continue
        found = _term_and_definition(cells)
        if found is None:
            continue
        term, definition = found
        # «СТЕ, Стандартная товарная единица, Товар» — одна строка на несколько
        # написаний термина. В заголовок идёт первое: остальные всё равно в теле,
        # а «Что означает СТЕ, Стандартная товарная единица, Товар?» — не вопрос.
        names = [name.strip() for name in term.split(",") if name.strip()]
        title = names[0]
        terms.append(
            GlossaryTerm(
                question=f"Что означает {title}?",
                answer=f"{term} — {definition}.",
                phrasings=[*names, f"что такое {title}", f"расшифровка {title}"],
            )
        )
    return terms


def parse_article_table(
    markdown: str, *, allow_small_explicit_qa: bool = False
) -> list[dict[str, Any]] | None:
    """Markdown-таблица «вопрос — ответ» → готовые статьи. None, если это не таблица.

    Берётся самая длинная таблица документа; её строки без пары пропускаются.
    Для отдельной CSV/TSV-выгрузки явные «Вопрос/Ответ» допускают одну строку.
    Встроенные таблицы и эвристически определённые колонки сохраняют общий порог.
    """
    lines = markdown.splitlines()
    best: list[dict[str, Any]] = []
    best_chars = 0
    i = 0
    while i < len(lines):
        if not _TABLE_ROW.match(lines[i]) or i + 1 >= len(lines) or not _TABLE_SEPARATOR.match(lines[i + 1]):
            i += 1
            continue
        start = i
        headers = {cell.casefold() for cell in _split_row(lines[i])}
        rows, chars, i = _read_table(lines, i)
        if (
            allow_small_explicit_qa
            and rows
            and headers & {"вопрос", "question"}
            and headers & {"ответ", "answer"}
            and not any(line.strip() for line in lines[:start] + lines[i:])
        ):
            return rows
        if len(rows) > len(best):
            best, best_chars = rows, chars
    if len(best) < MIN_TABLE_ROWS:
        return None
    # Таблица должна быть самим документом, а не его частью: в инструкциях таблиц
    # хватает (термины, сокращения), и принимать их за выгрузку статей нельзя.
    total_chars = len(markdown.strip())
    if total_chars and best_chars / total_chars < TABLE_DOMINANCE:
        return None
    return best
