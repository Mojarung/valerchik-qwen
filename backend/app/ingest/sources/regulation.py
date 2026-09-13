"""Нормативный акт (регламент, приказ, положение): статья на пункт, дословно.

Нарезка document режет по абзацам и заголовкам — для методички это верно, для
регламента нет. Смысловая единица регламента — пункт: «5.2. Просьба о снятии
котировочной сессии с публикации подлежит рассмотрению при соблюдении следующих
условий: …» отвечает ровно на один вопрос, а кусок «по 900 символов» склеивает
его с соседними пунктами о другом и отрывает от подпунктов. Поэтому здесь:

- раздел «N. Название» и приложение «Приложение N к Регламенту» — рубрика;
- пункт «N.N.» со всеми подпунктами — статья; слишком длинный пункт делится на
  подпункты, и каждый несёт с собой вводную фразу родителя, без которой
  «3.5.3. Выявления признаков…» не читается;
- определения из разделов сокращений и понятий — отдельные статьи «Что такое
  СКК?»: на такие вопросы пользователи приходят чаще всего, а в тексте раздела
  определение тонет среди сорока соседних.

Текст пункта нормативный, и пересказывать его нельзя: «подождите, блокировка
снимется сама» вместо «обжалуйте в Арбитражной комиссии» — ровно та ошибка, ради
которой ответ отдаётся дословно. Поэтому пункты помечены authored=True: вопрос
придумали мы, но ответ — слово в слово текст акта, с номером пункта, по которому
его найдут в первоисточнике.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field

from app.ingest.blob import FileBlob
from app.ingest.boilerplate import strip_boilerplate
from app.ingest.chunking import Section, _split_body
from app.ingest.questions import extract_keywords, generate_question
from app.ingest.records import ArticleRecord, Provenance
from app.ingest.registry import IngestContext, register
from app.ingest.sources.base import SourceMismatch
from app.services import kbkind

log = logging.getLogger(__name__)

# Выше pdf_figures (0.85): тот берёт любой PDF по расширению, а мы узнаём акт по
# содержимому и режем его по пунктам — docling порезал бы регламент как
# методичку. Ниже источников, узнающих структурированный файл целиком (таблицы,
# JSON, выгрузка поддержки): им книга или выгрузка понятнее, чем нам текст.
CONFIDENCE = 0.88
SUFFIXES = (".pdf", ".docx", ".doc", ".rtf", ".odt", ".txt", ".md", ".markdown")
# Для распознавания PDF хватает первых страниц: пункты и «настоящего Регламента»
# начинаются с первого же раздела. Весь документ markitdown разбирает минуту,
# а sniff спрашивают у каждого загруженного PDF.
SNIFF_PAGES = 8

# Длиннее — пункт делится на подпункты: эмбеддер видит только начало ответа, и
# подпункт о нецензурных обращениях в конце длинного пункта для него не существует.
MAX_ARTICLE_CHARS = 1500
# Вопрос длиннее — уже пересказ пункта, в списке уточнений он не читается.
# Порог щедрый: «В каких случаях блокировка личного кабинета поставщика …
# осуществляется сроком на три месяца?» длинный, но по нему пункт и находят.
QUESTION_MAX_CHARS = 170
QUESTION_MIN_CHARS = 12
# Фраза пункта вместо вопроса — заголовок в одну-две строки списка.
STATEMENT_MAX_CHARS = 120
# Рубрика — строка в списке статей; длинное название приложения обрезаем.
CATEGORY_MAX_CHARS = 90
# Вводная часть приложения без пунктов (таблица, преамбула) короче — не статья.
MIN_PREAMBLE_CHARS = 200
# Столько «|» во вводной части — это markdown-таблица, а не текст.
MAX_TABLE_BARS = 10
MAX_PARALLEL_QUESTIONS = 8

# Нумерация. Первый пункт части — «1.» или «1.1.»; первый подпункт — «.1», в
# крайнем случае «.3» (первые могли утратить силу); шаг между соседними — до
# пяти: пропуск больше — уже не пропуск, а случайная цифра переноса.
FIRST_CLAUSE_MAX_DEPTH = 2
MAX_FIRST_NUMBER = 3
MAX_NUMBER_GAP = 5
# Заголовок раздела длиннее — уже пункт, а не название.
HEADING_MAX_CHARS = 160
# Название приложения: «ПРАВИЛА / ПРОВЕДЕНИЯ …» — несколько коротких строк.
TITLE_MAX_LINES = 6
TITLE_LINE_MAX_CHARS = 100
# Слово капсом, встреченное в тексте хотя бы дважды, — сокращение, а не случайность.
MIN_ABBREVIATION_USES = 2
# «Система - источник команды - информационная система…»: второе тире в начале
# строки и не больше трёх слов между тире — дефис внутри термина.
TERM_SECOND_DASH_WITHIN = 60
TERM_INNER_WORDS = 3
TERM_MAX_CHARS = 90
# Короче этого фразу по запятым не укорачиваем — останется обрубок.
MIN_COMPACT_CHARS = 30

# ---------------------------------------------------------------- распознавание

# «настоящего Регламента», «настоящими Правилами» — акт ссылается сам на себя.
# Инструкции так не пишут: во всех шести инструкциях портала ноль таких оборотов,
# в регламенте — шесть десятков. Инструкция в списке нарочно отсутствует.
_SELF_REFERENCE = re.compile(
    r"\bнастоящ\w{0,3}\s+(?:регламент|правил|положени|приказ|порядк|постановлени|распоряжени|соглашени)",
    re.IGNORECASE,
)
# Пункт второго уровня и глубже с точкой после номера: «1.2.», «3.5.3.», «1.1.1(1).».
# Строки оглавления («2.1. Описание процесса …… 5») пунктами не считаются.
_DEEP_CLAUSE = re.compile(r"^\s*\d{1,2}(?:\.\d{1,3}){1,4}(?:\(\d{1,2}\))?\.\s+\S")
_TOC_LEADER = re.compile(r"\.{4,}|…{2,}")
# В инструкциях на каждый раздел десятки «(Рисунок 26)», в актах — ни одного.
_FIGURE = re.compile(r"\bрис(?:\.|ун\w*)\s*\d", re.IGNORECASE)

MIN_SELF_REFERENCES = 2
MIN_DEEP_CLAUSES = 10
# Пунктов должно быть заметно больше ссылок на рисунки: у инструкции с
# нумерованными разделами рисунков в разы больше, чем строк-пунктов.
MAX_FIGURES_PER_CLAUSE = 0.25


@dataclass(frozen=True)
class Signals:
    self_references: int
    deep_clauses: int
    figures: int

    @property
    def is_regulation(self) -> bool:
        return (
            self.self_references >= MIN_SELF_REFERENCES
            and self.deep_clauses >= MIN_DEEP_CLAUSES
            and self.figures <= MAX_FIGURES_PER_CLAUSE * self.deep_clauses
        )


def regulation_signals(text: str) -> Signals:
    """Признаки нормативного акта в тексте — отдельно, чтобы замер видел, почему."""
    lines = text.split("\n")
    return Signals(
        self_references=len(_SELF_REFERENCE.findall(text)),
        deep_clauses=sum(1 for line in lines if _DEEP_CLAUSE.match(line) and not _TOC_LEADER.search(line)),
        figures=len(_FIGURE.findall(text)),
    )


def _head_text(blob: FileBlob) -> str:
    """Текст для распознавания: у PDF — первые страницы, у остального — весь.

    pdfminer, а не blob.text: markitdown разбирает весь документ, и на инструкции
    в 370 страниц это минута — на каждый загруженный PDF, даже чужой нам.
    """
    if blob.suffix == ".pdf":
        from pdfminer.high_level import extract_text

        return extract_text(io.BytesIO(blob.data), maxpages=SNIFF_PAGES)
    return blob.text


# ---------------------------------------------------------------- нормализация

_CLAUSE = re.compile(r"^(?P<label>\d{1,2}(?:\.\d{1,3}){0,4}(?:\(\d{1,2}\))?)\.\s+(?P<text>\S.*)$")
_APPENDIX = re.compile(r"^Приложение(?:\s+N?\s*(?P<number>\d{1,2}))?$")
# Правки КонсультантПлюс: «(в ред. приказа … от 20.01.2025 N 70-01-1/25)»,
# «(п. 1.1.1(1) введен приказом …)». История редакций ответом не является, а в
# пункте она разрывает текст посередине перечня.
_EDITION = re.compile(
    r"^\((?:в\s+ред\.|(?:п|пп|абзац|подп)\.?\s*[\d.()]*\s*(?:введен|в\s+ред\.|утратил)|введен|утратил)",
    re.IGNORECASE,
)
_SERVICE_LINES = re.compile(
    r"^(?:Список изменяющих документов|КонсультантПлюс: примечание\.?)$", re.IGNORECASE
)
_LIST_ITEM = re.compile(r"^[-–—•]\s+")
_MD_MARKUP = re.compile(r"^#{1,6}\s+|\*\*")
_BLANK_FIELD = re.compile(r"_{3,}")
# Приложение-бланк («Примерная форма» с прочерками вместо реквизитов) —
# шаблон для заполнения, а не правило: отвечать им нечего.
MIN_BLANK_FIELDS_FOR_FORM = 3


def _quotes(text: str) -> str:
    """Прямые кавычки выгрузки → «ёлочки»: в ответе они читаются, в вопросе не мешают."""
    text = re.sub(r'(^|[\s(\[«-])"(?=\S)', r"\1«", text)
    return text.replace('"', "»")


def _join_lines(lines: list[str]) -> str:
    """Строки PDF → текст: перенос строки — пробел, перечень — с новой строки.

    Перенос по слогу с дефисом («Сторона-» / «координатор», «223-» / «ФЗ»)
    склеиваем без пробела; «(далее -» / «сеть» — с пробелом: там дефис отбит.
    """
    out = ""
    for line in lines:
        if not line:
            if out and not out.endswith("\n"):
                out += "\n"
            continue
        if not out or out.endswith("\n"):
            out += line
        elif _LIST_ITEM.match(line) and out.rstrip().endswith((":", ";", ".")):
            out += "\n" + line
        elif re.search(r"[\w]-$", out) and line[:1].isalnum():
            out += line
        else:
            out += " " + line
    return _quotes(out.strip())


def _clean_lines(text: str) -> list[str]:
    """Строки акта без разметки, истории редакций и служебных пометок."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("￾", "-").replace("­", "")
    lines: list[str] = []
    skip_until_paren = False
    skip_note = False
    for raw in text.split("\n"):
        line = _MD_MARKUP.sub("", raw).strip()
        if not line:
            # Пустые строки в акте ничего не значат: структуру задают номера пунктов,
            # а пустая строка — шов страницы или абзац markdown из docx. Оставь их — и
            # «Приложение 3», «к Регламенту» и «ПРАВИЛА / ПРОВЕДЕНИЯ …» по разные
            # стороны шва перестают узнаваться как одна шапка приложения.
            continue
        if skip_until_paren:
            # Пометка о редакции переносится на вторую строку: «…N 70-01-1/25,» / «от 10.06.2025 …)»
            skip_until_paren = not line.endswith(")")
            continue
        if skip_note:
            # Текст примечания КонсультантПлюс — одна строка сразу за пометкой
            skip_note = False
            if line and not _CLAUSE.match(line) and not _APPENDIX.match(line):
                continue
        if _SERVICE_LINES.match(line):
            skip_note = line.lower().startswith("консультантплюс")
            continue
        if _EDITION.match(line):
            skip_until_paren = not line.endswith(")")
            continue
        lines.append(line)
    return lines


# ---------------------------------------------------------------- структура


@dataclass
class Node:
    """Пункт акта: номер, собственный текст и подпункты."""

    number: tuple[int, ...]
    label: str
    lines: list[str] = field(default_factory=list)
    children: list[Node] = field(default_factory=list)
    heading: bool = False

    @property
    def text(self) -> str:
        return _join_lines(self.lines)

    def render(self) -> str:
        own = f"{self.label}. {self.text}" if self.lines else f"{self.label}."
        return "\n".join([own, *(child.render() for child in self.children)])


@dataclass
class Part:
    """Самостоятельная часть документа: сам акт, приложение, приказ-обёртка."""

    number: str = ""  # номер приложения; пусто у самого акта и обёртки
    to: str = ""  # «к Регламенту», «к Соглашению (договору присоединения)»
    title: str = ""
    lines: list[str] = field(default_factory=list)
    parent: str = ""  # номер приложения, к приложению которого относится вложенное


def _parse_number(label: str) -> tuple[tuple[int, ...], bool]:
    """«1.1.1(1)» → ((1, 1, 1), True): вставной пункт с тем же номером и суффиксом."""
    base, _, suffix = label.partition("(")
    return tuple(int(part) for part in base.split(".")), bool(suffix)


def _plausible(previous: tuple[int, ...] | None, number: tuple[int, ...], inserted: bool) -> bool:
    """Номер продолжает нумерацию, а не случайная цифра в начале перенесённой строки.

    «1.5.2 Положения» или «44-ФЗ» в начале строки — перенос фразы, а не пункт:
    точку после номера у них отсекает регулярка, а здесь отсекаются номера,
    которые не могут идти следом за предыдущим пунктом.
    """
    if previous is None:
        return len(number) <= FIRST_CLAUSE_MAX_DEPTH and all(part <= MAX_FIRST_NUMBER for part in number)
    depth = len(number)
    if depth > len(previous) + 1 or number[: depth - 1] != previous[: depth - 1]:
        return False
    if depth == len(previous) + 1:
        return number[-1] <= MAX_FIRST_NUMBER
    step = number[-1] - previous[depth - 1]
    return 0 < step <= MAX_NUMBER_GAP or (step == 0 and inserted and depth == len(previous))


def _tree(lines: list[str]) -> tuple[list[str], list[Node]]:
    """Строки части → (вводный текст до первого пункта, пункты верхнего уровня)."""
    preamble: list[str] = []
    roots: list[Node] = []
    stack: list[Node] = []
    last: tuple[int, ...] | None = None
    for line in lines:
        match = _CLAUSE.match(line)
        if match:
            number, inserted = _parse_number(match.group("label"))
            if _plausible(last, number, inserted):
                node = Node(number=number, label=match.group("label"), lines=[match.group("text")])
                while stack and len(stack[-1].number) >= len(number):
                    stack.pop()
                (stack[-1].children if stack else roots).append(node)
                stack.append(node)
                last = number
                continue
        (stack[-1].lines if stack else preamble).append(line)
    for root in roots:
        root.heading = _is_heading(root)
    return preamble, roots


def _is_heading(node: Node) -> bool:
    """«2. Состав информации, размещаемой в АИС» — заголовок раздела, а не пункт.

    Заголовок короткий, без точки и двоеточия в конце и открывает подпункты
    «2.1.», «2.2.». «3. Блокировка … осуществляется:» — пункт с перечнем, хоть у
    него тоже есть подпункты: двоеточие выдаёт фразу, которая продолжается.
    """
    text = node.text
    return (
        bool(node.children)
        and len(text) <= HEADING_MAX_CHARS
        and not text.endswith((".", ":", ";", ","))
        and text[:1].isupper()
    )


def _split_parts(lines: list[str]) -> list[Part]:
    """Документ → части по «Приложение N / к Регламенту …».

    Граница — строка «Приложение N», за которой идёт «к …»: так в тексте акта
    не выглядит ни одна ссылка на приложение («согласно приложению 4»).
    """
    parts = [Part()]
    index = 0
    while index < len(lines):
        match = _APPENDIX.match(lines[index])
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if not (match and following.startswith("к ")):
            parts[-1].lines.append(lines[index])
            index += 1
            continue
        part = Part(number=match.group("number") or "")
        index += 1
        to_lines: list[str] = []
        # Адресат тянется строками со строчной буквы («к Регламенту информационного
        # / взаимодействия»). Пункт «1. …» — уже текст приложения: цифра не
        # заглавная буква, и без этой проверки пункты уходили в адресат.
        while (
            index < len(lines)
            and lines[index]
            and not lines[index][:1].isupper()
            and not _CLAUSE.match(lines[index])
        ):
            to_lines.append(lines[index])
            index += 1
        part.to = _addressee(" ".join(to_lines))
        title_lines, index = _title(lines, index)
        part.title = _title_text(title_lines, lines)
        parts.append(part)
    return parts


def _addressee(to: str) -> str:
    """«к Регламенту информационного взаимодействия …» → «к Регламенту».

    Остаётся слово-адресат и уточнение в скобках сразу за ним: «к Соглашению
    (договору присоединения)», «к Протоколу (регламенту)».
    """
    match = re.match(r"^к\s+(\S+)(\s+\([^)]*\))?", to)
    return f"к {match.group(1)}{match.group(2) or ''}" if match else to


def _title(lines: list[str], index: int) -> tuple[list[str], int]:
    """Название приложения: «ПРАВИЛА / ПРОВЕДЕНИЯ КОТИРОВОЧНОЙ СЕССИИ» или
    «Соглашение / об использовании … / (пользовательское соглашение)»."""
    while index < len(lines) and not lines[index]:
        index += 1
    if index >= len(lines) or _CLAUSE.match(lines[index]) or not lines[index][:1].isupper():
        return [], index
    taken = [lines[index]]
    capital = _is_caps(lines[index])
    index += 1
    while index < len(lines) and len(taken) < TITLE_MAX_LINES:
        line = lines[index]
        if not line or _CLAUSE.match(line) or len(line) > TITLE_LINE_MAX_CHARS:
            break
        if capital and not _is_caps(line):
            break
        if not capital and not (line[:1].islower() or line[:1] in '"«('):
            break
        taken.append(line)
        index += 1
    return taken, index


def _is_caps(line: str) -> bool:
    letters = [char for char in line if char.isalpha()]
    return bool(letters) and all(char.isupper() for char in letters)


def _abbreviations(lines: list[str]) -> set[str]:
    """Сокращения документа (АИС, СТЕ, УКЭП) — слова капсом посреди обычного текста."""
    counts: Counter[str] = Counter()
    for line in lines:
        if not _is_caps(line):
            counts.update(re.findall(r"\b[А-ЯЁA-Z]{2,6}\b", line))
    return {word for word, count in counts.items() if count >= MIN_ABBREVIATION_USES}


def _title_text(title_lines: list[str], all_lines: list[str]) -> str:
    """Строки названия → «Правила проведения котировочной сессии».

    Капс выгрузки переводим в обычный регистр, но сокращения документа (АИС,
    СТЕ) оставляем: «аис «портал поставщиков»» в рубрике никто не узнает.
    """
    title = " ".join(title_lines)
    if _is_caps(title):
        known = _abbreviations(all_lines)
        words = [word if word.strip('"«»()') in known else word.lower() for word in title.split()]
        title = " ".join(words)
        title = re.sub(r'(^|["«(])([а-яёa-z])', lambda m: m.group(1) + m.group(2).upper(), title)
    return _quotes(title)


# ---------------------------------------------------------------- статьи


@dataclass
class Article:
    question: str
    answer: str
    category: str
    locator: str
    keywords: list[str] = field(default_factory=list)
    authored: bool = True
    # Вопрос уже точный («Что такое СКК?»): формулировать его моделью незачем.
    rephrase: bool = True
    # Где пункт стоит: название части (приложения) и раздел — ими различаются
    # одинаковые вопросы из разных процедур (см. _distinct_questions).
    part: str = ""
    section: str = ""


# «В настоящем Регламенте используются следующие сокращения / понятия:»
_GLOSSARY = re.compile(r"используются\s+следующие\s+(сокращения|понятия|термины|определения)", re.IGNORECASE)
_ABBREVIATION = re.compile(r"^[А-ЯЁA-Z]{2,8}$")


def _split_term(text: str) -> tuple[str, str] | None:
    """«СКК - служба контроля качества…» → («СКК», «служба контроля качества…»).

    «Система - источник команды - информационная система…»: термин с дефисом
    внутри. Если второе тире стоит в первых шестидесяти символах, а между тире не
    больше трёх слов — термин тянется до второго.
    """
    dashes = [match.start() for match in re.finditer(r"\s[-–—]\s", text)]
    if not dashes:
        return None
    cut = dashes[0]
    if (
        len(dashes) > 1
        and dashes[1] < TERM_SECOND_DASH_WITHIN
        and len(text[dashes[0] + 3 : dashes[1]].split()) <= TERM_INNER_WORDS
    ):
        cut = dashes[1]
    term, definition = text[:cut].strip(), text[cut + 3 :].strip()
    if not term or len(term) > TERM_MAX_CHARS or not definition:
        return None
    return term, definition


def _lower_first(text: str) -> str:
    """Первую букву — строчной, если это не сокращение («СКК», «АИС»)."""
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]


