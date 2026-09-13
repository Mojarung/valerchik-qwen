"""Страховка ответа модели: служебное — вон, факты — только из статей.

Промпт просит модель не показывать article_id и score и не называть сроков,
которых нет в статье. Просьба — не гарантия: на стенде в ответ протекали
«【3007420c…】» и «score = 0.7228», а на вопрос о блокировке кабинета модель
уверенно пообещала снятие блокировки через срок, которого в базе знаний нет.
Поэтому после модели стоит детерминированная проверка:

- sanitize() вычищает служебное — пометки источников, оценки поиска,
  идентификаторы статей, разметку тикет-системы;
- grounding() находит в ответе факты — числа, сроки, даты, ссылки, почту,
  телефоны, названия в кавычках, — которых нет ни в одном источнике.

Проверка намеренно строгая: пропустить выдуманный срок хуже, чем зря откатиться
на дословный текст статьи — он тоже верный, просто менее гладкий.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

# --- sanitize ---------------------------------------------------------------

# Ссылки не чистим: в адресе портала бывает длинный hex-идентификатор, и
# вырезать его значит сломать ссылку, по которой человеку идти.
_URL = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]«»\"']+", re.IGNORECASE)
# Пометки источников в духе OpenAI-цитирования: «【3007420c…】», «【4:0†source】»
_CITATION_MARK = re.compile(r"【[^】]*】")
_LINE_BREAK_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)
# Разметка тикет-системы из выгрузки обращений: цвет <c #1a1a1a>, подсветка
# <m #ffff00> и обрывки таблиц. Импорт её уже снимает, но статьи, загруженные до
# этого, лежат в базе как есть — а модель охотно повторяет их дословно.
_TICKET_MARKUP = re.compile(
    r"</?(?:c|m)(?:\s+#[0-9a-f]{3,8})?\s*>|</?(?:td|tr|th|table|tbody|thead)\s*/?>", re.IGNORECASE
)
# Фраза с именем инструмента или порога — рассуждение модели вслух («лучший score
# выше threshold, поэтому отвечаю»). Такую фразу выкидываем целиком: вырезанное
# из неё слово оставило бы обрубок без смысла.
# Пометки разбора PDF: docling ставит «<!-- image -->» на место картинки. В статьи
# методичек они попали вместе с текстом, и модель переносила их в ответ дословно.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_SERVICE_TOKEN = re.compile(
    r"\b(?:search_kb|escalate_to_operator|NO_ANSWER|threshold|rank_score|rerank_score|tool_trace)\b",
    re.IGNORECASE,
)
# А оценка и идентификатор обычно прилеплены к полезной фразе — их вырезаем на месте.
# Имена полей поиска в статье не встречаются — их вырезаем и без значения.
_SERVICE_NAME = r"(?:rerank_score|rank_score|article_id)"
# «score» и «confidence» — только со значением: без него это может быть просто слово
_SERVICE_SCORE = r"(?:score|confidence)"
_NUMBER_VALUE = r"\d+(?:[.,]\d+)?"
_HEX_VALUE = r"[0-9a-f]{6,}[0-9a-f-]*…?"
# «ID статьи бюджета» и «идентификатор статьи 123» — обычные слова портала.
# Служебная ссылка — только с hex-идентификатором нашей статьи (в нём есть и
# цифры, и буквы a-f; модель нередко обрезает его до «3007420c…»).
_ARTICLE_REF = r"(?:id|идентификатор\w*)\s+статьи\s*[:=]?\s*(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])" + _HEX_VALUE
_SERVICE_BRACKET = re.compile(
    rf"[ \t]*[(\[][^()\[\]\n]*?(?:\b(?:{_SERVICE_NAME}|{_SERVICE_SCORE})\b|\b{_ARTICLE_REF})"
    r"[^()\[\]\n]*[)\]]",
    re.IGNORECASE,
)
_SERVICE_VALUE = re.compile(
    rf"[,;]?[ \t]*(?:\b{_SERVICE_NAME}\b(?:[ \t]*[:=]?[ \t]*(?:{_HEX_VALUE}|{_NUMBER_VALUE}))?"
    rf"|\b{_SERVICE_SCORE}\b[ \t]*[:=]?[ \t]*{_NUMBER_VALUE}|\b{_ARTICLE_REF})",
    re.IGNORECASE,
)
# Идентификаторы статей — uuid4().hex, иногда в каноническом виде с дефисами
_HEX_ID = re.compile(
    r"\b(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", re.IGNORECASE
)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_BARE_LIST_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*$")
# Место вырезанного фрагмента. Пробелы и знаки приводим в порядок только вокруг
# таких мест: всё остальное — текст статьи, и он должен дойти до модели и до
# человека как есть. Общая чистка «пробел перед точкой» превращала кнопку
# «Загрузить .xlsx с данными» в «Загрузить.xlsx с данными». NUL в тексте из БД
# не бывает (Postgres его не хранит), так что с настоящим текстом метка не спутается.
_GAP = "\x00"
_GAP_IN_BRACKETS = re.compile(rf"[(\[][ \t{_GAP}]*{_GAP}[ \t{_GAP}]*[)\]]")
_GAP_RUN = re.compile(rf"([,;]?)[ \t{_GAP}]*{_GAP}[ \t{_GAP}]*")
_CLOSING = frozenset(",.;:!?)]")
_OPENING = frozenset("([«„“")
_TRAILING_SPACES = re.compile(r"[ \t]+$", re.MULTILINE)
_EXTRA_BLANK_LINES = re.compile(r"\n{3,}")


def _outside_links(text: str, clean: Callable[[str], str]) -> str:
    """Применить чистку ко всему, кроме ссылок."""
    out: list[str] = []
    pos = 0
    for link in _URL.finditer(text):
        out.append(clean(text[pos : link.start()]))
        out.append(link.group(0))
        pos = link.end()
    out.append(clean(text[pos:]))
    return "".join(out)


def _drop_service_sentences(text: str) -> str:
    lines: list[str] = []
    for line in text.split("\n"):
        if _SERVICE_TOKEN.search(line):
            line = " ".join(s for s in _SENTENCE_BREAK.split(line) if not _SERVICE_TOKEN.search(s))
            if not line.strip() or _BARE_LIST_MARKER.match(line):
                continue
        lines.append(line)
    return "\n".join(lines)


def _strip_service(fragment: str) -> str:
    fragment = _SERVICE_BRACKET.sub(_GAP, fragment)
    fragment = _SERVICE_VALUE.sub(_GAP, fragment)
    return _HEX_ID.sub(_GAP, fragment)


def _close_gap(match: re.Match[str]) -> str:
    """Что оставить на месте вырезанного: пробел, запятую или ничего."""
    text, start, end = match.string, match.start(), match.end()
    after = text[end : end + 1]
    # Точка перед буквой — начало расширения файла (« .xlsx»), а не конец фразы
    extension = after == "." and text[end + 1 : end + 2].isalnum()
    if not after or after == "\n" or (after in _CLOSING and not extension):
        return ""  # и запятую перед концом фразы: «Нажмите «Подписать», score: 0.7.»
    if start == 0 or text[start - 1] == "\n" or text[start - 1] in _OPENING:
        return ""
    return match.group(1) + " "


def sanitize(text: str) -> str:
    """Текст для человека: без служебных пометок, оценок, идентификаторов и разметки.

    Markdown не трогаем — списки, жирный и абзацы остаются как есть. Пробелы и
    знаки правятся только на месте вырезанного; вне этих мест меняются лишь
    пробелы в конце строк и лишние пустые строки.
    """
    if not text:
        return ""
    text = text.replace(_GAP, "")
    text = _CITATION_MARK.sub(_GAP, text)
    text = _LINE_BREAK_TAG.sub("\n", text)
    text = _TICKET_MARKUP.sub(_GAP, text)
    text = _HTML_COMMENT.sub(_GAP, text)
    text = _drop_service_sentences(text)
    text = _outside_links(text, _strip_service)
    text = _GAP_IN_BRACKETS.sub(_GAP, text)
    text = _GAP_RUN.sub(_close_gap, text)
    text = _TRAILING_SPACES.sub("", text)
    return _EXTRA_BLANK_LINES.sub("\n\n", text).strip()


# --- grounding --------------------------------------------------------------

_SPACE_LIKE = re.compile(r"[\u00a0\u2007\u2009\u202f\t]")
_DASHES = str.maketrans({"–": "-", "—": "-", "‑": "-"})
_WHITESPACE = re.compile(r"\s+")

_QUOTED = re.compile(r"«([^«»\n]{1,150})»|\"([^\"\n]{1,150})\"|“([^“”\n]{1,150})”|„([^„“”\n]{1,150})[“”]")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LINK = re.compile(
    r"(?:https?://|www\.)[^\s<>()\[\]«»\"']+"
    r"|\b(?:[a-z0-9-]+\.)+(?:ru|su|com|org|net|info|online|gov)\b(?:/[^\s<>()\[\]«»\"']*)?",
    re.IGNORECASE,
)
_PHONE = re.compile(r"(?<![\d+])(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")
_DATE = re.compile(r"(?<!\d)(?<!\d[.])(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?!\d|[.]\d)")
_TIME = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
# Слово-месяц после числа: «31 декабря 2025 года» — та же дата, что «31.12.2025».
_MONTH_WORDS = (
    r"январ\w*", r"феврал\w*", r"март\w*", r"апрел\w*", r"май|мая|мае", r"июн\w*",
    r"июл\w*", r"август\w*", r"сентябр\w*", r"октябр\w*", r"ноябр\w*", r"декабр\w*",
)  # fmt: skip
# Имена тех же месяцев как единиц при числе (см. _UNITS): «31 декабря» — число 31 при месяце
_MONTH_UNITS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip
_MONTH_OF = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _MONTH_WORDS)
_WORD_DATE = re.compile(
    r"(?<!\d)(\d{1,2})\s+(" + "|".join(_MONTH_WORDS) + r")\s+(\d{4})(?!\d)", re.IGNORECASE
)
# Номера пунктов списка и шагов — нумерация, которую модель расставляет сама,
# пересобирая инструкцию в список, или переносит из регламента («1.1. Откройте»).
# Это не факт из статьи, и сверять его не с чем. Многоуровневый номер без точки
# в конце («1.1 Откройте») — только перед заглавной: «1.5 часа» — это срок.
_NOT_A_FACT = re.compile(
    r"(?m)^[ \t]*(?:[-*•][ \t]*)?(?:#+[ \t]*)?"
    r"(?:\d+(?:\.\d+)*[.)](?=\s)|\d+(?:\.\d+)+(?=[ \t]+[*«\"A-ZА-ЯЁ]))"
    r"|(?i:\bшаг\w*)\s+\d+"
)
# Число: «1 000 000» с разрядами через пробел, «0,5», «10».
_TOKEN = re.compile(r"\d{1,3}(?: \d{3})+(?!\d)|\d+(?:[.,]\d+)?|%|₽|[a-zа-я]+")

# Голое число до трёх без единицы — это «два способа» и «вариант 1», а не срок и
# не сумма. Сверять его строго значило бы откатывать на статью почти любой пересказ.
_TRIVIAL_NUMBER = 3
# Сколько слов после числа смотрим в поисках единицы: «10 (десяти) рабочих дней».
_UNIT_WINDOW = 3
# Название сверяем по основам слов: «в разделе «Личный кабинет»» при статье
# «в личном кабинете» — не выдумка. Короткие слова и числа — только точно.
_SHORT_WORD = 3
_MIN_STEM = 3
_ENDING = 3
# «01.02.25» и «01.02.2025» — одна дата: двузначный год дописываем до четырёх цифр.
_FULL_YEAR = 4
# Сплошное число от стольких цифр без единицы — номер: контракта, заявки, ИНН.
# Только такие числа (и контакты со ссылками) вправе подтвердить слова человека.
_ID_DIGITS = 6

# Единицы при числе. Сверяется не только число, но и к чему оно относится:
# «10 минут» в статье не подтверждает «10 рабочих дней» в ответе. Сокращения
# («2024 г.», «5 раб. дней», «24 ч.») модель при пересказе раскрывает — это
# та же единица, а не выдумка.
_UNITS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("рабочих", r"рабоч\w*|раб"),
        ("календарных", r"календарн\w*"),
        ("дней", r"день|дня|дней|дни|дн"),
        ("суток", r"сут\w*"),
        ("часов", r"час|часа|часов|часы|ч"),
        ("минут", r"мин|минута|минуты|минуту|минут"),
        ("секунд", r"сек|секунда|секунды|секунду|секунд"),
        ("недель", r"недел\w*|нед"),
        ("месяцев", r"месяц\w*|мес"),
        ("лет", r"год|года|году|годы|годах|лет|г|гг"),
        ("рублей", r"руб\w*|р|₽"),
        ("копеек", r"коп\w*"),
        ("тысяч", r"тыс\w*"),
        ("миллионов", r"млн|миллион\w*"),
        ("миллиардов", r"млрд|миллиард\w*"),
        ("процентов", r"%|процент\w*"),
        ("килобайт", r"кб|kb|килобайт\w*"),
        ("мегабайт", r"мб|mb|мегабайт\w*"),
        ("гигабайт", r"гб|gb|гигабайт\w*"),
        ("штук", r"шт|штук\w*"),
        ("символов", r"символ\w*|знак\w*"),
        ("раз", r"раз|раза"),
        *zip(_MONTH_UNITS, _MONTH_WORDS, strict=True),
    )
)


def _number_words() -> dict[str, str]:
    """Числа прописью. В регламентах их пишут и так («десяти рабочих дней»), а
    модель пересказывает цифрой — это то же число, а не выдумка."""
    words = {
        "один": 1, "одна": 1, "одно": 1, "одного": 1, "одной": 1, "одну": 1,
        "два": 2, "две": 2, "двух": 2, "три": 3, "трех": 3, "четыре": 4, "четырех": 4,
        "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7,
        "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9, "десять": 10, "десяти": 10,
        "сорок": 40, "сорока": 40, "девяносто": 90, "сто": 100,
    }  # fmt: skip
    stems = {
        "одиннадцат": 11, "двенадцат": 12, "тринадцат": 13, "четырнадцат": 14, "пятнадцат": 15,
        "шестнадцат": 16, "семнадцат": 17, "восемнадцат": 18, "девятнадцат": 19,
        "двадцат": 20, "тридцат": 30,
    }  # fmt: skip
    for stem, value in stems.items():
        words[stem + "ь"] = words[stem + "и"] = value
    words.update({
        "пятьдесят": 50, "пятидесяти": 50, "шестьдесят": 60, "шестидесяти": 60,
        "семьдесят": 70, "семидесяти": 70, "восемьдесят": 80, "восьмидесяти": 80, "девяноста": 90,
    })  # fmt: skip
    return {word: str(value) for word, value in words.items()}


_NUMBER_WORDS = _number_words()
_NUMBER_WORD = re.compile(
    r"(?<![а-я])(?:" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")(?![а-я])"
)
# Составное число: «двадцать пять», «двадцати пяти» — одно число 25, а не 20 и 5.
_TENS_VALUES = frozenset(str(n) for n in range(20, 100, 10))
_ONES_VALUES = frozenset(str(n) for n in range(1, 10))
_TENS = sorted((w for w, v in _NUMBER_WORDS.items() if v in _TENS_VALUES), key=len)
_ONES = sorted((w for w, v in _NUMBER_WORDS.items() if v in _ONES_VALUES), key=len)
_COMPOUND_NUMBER = re.compile(
    r"(?<![а-я])(" + "|".join(reversed(_TENS)) + r")\s+(" + "|".join(reversed(_ONES)) + r")(?![а-я])"
)


def _spelled_out(text: str) -> str:
    """Числа прописью — цифрами: сначала составные, потом одиночные."""
    text = _COMPOUND_NUMBER.sub(lambda m: str(int(_NUMBER_WORDS[m[1]]) + int(_NUMBER_WORDS[m[2]])), text)
    return _NUMBER_WORD.sub(lambda m: _NUMBER_WORDS[m.group(0)], text)


@dataclass(frozen=True, slots=True)
class Fact:
    """Проверяемое утверждение из ответа."""

    kind: str  # name | email | url | phone | date | time | id | number
    text: str  # как написано в ответе — для журнала и meta сообщения
    key: str  # нормализованный вид для сверки
    units: frozenset[str] = frozenset()  # к чему относится число: дни, рубли, месяц…


def _unify(text: str) -> str:
    return _SPACE_LIKE.sub(" ", text)


def _norm(text: str) -> str:
    """Регистр, ё/е, неразрывные пробелы, тире и Markdown-выделение не различают факты."""
    text = _unify(text).lower().replace("ё", "е").translate(_DASHES)
    return _WHITESPACE.sub(" ", text.replace("*", "").replace("`", "")).strip()


def _num_key(token: str) -> str:
    """«1 000» = «1000», «0,5» = «0.5», «9.00» = «9», «05» = «5»."""
    whole, _, frac = token.replace(" ", "").replace(",", ".").partition(".")
    whole = whole.lstrip("0") or "0"
    frac = frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def _url_key(url: str) -> str:
    key = re.sub(r"^(?:https?://)?(?:www\.)?", "", url.lower())
    return key.rstrip("/.,;:!?)")


def _phone_key(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return "7" + digits[1:] if digits.startswith("8") else digits


def _date_key(match: re.Match[str]) -> str:
    day, month, year = match.groups()
    return f"{int(day):02d}.{int(month):02d}.{year if len(year) == _FULL_YEAR else '20' + year}"


def _word_date_key(match: re.Match[str]) -> str:
    day, month_word, year = match.groups()
    month = next(i for i, pattern in enumerate(_MONTH_OF, 1) if pattern.fullmatch(month_word))
    return f"{int(day):02d}.{month:02d}.{year}"


def _time_key(match: re.Match[str]) -> str:
    hours, minutes = match.groups()
    return f"{int(hours):02d}:{minutes}"


def _unit_of(token: str) -> str | None:
    return next((name for name, pattern in _UNITS if pattern.fullmatch(token)), None)


def _units_after(tokens: list[str], index: int, key: str) -> tuple[set[str], list[str]]:
    """Единицы сразу за числом. Пропись того же числа в скобках — не единица, её пропускаем."""
    units: set[str] = set()
    words: list[str] = []
    for token in tokens[index + 1 : index + 1 + _UNIT_WINDOW]:
        if token in _NUMBER_WORDS or (token[0].isdigit() and _num_key(token) == key):
            continue
        unit = _unit_of(token)
        if unit is None:
            break
        units.add(unit)
        words.append(token)
    return units, words


def _mask(
    text: str, pattern: re.Pattern[str], make: Callable[[re.Match[str]], Fact] | None, found: list[Fact]
) -> str:
    """Забрать факты одного вида и вырезать их из текста, чтобы числа внутри
    ссылки, телефона или даты не проверялись второй раз как отдельные числа."""

    def take(match: re.Match[str]) -> str:
        if make is not None:
            found.append(make(match))
        return " "

    return pattern.sub(take, text)


def _quoted(match: re.Match[str]) -> Fact:
    inner = next(group for group in match.groups() if group is not None)
    return Fact("name", match.group(0).strip(), _norm(inner).strip(" .,:;!?-"))


def _link(match: re.Match[str]) -> Fact:
    # Звёздочки и подчёркивания в хвосте — разметка Markdown («**https://…**»), а
    # не часть адреса: с ними верная ссылка из статьи считалась выдуманной.
    text = match.group(0).rstrip(".,;:!?)*_»")
    return Fact("url", text, _url_key(text))


def _numbers(text: str) -> list[Fact]:
    tokens = _TOKEN.findall(_norm(text))
    found: list[Fact] = []
    for index, token in enumerate(tokens):
        if not token[0].isdigit():
            continue
        key = _num_key(token)
        units, words = _units_after(tokens, index, key)
        if not units and key.isdigit() and int(key) <= _TRIVIAL_NUMBER:
            continue
        display = " ".join([token, *words]).replace(" %", "%")
        # Номер пишут сплошь; «1 000 000» с разрядами — это сумма, а не номер
        kind = "id" if not units and token.isdigit() and len(token) >= _ID_DIGITS else "number"
        found.append(Fact(kind, display, key, frozenset(units)))
    return found


def facts(answer: str) -> list[Fact]:
    """Все проверяемые утверждения ответа.

    Порядок извлечения важен: сначала то, внутри чего есть цифры (названия,
    почта, ссылки, телефоны, даты, время), и только потом числа — по остатку.
    """
    found: list[Fact] = []
    text = _unify(answer)
    text = _mask(text, _QUOTED, _quoted, found)
    text = _mask(text, _EMAIL, lambda m: Fact("email", m.group(0), m.group(0).lower()), found)
    text = _mask(text, _LINK, _link, found)
    text = _mask(text, _PHONE, lambda m: Fact("phone", m.group(0).strip(), _phone_key(m.group(0))), found)
    text = _mask(text, _DATE, lambda m: Fact("date", m.group(0), _date_key(m)), found)
    text = _mask(text, _WORD_DATE, lambda m: Fact("date", m.group(0), _word_date_key(m)), found)
    text = _mask(text, _TIME, lambda m: Fact("time", m.group(0), _time_key(m)), found)
    text = _mask(text, _NOT_A_FACT, None, found)
    return found + _numbers(text)


@dataclass(frozen=True, slots=True)
class _Evidence:
    """Источники в виде, удобном для сверки каждого вида фактов."""

    text: str
    links: str
    phones: frozenset[str]
    dates: frozenset[str]
    times: frozenset[str]
    numbers: dict[str, list[frozenset[str]]]


def _evidence(sources: Iterable[str]) -> _Evidence:
    raw = _unify("\n".join(s for s in sources if s))
    text = _norm(raw)
    tokens = _TOKEN.findall(_spelled_out(text))
    numbers: dict[str, list[frozenset[str]]] = {}
    for index, token in enumerate(tokens):
        if token[0].isdigit():
            key = _num_key(token)
            units, _ = _units_after(tokens, index, key)
            numbers.setdefault(key, []).append(frozenset(units))
    dates = {_date_key(m) for m in _DATE.finditer(raw)}
    dates |= {_word_date_key(m) for m in _WORD_DATE.finditer(raw)}
    # «31.12.2025» в статье подтверждает и пересказ словами: «31 декабря», «2025 года»
    for date in dates:
        day, month, year = date.split(".")
        numbers.setdefault(_num_key(day), []).append(frozenset({_MONTH_UNITS[int(month) - 1]}))
        numbers.setdefault(year, []).append(frozenset({"лет"}))
    return _Evidence(
        text=text,
        links=re.sub(r"https?://|www\.", "", text),
        phones=frozenset(_phone_key(m.group(0)) for m in _PHONE.finditer(raw)),
        dates=frozenset(dates),
        times=frozenset(_time_key(m) for m in _TIME.finditer(raw)),
        numbers=numbers,
    )


def _name_found(name: str, text: str) -> bool:
    words = re.findall(r"[a-zа-я0-9]+", name)
    if not words:
        return True
    parts = [
        re.escape(word) + r"(?![a-zа-я0-9])"
        if word.isdigit() or len(word) <= _SHORT_WORD
        else re.escape(word[: max(_MIN_STEM, len(word) - _ENDING)]) + r"[a-zа-я0-9]*"
        for word in words
    ]
    pattern = r"(?<![a-zа-я0-9])" + r"[^a-zа-я0-9]{1,5}".join(parts)
    return re.search(pattern, text) is not None


def _confirmed(fact: Fact, evidence: _Evidence) -> bool:
    match fact.kind:
        case "name":
            return _name_found(fact.key, evidence.text)
        case "email":
            return fact.key in evidence.text
        case "url":
            return fact.key in evidence.links
        case "phone":
            return fact.key in evidence.phones
        case "date":
            return fact.key in evidence.dates
        case "time":
            return fact.key in evidence.times
        case _:
            # Число без единицы подтверждается любым своим вхождением, с единицей —
            # только тем, где рядом стоят те же единицы (или более подробные).
            return any(fact.units <= seen for seen in evidence.numbers.get(fact.key, ()))


# Что вправе подтвердить слова самого человека: его номера, почта, телефоны, ссылки.
_OWN_FACTS = frozenset({"id", "email", "url", "phone"})


def grounding(answer: str, sources: Iterable[str], user_texts: Iterable[str] = ()) -> list[str]:
    """Факты ответа, которых нет ни в одном источнике, — как они написаны в ответе.

    Пустой список — всё подтверждено (или проверять было нечего). Источники —
    тексты статей, которые видела модель. Слова человека (user_texts) подтверждают
    только его идентификаторы: номер его контракта в ответе — не выдумка. Срок,
    сумму, дату и название из вопроса они не подтверждают: на наводящий «снимут
    через 3 рабочих дня?» модель охотно поддакивает, и выдуманный срок уходил бы
    человеку как найденный в базе.
    """
    found = facts(answer)
    if not found:
        return []
    evidence = _evidence(sources)
    own: _Evidence | None = None
    missing: list[str] = []
    for fact in found:
        if _confirmed(fact, evidence):
            continue
        if fact.kind in _OWN_FACTS:
            own = own or _evidence(user_texts)
            if _confirmed(fact, own):
                continue
        if fact.text not in missing:
            missing.append(fact.text)
    return missing


# --- откат на статью ----------------------------------------------------------

# Основа слова для сравнения пересказа со статьёй — первые пять букв: падежи
# («котировочные» / «котировочную») не мешают, а короткие служебные слова не в счёт.
_STEM = 5
_WORD = re.compile(r"[a-zа-я0-9]+")


def _stems(text: str) -> set[str]:
    return {word[:_STEM] for word in _WORD.findall(_norm(text)) if len(word) > _SHORT_WORD}


def overlap(answer: str, text: str) -> float:
    """Доля слов ответа, которые есть в тексте статьи, 0..1.

    По ней видно, какую из статей пересказывала модель: откатываться, когда
    пересказ не прошёл сверку фактов, надо именно на неё, а не на первую по рангу.
    """
    words = _stems(answer)
    return len(words & _stems(text)) / len(words) if words else 0.0
