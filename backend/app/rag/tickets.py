"""Выгрузка обращений техподдержки → типовые решения с живыми формулировками вопросов.

Отдельное обращение — это отчёт о конкретной заявке, а не знание. Знание — решение,
которое поддержка давала многим людям: такие решения группируются, шум и частные
случаи (номера, статусы «на момент проверки», «исправлено») отбрасываются.
"""

import io
import re
from collections import Counter
from dataclasses import dataclass, field

import openpyxl

from app.rag.passages import KIND_TICKET, Passage
from app.rag.text import clean_markup

DOC_TITLE = "Обращения в техподдержку"
MIN_SOLUTION_CHARS = 80
SHORT_SOLUTION_CHARS = 220
SINGLE_MIN_CHARS = 260
MIN_GROUP = 2
MAX_EXAMPLES = 6
EXAMPLE_MAX_CHARS = 220
EXAMPLE_MIN_CHARS = 12
KEY_CHARS = 180

_CASE = re.compile(
    r"\d{9,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}|[\w.+-]+@[\w-]+\.[a-z]{2,}|\bинн\s*:?\s*\d|№\s*\d{5,}"
    r"|workflow id|id эдо|реестров\w+ номер\w*\s*\d",
    re.IGNORECASE,
)
_TEMPORARY = re.compile(
    r"массов\w+ задач|наблюдаются сложности|ведутся работы|в ближайшее время|технически\w+ работ",
    re.IGNORECASE,
)
_DONE = re.compile(
    r"^(?:добрый день|здравствуйте)?[!.,\s]*(?:проблема|ошибка|замечание)?\s*"
    r"(?:устранен|исправлен|решен|выполнен)"
    r"|на момент проверки|просьба предоставить|предоставьте|заказчик принял|актуализирован"
    r"|в обращении предоставлен|по информации,? полученн|контракт заключен|направьте|пришлите|уточните",
    re.IGNORECASE,
)
_INSTRUCTIVE = re.compile(
    r"необходимо|нужно|требуется|рекоменд|перейдите|нажмите|выберите|заполните|если|следует|для этого|можно"
    r"|обратит|повторите|проверьте",
    re.IGNORECASE,
)
_SUBTOPIC = re.compile(r"^\s*Подтема запроса:\s*(?P<sub>[^/]{2,160})/(?P<text>.*)$", re.DOTALL)
_TECH = re.compile(r"ID запроса поддержки|ID шаблона|Текст ошибки:|<p>", re.IGNORECASE)
_GREETING = re.compile(
    r"^(?:добрый (?:день|вечер|утро)|здравствуйте|доброе утро|приветствую|добрый)[!.,\s]*", re.IGNORECASE
)
_KEY = re.compile(r"[^a-zа-я]+")


@dataclass
class _Group:
    solutions: Counter[str] = field(default_factory=Counter)
    subtopics: Counter[str] = field(default_factory=Counter)
    topics: Counter[str] = field(default_factory=Counter)
    examples: list[str] = field(default_factory=list)
    count: int = 0


def _key(solution: str) -> str:
    return _KEY.sub(" ", solution.lower().replace("ё", "е")).strip()[:KEY_CHARS]


def useful_solution(solution: str) -> bool:
    if len(solution) < MIN_SOLUTION_CHARS or _CASE.search(solution) or _TEMPORARY.search(solution):
        return False
    if len(solution) < SHORT_SOLUTION_CHARS and _DONE.search(solution):
        return False
    return bool(_INSTRUCTIVE.search(solution))


def _question(description: str) -> tuple[str, str]:
    """(подтема, текст вопроса человека)."""
    text = clean_markup(description)
    subtopic = ""
    m = _SUBTOPIC.match(text)
    if m:
        subtopic, text = m["sub"].strip(), m["text"].strip()
    if _TECH.search(text):
        text = ""
    text = _GREETING.sub("", " ".join(text.split())).strip()
    return subtopic, text[:EXAMPLE_MAX_CHARS]


def _columns(header: tuple[object, ...]) -> dict[str, int]:
    names = {str(value or "").strip().lower(): index for index, value in enumerate(header)}
    return {
        key: next((i for name, i in names.items() if name.startswith(prefix)), -1)
        for key, prefix in (("description", "описан"), ("solution", "решени"), ("topic", "тем"))
    }


def is_ticket_export(header: tuple[object, ...]) -> bool:
    cols = _columns(header)
    return cols["description"] >= 0 and cols["solution"] >= 0


def parse_tickets(data: bytes) -> list[Passage]:
    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    groups: dict[str, _Group] = {}
    try:
        rows = book.active.iter_rows(values_only=True)
        cols = _columns(next(rows))
        for row in rows:
            solution = clean_markup(str(row[cols["solution"]] or ""))
            if not useful_solution(solution):
                continue
            subtopic, question = _question(str(row[cols["description"]] or ""))
            group = groups.setdefault(_key(solution), _Group())
            group.count += 1
            group.solutions[solution] += 1
            if subtopic:
                group.subtopics[subtopic] += 1
            if cols["topic"] >= 0 and row[cols["topic"]]:
                group.topics[str(row[cols["topic"]]).strip()] += 1
            if len(question) >= EXAMPLE_MIN_CHARS and question not in group.examples:
                group.examples.append(question)
    finally:
        book.close()
    passages = []
    for group in groups.values():
        solution = group.solutions.most_common(1)[0][0]
        if group.count < MIN_GROUP and len(solution) < SINGLE_MIN_CHARS:
            continue
        subtopic = group.subtopics.most_common(1)[0][0] if group.subtopics else ""
        topic = group.topics.most_common(1)[0][0] if group.topics else ""
        examples = sorted(group.examples, key=len)[:MAX_EXAMPLES]
        passages.append(
            Passage(
                title=subtopic or topic or "Решение техподдержки",
                text=solution,
                doc=DOC_TITLE,
                path=topic,
                kind=KIND_TICKET,
                audience="all",
                examples=examples,
                extra={"tickets": group.count},
            )
        )
    passages.sort(key=lambda p: -int(p.extra["tickets"]))
    return passages