def _term_question(term: str) -> str:
    if _ABBREVIATION.match(term):
        return f"Что такое {term}?"
    if "«" in term:
        return f"Что такое {_lower_first(term)}?"
    return f"Что означает «{term}»?"


# Точка после сокращения — не конец фразы: «г. N 70-01-17/24», «ст. 93».
_ABBREVIATED = re.compile(r"(?:\b[гпчстN]|\bст|\bпп|\bруб|\bдр|\bтыс|\bмлн)\.$")


def _lead(text: str) -> str:
    """Первая фраза пункта — до двоеточия перечня или конца предложения."""
    head = text.split("\n", 1)[0]
    colon = head.find(":")
    for match in re.finditer(r"[.;]\s+(?=[А-ЯЁA-Z«])", head):
        if colon != -1 and match.start() > colon:
            break
        if _ABBREVIATED.search(head[: match.start() + 1]):
            continue
        return head[: match.start() + 1]
    return head[: colon + 1] if colon != -1 else head


def _plain(text: str) -> str:
    """Фраза для вопроса: без скобок, ссылок на пункты, вставок «в том числе …».

    Ссылка «в соответствии с пунктом 4.4 настоящего Регламента» в ответе нужна,
    а в вопросе только удлиняет его до нечитаемого.
    """
    text = re.sub(r"\s*\([^()]*\)", "", text)
    text = re.sub(r",\s+(?:в том числе|за исключением|при этом)[^,]*,", "", text)
    text = re.sub(
        r"\s+(?:в соответствии с|согласно)\s+(?:пункт|подпункт|раздел|приложени|правилам|порядк)\w*"
        r"(?:\s+(?:[\d.]+|и|-|,))*(?:\s+к)?(?:\s+настоящ\w+)?(?:\s+(?:Регламент|Положени|Правил|Порядк|Соглашени)\w*)?",
        "",
        text,
    )
    return " ".join(text.split()).rstrip(" .;:,")


# Модальное слово в хвосте фразы («… заказчик обязан») без глагола за ним — не вопрос.
_TRAILING_MODAL = re.compile(
    r"\s+(?:обязан[аоы]?|должен|должна|должно|должны|может|могут|вправе|только|лишь)$"
)
# Грубый признак глагола или краткого причастия: «рассматривает», «осуществляется»,
# «размещены». Без него «Заказчик и поставщик в срок…» дал бы «В какой срок
# заказчик и поставщик?» — действие стоит после срока, и в вопрос оно не попадает.
_VERB = re.compile(
    r"\b[а-яё]{2,}(?:ет|ют|ут|ит|ят|ется|ются|утся|ится|ятся|ен|ена|ено|ены|ан|ана|ано|аны|ть|ться|жет|гут)\b"
)
# Обстоятельство образа действия — хвост, который в вопросе не нужен:
# «расторгнуть Соглашение … путем письменного уведомления» → «расторгнуть Соглашение …?»
_MODIFIER = re.compile(
    r"\s+(?:путем|посредством|в соответствии с|с использованием|согласно|не позднее|в течение|"
    r"в случае|при условии|с приложением|с учетом|по форме)\b.*$"
)
_PREPOSITIONS = frozenset("в во на при по для с со из от до после за об о у к".split())
_PROCESS_VERBS = (
    r"осуществляется|осуществляются|производится|производятся|проводится|проводятся|оформляется|оформляются|"
    r"определяется|определяются|устанавливается|устанавливаются|обеспечивается|обеспечиваются|подписывается|"
    r"подписываются|регистрируется|формируется|формируются|рассчитывается|инициируется|приостанавливается|"
    r"продлевается|используется|используются|применяется|применяются"
)
_VERB_WHERE = r"направляется|направляются|подается|подаётся|подаются|представляется|представляются"
_WHICH = {"ая": "Какая", "ие": "Какие", "ий": "Какой", "ее": "Какое"}

# Шаблон, группа-подлежащее и нужен ли глагол в «pre». Порядок важен: срок и
# случаи конкретнее, чем «как осуществляется».
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # «Просьба о снятии … направляется:» → «Куда направляется просьба о снятии …?»
    (
        re.compile(rf"^(?P<subj>.{{5,110}}?)\s+(?P<verb>{_VERB_WHERE})(?:\s*$|\s+(?:в|во)\s)"),
        "Куда {verb} {subj}?",
    ),
    # «… подлежит рассмотрению при соблюдении следующих условий» → «При каких условиях …?»
    (
        re.compile(
            r"^(?P<pre>.{5,150}?)\s+при\s+(?:соблюдении|наличии|выполнении)\s+(?:следующих\s+)?условий"
        ),
        "При каких условиях {pre}?",
    ),
    # «Блокировка … осуществляется сроком на три месяца в случаях» → «В каких случаях …?»
    (re.compile(r"^(?P<pre>.{5,150}?)\s+в\s+(?:следующих\s+)?случаях\b"), "В каких случаях {pre}?"),
    # «Арбитражная комиссия рассматривает жалобу в течение десяти рабочих дней»
    (
        re.compile(
            r"^(?P<pre>.{5,150}?)\s+(?:в\s+течение|не\s+позднее|не\s+ранее|в\s+срок|не\s+менее\s+чем\s+за|до\s+истечения)\s"
        ),
        "В какой срок {pre}?",
    ),
    # «Котировочная сессия по выбору заказчика длится 3, 6 или 24 часа»
    (re.compile(r"^(?P<subj>.{5,110}?)\s+(?:длится|длятся|продолжается)\b"), "Сколько длится {subj}?"),
    # «В публичной части АИС размещается и обрабатывается следующая информация:»
    (
        re.compile(
            r"^(?P<pre>.{3,110}?)\s+"
            r"(?P<verb>[а-яё]+(?:ся|ет|ют|ит|ят)(?:\s+(?:на себя|и\s+[а-яё]+(?:ся|ет|ют|ит|ят)))?)"
            r"\s+следующ(?P<end>ая|ие|ий|ее)\s+(?P<what>[а-яё]+)$"
        ),
        "{which} {what} {verb} {pre}?",
    ),
    # «Источниками информации для осуществления блокировки являются:»
    (re.compile(r"^(?P<pre>.{5,120}?)\s+(?:является|являются)$"), "Что является {pre}?"),
    # «Координатор осуществляет:» → «Что осуществляет координатор?»
    (
        re.compile(r"^(?P<subj>.{3,70}?)\s+(?P<verb>осуществляет|осуществляют|обеспечивает|обеспечивают)$"),
        "Что {verb} {subj}?",
    ),
    # «Взаимодействие … включает:» → «Что включает взаимодействие …?»
    (
        re.compile(r"^(?P<subj>.{5,110}?)\s+(?P<verb>включает|включают|содержит|содержат)(?:\s+в\s+себя)?$"),
        "Что {verb} {subj}?",
    ),
    # «Решение о снятии … принимается модератором» → «Кем принимается решение о снятии …?»
    (
        re.compile(
            r"^(?P<subj>.{5,120}?)\s+(?P<verb>принимается|принимаются|рассматривается|рассматриваются|"
            r"утверждается|утверждаются)\s+[а-яё]+(?:ом|ем|ой|ей|ами|ями)\b"
        ),
        "Кем {verb} {subj}?",
    ),
    # «Регистрация пользователей осуществляется следующими способами:» → «Как осуществляется регистрация …?»
    (re.compile(rf"^(?P<subj>.{{5,120}}?)\s+(?P<verb>{_PROCESS_VERBS})\b"), "Как {verb} {subj}?"),
    # «Поставщик вправе обжаловать блокировку, направив …» → «Может ли поставщик обжаловать блокировку?»
    (
        re.compile(r"^(?P<subj>.{3,70}?)\s+(?:вправе|имеет\s+право|может)\s+(?P<act>[^,;:]{5,})"),
        "Может ли {subj} {act}?",
    ),
    (
        re.compile(r"^(?P<subj>.{3,70}?)\s+(?:имеют\s+право|могут)\s+(?P<act>[^,;:]{5,})"),
        "Могут ли {subj} {act}?",
    ),
]


_CONDITIONAL = ("если", "когда")
_MODAL = re.compile(r"\b(?:вправе|имеет право|имеют право|может|могут)\b")
# Шаблоны, где «pre» — целая фраза с действием: без глагола в ней вопрос
# («В какой срок заказчик и поставщик?») теряет само действие.
_ACTION_TEMPLATES = ("В какой срок", "В каких случаях", "При каких условиях")


def _fits(groups: dict[str, str], template: str) -> bool:
    """Группы шаблона складываются в вопрос, а не в обрывок фразы."""
    for name in ("subj", "pre"):
        value = groups.get(name)
        # «…с иными системами, которое осуществляется в соответствии с разделом 5»:
        # глагол нашёлся в придаточном, и вопрос вышел бы про «которое».
        if value is not None and re.search(r"\bкотор", value):
            return False
    subject = groups.get("subj")
    if subject is not None:
        first = subject.split()[0].lower()
        # «По решению органа может быть…», «В АИС обеспечивается…» — обратный
        # порядок слов: подлежащее стоит после глагола, и вопрос выйдет задом наперёд.
        # «Если кабинет не используется» и «АИС не используется для…» — условие и
        # отрицание: «Как используется если кабинет не?» вопросом не является.
        if first in _PREPOSITIONS or first in _CONDITIONAL or subject.endswith(" не"):
            return False
    pre = groups.get("pre")
    if pre is not None and template.startswith(_ACTION_TEMPLATES):
        if pre.split()[0].lower() in _CONDITIONAL:
            return False
        # «Пользователь вправе расторгнуть Соглашение, уведомив не позднее чем за
        # пять дней»: вопрос о праве, а не о сроке — его задаст шаблон «Может ли».
        if template.startswith("В какой срок") and _MODAL.search(pre):
            return False
        return bool(_VERB.search(pre.lower()))
    return True


def _trim(value: str, min_words: int) -> str:
    """Срезать обстоятельство и модальный хвост — если после этого что-то осталось.

    «направить по форме обратной связи в СКК просьбу…» без «по форме…»
    превращается в голое «направить»; такой обрезок хуже длинного вопроса.
    """
    cut = _TRAILING_MODAL.sub("", _MODIFIER.sub("", value)).strip(" ,")
    return cut if len(cut.split()) >= min_words else _TRAILING_MODAL.sub("", value).strip(" ,")


def _fill(match: re.Match[str], template: str) -> str | None:
    """Шаблон + найденные группы → вопрос; None, если вопрос не складывается."""
    groups = {name: value for name, value in match.groupdict().items() if value}
    if "end" in groups:
        groups["which"] = _WHICH[groups.pop("end")]
    if not _fits(groups, template):
        return None
    for name, min_words in (("subj", 2), ("pre", 2), ("act", 3)):
        if name in groups:
            groups[name] = _trim(groups[name], min_words)
    if not all(groups.values()):
        return None
    for name in ("subj", "pre"):
        if name in groups:
            groups[name] = _lower_first(groups[name])
    question = template.format(**groups)
    question = question[:1].upper() + question[1:]
    return question if QUESTION_MIN_CHARS <= len(question) <= QUESTION_MAX_CHARS else None


def _ask(lead: str) -> str | None:
    """Вопрос по первой фразе пункта — по нескольким оборотам нормативного языка."""
    for pattern, template in _PATTERNS:
        match = pattern.search(lead)
        question = _fill(match, template) if match else None
        if question:
            return question
    return None


def _compact(phrase: str, limit: int) -> str | None:
    """Длинная фраза → короче за счёт хвостовых оборотов через запятую, не на полуслове."""
    while len(phrase) > limit:
        cut = phrase.rfind(", ")
        if cut < MIN_COMPACT_CHARS:
            return None
        phrase = phrase[:cut]
    return phrase or None


def clause_question(leads: list[str], section: str, label: str) -> str:
    """Детерминированный вопрос к пункту — без модели.

    Вводные фразы родителей склеиваем с фразой пункта: «Блокировка … осуществляется:»
    + «Бессрочно в случаях:» → «В каких случаях блокировка … осуществляется
    бессрочно?». Не узнали оборот ни в склейке, ни в самом пункте — фраза как
    заголовок, укороченная по запятым. Сначала склейка: «Взаимодействие …
    включает подачу и регистрацию оферт» — фраза, а собственное «Подачу и
    регистрацию оферт…» без родителя — обрывок без подлежащего. Не влезает и
    так — начало фразы пункта по границе слова: «Общие положения (п. 1.7)» у
    одиннадцати пунктов подряд не различить ни поиску, ни человеку.
    """
    parts = [_plain(_lead(text)) for text in leads if text]
    combined = ""
    for part in parts:
        combined = f"{combined} {_lower_first(part)}" if combined else part
    combined = " ".join(combined.split())
    own = parts[-1] if parts else ""
    # Склейка без фразы самого пункта — только вводные фразы родителей
    lead_only = " ".join(_lower_first(part) for part in parts[:-1])
    candidates = [phrase for phrase in dict.fromkeys((combined, own)) if phrase]
    for phrase in candidates:
        question = _ask(phrase)
        if question:
            return question
    for phrase in candidates:
        # Придаточное «…, которые осуществляются в соответствии с …» в заголовке
        # только мешает: смысл пункта — в том, что стоит до него.
        title = _compact(re.split(r",\s+котор", phrase)[0], STATEMENT_MAX_CHARS)
        if not title or len(title) < QUESTION_MIN_CHARS:
            continue
        # Склейка, укороченная до вводной фразы родителя, у всех подпунктов одна
        # и та же: «Ответственность Координатора ограничена…» восемь раз подряд.
        if phrase == combined != own and len(title) <= len(lead_only):
            continue
        return title
    if len(own) >= QUESTION_MIN_CHARS:
        return _shorten(own, STATEMENT_MAX_CHARS)
    return f"{section} (п. {label})" if section else f"Пункт {label}"


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut + "…"


@dataclass
class _Scope:
    """Где лежит пункт: для цитаты, рубрики и адреса в документе."""

    cite: str  # «Регламента», «приложения 4 к Регламенту «Правила …»»
    category: str
    locator_prefix: str  # «», «прил. 4, »
    title: str = ""  # название части — для вопроса, когда оборот не узнан


def _articles_of(node: Node, scope: _Scope, section: str, leads: list[Node]) -> Iterator[Article]:
    """Пункт → статья; длинный пункт → статьи по подпунктам с вводной фразой."""
    if _GLOSSARY.search(node.text) and node.children:
        yield from _glossary(node, scope)
        return
    body = "\n".join([*(f"{lead.label}. {lead.text}" for lead in leads), node.render()])
    if len(body) > MAX_ARTICLE_CHARS and node.children:
        # Собственный текст пункта уходит к подпунктам вводной фразой; отдельной
        # статьёй он не нужен — без перечня «осуществляется:» ни на что не отвечает.
        for child in node.children:
            yield from _articles_of(child, scope, section, [*leads, node])
        return
    if not any(char.isalpha() for char in node.render()):
        return  # «2.1. ...» из бланка: номер без текста
    where = f", раздел «{section}»" if section else ""
    answer = f"Пункт {node.label} {scope.cite}{where}:\n{body}"
    question = clause_question(
        [lead.text for lead in leads] + [node.text], section or scope.title, node.label
    )
    yield Article(
        question=question,
        answer=answer,
        category=scope.category,
        locator=f"{scope.locator_prefix}п. {node.label}",
        keywords=extract_keywords(Section(heading=section or None, body=body)),
        part=scope.title,
        section=section,
    )


def _glossary(node: Node, scope: _Scope) -> Iterator[Article]:
    """Раздел сокращений или понятий → статья на каждое определение."""
    category = f"{scope.category.split(':', 1)[0]}: термины и сокращения"
    for child in node.children:
        split = _split_term(child.text)
        if split is None:
            continue
        term, definition = split
        definition = definition.rstrip(".") + "."
        answer = f"{term} — {definition}\n\n(пункт {child.label} {scope.cite})"
        # Расшифровка сокращения — главное, что по нему ищут: «служба контроля качества»
        expansion = _lead(definition).split(",")[0].rstrip(".")
        yield Article(
            question=_term_question(term),
            answer=answer,
            category=category,
            locator=f"{scope.locator_prefix}п. {child.label}",
            keywords=[term, expansion, *extract_keywords(Section(heading=term, body=definition))],
            rephrase=False,
            part=scope.title,
        )


# Слово, которым акт называет сам себя, → (рубрика, родительный падеж для цитаты)
_ACT_NAMES = {
    "регламент": ("Регламент", "Регламента"),
    "положение": ("Положение", "Положения"),
    "правила": ("Правила", "Правил"),
    "порядок": ("Порядок", "Порядка"),
    "приказ": ("Приказ", "приказа"),
    "постановление": ("Постановление", "постановления"),
    "распоряжение": ("Распоряжение", "распоряжения"),
}


def _act_name(title: str) -> tuple[str, str]:
    first = (title.split() or [""])[0].strip('«»"').lower()
    return _ACT_NAMES.get(first, ("Документ", "документа"))


def _is_order(part: Part) -> bool:
    text = " ".join(part.lines[:80]).lower()
    return "приказываю" in text or "постановляю" in text or "приказ" in part.title.lower()


def parse_act(text: str) -> list[Article]:
    """Текст нормативного акта → статьи. Чистая функция: без модели и без сети."""
    lines = _clean_lines(strip_boilerplate(text))
    parts = _split_parts(lines)
    # Акт, утверждённый приказом, приезжает приложением к нему («Приложение / к
    # приказу …»); обёртка-приказ («утвердить», «признать утратившими силу»,
    # «контроль возложить на …») пользователю ни на что не отвечает.
    main_index = next(
        (i for i, part in enumerate(parts) if not part.number and part.to.startswith("к ") and part.title),
        0,
    )
    main = parts[main_index]
    if not main.title:
        main.title = _title_text(_title(main.lines, 0)[0], lines)
    nominative, genitive = _act_name(main.title)
    articles: list[Article] = []
    top = Part()
    for index, part in enumerate(parts):
        if index < main_index and _is_order(part):
            continue
        nested = index != main_index and bool(top.number) and not _addresses(part.to, nominative)
        if index != main_index and not nested:
            # Верхнее приложение запоминаем до проверки на бланк: иначе «Приложение 1
            # к Протоколу» внутри бланка приписалось бы предыдущему приложению —
            # с чужим адресом и чужой цитатой.
            top = part
        if _is_form(part):
            log.info("Приложение %s «%s» — бланк, статей из него не делаем", part.number, part.title)
            continue
        category = _shorten(f"{nominative}: {part.title}", CATEGORY_MAX_CHARS)
        if index == main_index:
            scope = _Scope(cite=genitive, category=nominative, locator_prefix="", title=main.title)
        elif not nested:
            scope = _Scope(
                cite=f"приложения {part.number} {part.to} «{part.title}»",
                category=category,
                locator_prefix=f"прил. {part.number}, ",
                title=part.title,
            )
        else:
            # Вложенное приложение: «Приложение 1 к Соглашению» внутри приложения 11
            scope = _Scope(
                cite=f"приложения {part.number} {part.to} (приложение {top.number} {top.to})",
                category=category,
                locator_prefix=f"прил. {top.number}, прил. {part.number}, ",
                title=part.title,
            )
        articles.extend(_part_articles(part, scope, nominative))
    return _distinct_questions(articles)


# Уточнение в скобках к вопросу — название процедуры, не длиннее
CONTEXT_MAX_CHARS = 70
# Слова вопроса — как их режет поиск (retrieval.tokenize): от трёх букв, основа в шесть
QUESTION_KEY_MIN_WORD = 3
QUESTION_KEY_STEM = 6
# Одинаковый вопрос у стольких статей и больше — уже не различить
MIN_DUPLICATES = 2


def _question_key(question: str) -> frozenset[str]:
    """Слова вопроса так, как их различает поиск: номера и короткие слова не в счёт."""
    words = re.findall(r"[а-яa-z]+", question.lower().replace("ё", "е"))
    return frozenset(word[:QUESTION_KEY_STEM] for word in words if len(word) >= QUESTION_KEY_MIN_WORD)


def _distinct_questions(articles: list[Article]) -> list[Article]:
    """Одинаковые вопросы из разных частей акта — развести контекстом.

    «Куда направляются сведения о заключенном контракте?» дословно стоит в
    правилах прямой закупки и закупки по потребностям, а ответы у них разные.
    С одним вопросом на двоих их не различает ни поиск (номер пункта токенайзер
    выбрасывает), ни человек в вариантах уточнения. Поэтому к таким вопросам
    дописываем то, чем они различаются: название процедуры, иначе раздел, иначе
    адрес пункта. Уникальные вопросы не трогаем.
    """
    groups: dict[frozenset[str], list[Article]] = {}
    for article in articles:
        groups.setdefault(_question_key(article.question), []).append(article)
    for group in groups.values():
        if len(group) < 2:  # noqa: PLR2004 — пара и больше: одиночку разводить не с кем
            continue
        parts = {article.part for article in group}
        sections = {article.section for article in group}
        for article in group:
            if len(parts) > 1 and article.part:
                context = article.part
            elif len(sections) > 1 and article.section:
                context = article.section
            else:
                context = article.locator
            context = _shorten(context, CONTEXT_MAX_CHARS)
            question = article.question
            if context.lower() in question.lower():  # «Раздел (п. 5.2.1)» уже с адресом
                continue
            if question.endswith("?"):
                article.question = f"{question[:-1]} ({context})?"
            else:
                article.question = f"{question} ({context})"
    return articles


def _is_form(part: Part) -> bool:
    """Бланк: «Примерная форма» в шапке или прочерки вместо реквизитов."""
    head = " ".join([part.title, *part.lines[:5]]).lower()
    blanks = sum(1 for line in part.lines if _BLANK_FIELD.search(line))
    return "примерная форма" in head or blanks >= MIN_BLANK_FIELDS_FOR_FORM


def _addresses(to: str, nominative: str) -> bool:
    """«к Регламенту» относится к акту «Регламент»: сравниваем основу слова, не падеж."""
    words = to.split()
    return len(words) > 1 and words[1].lower()[:5] == nominative.lower()[:5]


def _part_articles(part: Part, scope: _Scope, nominative: str) -> Iterator[Article]:
    body_lines = part.lines
    if part.title:
        # Строки названия уже разобраны в _split_parts и в текст части не входят,
        # кроме названия самого акта, найденного по первым строкам.
        title_lines, start = _title(body_lines, 0)
        if title_lines and _title_text(title_lines, body_lines) == part.title:
            body_lines = body_lines[start:]
    preamble, roots = _tree(body_lines)
    intro = _join_lines(preamble)
    if intro.count("|") > MAX_TABLE_BARS:
        # Таблица, которую PDF развернул в строки («Схема распределения
        # полномочий»): склеенная в абзац, она нечитаема, и ответом быть не может.
        log.info("Вводная часть «%s» — развёрнутая таблица, статью из неё не делаем", part.title)
        intro = ""
    if len(intro) >= MIN_PREAMBLE_CHARS:
        # Текст без номеров — преамбула или таблица, развёрнутая PDF в строки:
        # дословно такое не отдашь, поэтому authored=False — пусть пересказывают.
        title = part.title or nominative
        for number, piece in enumerate(_split_body(intro, MAX_ARTICLE_CHARS), start=1):
            yield Article(
                question=title if number == 1 else f"{title} (продолжение)",
                answer=piece,
                category=scope.category,
                locator=f"{scope.locator_prefix}вводная часть",
                keywords=extract_keywords(Section(heading=title, body=piece)),
                authored=False,
                part=part.title,
            )
    for root in roots:
        if root.heading:
            section = root.text
            category = scope.category
            if not part.number:
                # Разделы самого акта — рубрики: «Регламент: Общие положения»
                category = _shorten(f"{nominative}: {section}", CATEGORY_MAX_CHARS)
            section_scope = _Scope(scope.cite, category, scope.locator_prefix, scope.title)
            for child in root.children:
                yield from _articles_of(child, section_scope, section, [])
        else:
            yield from _articles_of(root, scope, "", [])


# ---------------------------------------------------------------- источник


@register
class RegulationSource:
    name = "regulation"
    priority = 20

    async def sniff(self, blob: FileBlob) -> float:
        if blob.suffix not in SUFFIXES:
            return 0.0
        try:
            # В потоке: разбор PDF и docx синхронный и идёт секунды, а sniff
            # спрашивают в цикле событий, где живёт прогресс остальных задач.
            text = await asyncio.to_thread(_head_text, blob)
        except Exception:
            log.info("Файл %s не читается как текст акта", blob.name, exc_info=True)
            return 0.0
        return CONFIDENCE if regulation_signals(text).is_regulation else 0.0

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[ArticleRecord]:
        try:
            text = await asyncio.to_thread(lambda: blob.text)
        except Exception as exc:
            raise SourceMismatch(f"{blob.name} не разворачивается в текст: {exc}") from exc
        articles = parse_act(text)
        if not articles:
            # Признаки акта были, а пунктов не нашлось (нумерация картинкой,
            # автосписок docx) — пусть файл нарежет document.
            raise SourceMismatch(f"в {blob.name} не нашлось пунктов акта")
        questions = await self._questions(articles, ctx)
        category = ctx.params.get("category") or ""
        for article, question in zip(articles, questions, strict=True):
            yield ArticleRecord(
                question=question,
                answer=article.answer,
                category=category or article.category,
                line=ctx.params.get("line"),
                audience=ctx.params.get("audience") or "all",
                keywords=article.keywords,
                origin=Provenance(
                    file=blob.name,
                    locator=article.locator,
                    source_name=self.name,
                    authored=article.authored,
                    kind=kbkind.ANSWER,
                ),
            )

    async def _questions(self, articles: list[Article], ctx: IngestContext) -> list[str]:
        """Вопросы моделью, если она есть и её не выключили; иначе — детерминированные.

        Детерминированный вопрос уходит модели заголовком куска: при любой её
        осечке generate_question вернёт его же, а не голое название раздела.
        """
        runtime = ctx.rt
        if not (ctx.params.get("use_llm", True) and runtime is not None):
            return [article.question for article in articles]
        limit = asyncio.Semaphore(MAX_PARALLEL_QUESTIONS)

        async def ask(article: Article) -> str:
            if not article.rephrase:
                return article.question
            async with limit:
                return await generate_question(
                    runtime.dyn, Section(heading=article.question, body=article.answer)
                )

        return list(await asyncio.gather(*(ask(article) for article in articles)))
