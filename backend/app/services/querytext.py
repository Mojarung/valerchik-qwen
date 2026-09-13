"""Запрос человека → текст, по которому ищет база знаний.

Два исправления, которые человек делает в уме, а поиск без них не делает:

1. Сокращения. «ЭП», «КС», «ЛК», «ПП» короче трёх букв и в лексический индекс не
   попадают вовсе — ни из вопроса, ни из статьи. А «ЭЦП», «МЧД», «УПД» в одной
   статье написаны сокращением, в другой полностью, и совпадения нет. Поэтому
   сокращение и его полная форма в любом падеже сводятся к одному понятию
   (concept_of, concepts_in_words) — так их видит лексический индекс, и «ЭП» в
   вопросе встречает «электронную подпись» в статье и наоборот. Векторному
   поиску и реранкеру к вопросу дописываются расшифровки (expand_abbreviations):
   модель понимает слова лучше, чем аббревиатуры.

2. Опечатки. «зарегестрироватся на партале поставщеков» не совпадает ни с одним
   словом базы. Слово, которого нет ни в базе, ни в словаре русского языка,
   заменяется ближайшим словом базы (одна-две правки). Слово из словаря не
   трогаем никогда: «погода» — не опечатка «подгода», и вопрос не про портал
   должен остаться вопросом не про портал, а не превратиться в чужой.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import cache, lru_cache

import snowballstemmer

log = logging.getLogger(__name__)

_WORD = re.compile(r"[а-яёa-z0-9]+(?:-[а-яёa-z0-9]+)?", re.IGNORECASE)
# Слова так, как их режет поиск (retrieval._WORD_RE): без склейки через дефис
_PLAIN_WORD = re.compile(r"[а-яa-z0-9]+")
# Исправляем только русские слова: латиницу словарь русского языка не знает, и
# без этой защиты любое английское слово вне базы («firefox», «google»)
# «исправлялось» бы в ближайшее латинское слово статей.
_CYRILLIC = re.compile(r"[а-я]+")

# Сокращения предметной области: как их пишут в обращениях и как они звучат
# полностью. Источник — регламент портала (раздел 1.1 «Сокращения») и сами
# обращения СТП. Понятия по таблице собираются один раз на процесс: после её
# правки на лету нужен _concepts.cache_clear().
DOMAIN_ABBREVIATIONS: dict[str, tuple[str, ...]] = {
    "эп": ("электронная подпись",),
    "эцп": ("электронная подпись",),
    "кэп": ("квалифицированная электронная подпись",),
    "укэп": ("усиленная квалифицированная электронная подпись",),
    "нэп": ("неквалифицированная электронная подпись",),
    "мчд": ("машиночитаемая доверенность",),
    "кс": ("котировочная сессия",),
    "сте": ("стандартная товарная единица",),
    "упд": ("универсальный передаточный документ",),
    "укд": ("универсальный корректировочный документ",),
    "еис": ("единая информационная система",),
    "еаист": ("единая автоматизированная информационная система торгов",),
    "лк": ("личный кабинет",),
    "змо": ("закупка малого объема",),
    "пп": ("портал поставщиков",),
    "эдо": ("электронный документооборот",),
    "окпд": ("классификатор продукции",),
    "окпд2": ("классификатор продукции",),
    "ктру": ("каталог товаров работ услуг",),
    "кпгз": ("классификатор предметов государственного заказа",),
    "скк": ("служба контроля качества",),
    "стп": ("служба технической поддержки",),
    "рис": ("региональная информационная система",),
    "внис": ("внешняя информационная система",),
    "гар": ("государственный адресный реестр",),
    "нмцк": ("начальная максимальная цена контракта",),
    "гк": ("государственный контракт",),
    "доп": ("дополнительное соглашение",),
    "ду": ("дополнительное соглашение",),
    "кпп": ("код причины постановки",),
}

# Синонимы действия: слова, которыми называют одно и то же действие в разных
# статьях и обращениях, но которые лексический поиск без этого считает разными
# словами («выгрузить» и «скачать» не пересекаются даже префиксом, живой пример
# — «9.1.1 Выгрузка оферт» обходила по kw-score статью «Где можно скачать
# XML‑версию УПД?» в выдаче на запрос «выгрузить УПД», а верная статья вообще
# не попадала в топ‑5). В отличие от сокращений выше, здесь нет инвариантного
# «ключа»: русские видовые пары (выгрузить/выгружать, скачать/скачивать) и
# глагол на -овать (экспортировать) расходятся корнем при спряжении сильнее,
# чем грубый префиксный стемминг сводит одной основой, — поэтому каждый член
# группы прогоняется через ту же стеммингованную проверку полной формы, что и
# расшифровки сокращений (_phrase_stems/by_start/forms), а не через точное
# сравнение слова.
#
# Ключ группы — не одно из слов, а отдельная метка («_download»). Если бы ключом
# было «выгрузить», как у сокращений (там ключ — сама абревиатура), align_concepts
# принял бы токен-понятие за «полную форму понятия», ведь он начинается с той же
# основы «выгру», что и разобранное слово «выгрузить»: понятие вычищалось бы из
# обеих сторон сравнения (query и заголовок статьи) как «уже сказанное словами»,
# и title_match — 75% веса лексической уверенности (TITLE_SHARE в retrieval.py) —
# не получал(а) бы никакой прибавки от синонима именно там, где выгода нужнее
# всего. Синтетическая метка ни с одной основой не совпадает — коллизии нет.
SYNONYM_GROUPS: dict[str, tuple[str, ...]] = {
    "_download": ("выгрузить", "скачать", "скачивать", "экспортировать", "экспорт"),
}

# Короче — слишком много соседей на расстоянии одной буквы («сеть»/«сеть»): такие
# слова не исправляем, их опечатка неотличима от другого слова.
MIN_FIX_LEN = 5
# Длиннее — допускаем две правки: «зарегестрироватся» от «зарегистрироваться»
# отделяют ровно две, а длинное слово с двумя правками ещё не становится другим.
# С девяти букв (как было) две правки на выгрузке СТП примерно в половине
# случаев давали другое слово: «нерешение → нарушение», «выйгранной →
# выбранной», «перезайти → перейти». Не исправить опечатку — значит не найти
# статью; исправить в чужое слово — уверенно найти не ту.
TWO_EDITS_FROM = 11
# Разговорные слова портала, которых нет ни в словаре русского языка, ни в
# статьях: «учетка», «перезайти», «подвисает», «принт», «скрин», «задвоение».
# Ближайшее к ним слово базы — всегда другое слово («учетку → учету», «принт →
# принят», «подвисала → подписала»), поэтому по началу слова их не трогаем.
COLLOQUIAL_STEMS = (
    "учетк", "перезай", "перезах", "подвис", "зависа", "принт", "скрин", "задвоен",
    "регит", "зарегит", "залогин", "разлогин", "глюч", "флешк", "виснет",
)  # fmt: skip
# Сколько исправлений помнить. Запросы повторяются («не видит подпись»), а слово
# с опечаткой ищется по всему словарю — это самая дорогая часть нормализации.
_FIX_CACHE_SIZE = 20_000

_RUSSIAN = snowballstemmer.stemmer("russian")


@lru_cache(maxsize=1)
def _morph():  # type: ignore[no-untyped-def]
    """Морфоанализатор один на процесс — тот же, что у словаря мата: словари
    грузятся около секунды и занимают память в каждом процессе."""
    try:
        from app.services.guardrails import morph

        return morph()
    except Exception:  # noqa: BLE001 — без словаря опечатки не исправляем вовсе (Speller.fix_word)
        log.warning("pymorphy3 недоступен: исправление опечаток выключено")
        return None


def is_dictionary_word(word: str) -> bool:
    morph = _morph()
    return bool(morph is not None and morph.word_is_known(word))


def warm() -> None:
    """Загрузить морфологию заранее — иначе её ждал бы первый посетитель,
    написавший слово, которого нет в базе."""
    is_dictionary_word("прогрев")


def expand_abbreviations(text: str, extra: dict[str, tuple[str, ...]] | None = None) -> str:
    """Текст с дописанными расшифровками сокращений (без повторов)."""
    table = DOMAIN_ABBREVIATIONS if not extra else {**DOMAIN_ABBREVIATIONS, **extra}
    found: list[str] = []
    for word in _WORD.findall(text.lower().replace("ё", "е")):
        for expansion in table.get(word, ()):
            if expansion not in found:
                found.append(expansion)
    return f"{text} {' '.join(found)}" if found else text


# --- понятия: сокращение и полная форма — одно понятие для лексического поиска ---

# Основу длиннее укорачиваем на букву (см. _phrase_stems)
_SHORT_STEM = 5
# Сколько букв окончания допускаем после основы: «подп|исями», «сесс|иями»
_MAX_ENDING = 7
# Токены поиска — первые шесть букв слова (retrieval.tokenize)
_TOKEN_PREFIX = 6
# По стольким первым буквам слова ищутся полные формы, которые с него начинаются
_START = 3


def _phrase_stems(phrase: str) -> tuple[str, ...]:
    stems = []
    for word in phrase.split():
        stem = _RUSSIAN.stemWord(word)
        # Основа snowball режет по границе окончания одной формы: «закупк» из
        # «закупка» не встречает «закупок». Длинную основу укорачиваем на букву, а
        # короткую («подп», «сесс») не трогаем — «под» совпало бы с «подачей».
        stems.append(stem[:-1] if len(stem) > _SHORT_STEM else stem)
    return tuple(stems)


_Phrase = tuple[str, tuple[str, ...]]  # (ключ понятия, основы слов полной формы)


@dataclass(frozen=True)
class _Concepts:
    by_word: dict[str, str]  # сокращение → ключ понятия
    # Первые буквы первого слова → полные формы, которые с него начинаются.
    # Почти ни одно слово статьи не начинает полную форму, и поиск по тексту —
    # это одна проверка словаря на слово.
    by_start: dict[str, tuple[_Phrase, ...]]
    # Ключ понятия → основы каждой его полной формы, обрезанные до длины токена
    forms: dict[str, tuple[tuple[str, ...], ...]]
    # Ключи, которые вытесняют слова своей полной формы из индекса, а не
    # приписываются рядом с ними (SYNONYM_GROUPS). У сокращения полная форма —
    # то же самое слово в другом падеже, и оставить его полезно: «подпись» без
    # «электронной» тоже находит статью про ЭП. У синонимов действия слова
    # группы — разные слова, и оставленное «выгруз» тянет за собой все статьи
    # про выгрузку чего угодно, перевешивая ключ вдвое (см. index_tokens).
    replacing: frozenset[str]


@cache
def _concepts() -> _Concepts:
    """Ключ понятия — первое сокращение с этой расшифровкой: «эцп» и «электронная
    подпись» сводятся к «эп», «окпд2» — к «окпд»."""
    by_word: dict[str, str] = {}
    by_phrase: dict[str, str] = {}
    for abbreviation, expansions in DOMAIN_ABBREVIATIONS.items():
        key = next((by_phrase[e] for e in expansions if e in by_phrase), abbreviation)
        by_word[abbreviation] = key
        for expansion in expansions:
            by_phrase.setdefault(expansion, key)
    # Синонимы действия — то же дерево (by_start/forms), но без слова-ключа в
    # by_word: ни один член группы не инвариантен, все проходят через ту же
    # стеммингованную проверку, что и полные формы сокращений выше.
    replacing: set[str] = set()
    for key_hint, phrases in SYNONYM_GROUPS.items():
        key = next((by_phrase[phrase] for phrase in phrases if phrase in by_phrase), key_hint)
        replacing.add(key)
        for phrase in phrases:
            by_phrase.setdefault(phrase, key)
    by_start: dict[str, list[_Phrase]] = {}
    forms: dict[str, list[tuple[str, ...]]] = {}
    for phrase, key in by_phrase.items():
        stems = _phrase_stems(phrase)
        by_start.setdefault(stems[0][:_START], []).append((key, stems))
        forms.setdefault(key, []).append(tuple(stem[:_TOKEN_PREFIX] for stem in stems))
    return _Concepts(
        by_word,
        {start: tuple(phrases) for start, phrases in by_start.items()},
        {key: tuple(prefixes) for key, prefixes in forms.items()},
        frozenset(replacing),
    )


def concept_of(word: str) -> str | None:
    """Ключ понятия, если слово — известное сокращение («эцп» → «эп»); иначе None.

    Слово — в нижнем регистре и с «е» вместо «ё», как его режет поиск.
    """
    return _concepts().by_word.get(word)


def _fits(word: str, stem: str) -> bool:
    return word.startswith(stem) and len(word) - len(stem) <= _MAX_ENDING


def replacing_concepts() -> frozenset[str]:
    """Ключи понятий, вытесняющих слова своей полной формы (_Concepts.replacing)."""
    return _concepts().replacing


def concept_spans(words: Sequence[str]) -> list[tuple[str, int, int]]:
    """Понятия, названные полностью: (ключ, индекс первого слова, сколько слов).

    Слова — подряд, как в тексте, в нижнем регистре и с «е» вместо «ё». Полная
    форма находится в любом падеже («электронной подписью», «личного кабинета»),
    а вложенные понятия — все: «усиленная квалифицированная электронная подпись»
    — это и УКЭП, и КЭП, и ЭП, поэтому спаны вкладываются и пересекаются.

    Позиции нужны только тому, кто слова понятия вытесняет (index_tokens);
    остальным хватает ключей — concepts_in_words.
    """
    by_start = _concepts().by_start
    found: list[tuple[str, int, int]] = []
    for i, word in enumerate(words):
        for key, stems in by_start.get(word[:_START], ()):
            if i + len(stems) <= len(words) and all(
                _fits(words[i + j], stem) for j, stem in enumerate(stems)
            ):
                found.append((key, i, len(stems)))
    return found


def concepts_in_words(words: Sequence[str]) -> list[str]:
    """Ключи понятий, названных полностью, — по одному на упоминание."""
    return [key for key, _, _ in concept_spans(words)]


def phrase_concepts(text: str) -> list[str]:
    """То же, что concepts_in_words, по тексту."""
    return concepts_in_words(_PLAIN_WORD.findall(text.lower().replace("ё", "е")))


def _spelled_out(tokens: set[str], forms: tuple[tuple[str, ...], ...]) -> set[str]:
    """Токены, которыми понятие написано полностью; пусто — полной формы нет."""
    for prefixes in forms:
        matched = [{t for t in tokens if t.startswith(prefix)} for prefix in prefixes]
        if all(matched):
            return set().union(*matched)
    return set()


def align_concepts(a: set[str], b: set[str]) -> tuple[set[str], set[str]]:
    """Одно понятие — одинаково с обеих сторон и один раз.

    Наборы — токены поиска (retrieval.index_tokens): у понятия, названного
    полностью, в наборе и его слова, и ключ. Сравнивать так нельзя:

    - где понятие с обеих сторон написано словами, ключ их дублирует —
      «электронная подпись» против «электронной подписи» совпадала бы трижды
      вместо двух раз. Здесь ключ убираем, остаются слова — как до понятий;
    - где хотя бы с одной стороны сокращение, обе стороны сводятся к одному
      ключу: слова полной формы уходят, ключ остаётся. Понятие весит одинаково,
      как бы его ни записали, — idf ключа, как у «УПД» против «УПД».

    Синонимов действия (SYNONYM_GROUPS) это не касается: их слова index_tokens
    вытесняет ключом ещё при токенизации, так что в наборах от них только ключ и
    _spelled_out ничего не находит — работает вторая ветка, как у сокращений.

    Раньше сторона с сокращением получала слова полной формы другой стороны, и
    одно понятие весило два-четыре слова, да ещё в точной падежной форме
    заголовка. Одного общего понятия и одного общего слова хватало на порог
    ответа: «в КС указаны не корректные размеры» уверенно получал «Что такое
    котировочная сессия?», а вопрос с «КС» набирал больше, чем тот же вопрос,
    написанный полностью.
    """
    forms = _concepts().forms
    keys = [key for key in a | b if key in forms]
    if not keys:
        return a, b
    a, b = set(a), set(b)
    for key in keys:
        a_words = _spelled_out(a, forms[key]) if key in a else set()
        b_words = _spelled_out(b, forms[key]) if key in b else set()
        # Ключ без слов полной формы — значит, понятие названо сокращением
        abbreviated = (key in a and not a_words) or (key in b and not b_words)
        if abbreviated:
            a -= a_words
            b -= b_words
        else:
            if a_words:
                a.discard(key)
            if b_words:
                b.discard(key)
    return a, b


# --- опечатки ---


def _letters(word: str) -> int:
    """Набор букв слова битовой маской.

    Одна правка меняет набор букв не больше чем на две (замена убирает одну и
    добавляет другую, перестановка не меняет ничего), поэтому слова, чьи наборы
    расходятся больше чем на 2·k букв, не бывают ближе k правок. Такой отсев —
    одна операция над int — отбрасывает почти весь словарь, не считая расстояний.
    Русские буквы ложатся на разные биты; латиница может совпасть с ними, но
    совпадение лишь ослабляет отсев и никогда не теряет кандидата.
    """
    mask = 0
    for ch in word:
        mask |= 1 << (ord(ch) & 63)
    return mask


def _distance_at_most(a: str, b: str, limit: int) -> int | None:
    """Расстояние Дамерау–Левенштейна, если оно не больше limit; иначе None.

    Считается только полоса шириной 2·limit+1 вокруг диагонали: клетка дальше от
    неё стоит больше limit правок, а на пути к ответу не дороже limit все клетки
    не дороже limit — значит, лежат в полосе.
    """
    n, m = len(a), len(b)
    if abs(n - m) > limit:
        return None
    over = limit + 1  # «больше limit» — точное значение за полосой не нужно
    previous_previous: list[int] | None = None
    previous = [j if j <= limit else over for j in range(m + 1)]
    for i in range(1, n + 1):
        current = [over] * (m + 1)
        current[0] = i if i <= limit else over
        best = current[0]
        ca = a[i - 1]
        for j in range(max(1, i - limit), min(m, i + limit) + 1):
            cb = b[j - 1]
            value = previous[j - 1] + (ca != cb)
            if previous[j] + 1 < value:
                value = previous[j] + 1
            if current[j - 1] + 1 < value:
                value = current[j - 1] + 1
            if (
                previous_previous is not None
                and j > 1
                and ca == b[j - 2]
                and a[i - 2] == cb
                and previous_previous[j - 2] + 1 < value
            ):
                value = previous_previous[j - 2] + 1
            current[j] = min(value, over)
            best = min(best, current[j])
        if best > limit:
            return None
        previous_previous, previous = previous, current
    return previous[m] if previous[m] <= limit else None


@dataclass
class _Vocabulary:
    counts: Counter[str]
    # (первая буква, длина) → русские слова базы и их наборы букв
    buckets: dict[tuple[str, int], list[tuple[str, int]]]
    # Кэш исправлений живёт вместе со словарём: новая база — новый кэш
    fixes: dict[str, str] = field(default_factory=dict)


class Speller:
    """Исправление опечаток по словарю самой базы знаний.

    Словарь — слова статей с частотами: исправлять имеет смысл только в сторону
    слов, которые в базе есть, иначе исправление ничего не найдёт. Кандидаты
    ищутся среди слов той же первой буквы и близкой длины: опечатка в первой
    букве редка, а без этого ограничения короткие слова «исправлялись» во что
    попало.
    """

    def __init__(self) -> None:
        self._vocab = _Vocabulary(Counter(), {})

    def build(self, texts: Iterable[str]) -> None:
        counts: Counter[str] = Counter()
        for text in texts:
            counts.update(
                w for w in _WORD.findall(text.lower().replace("ё", "е")) if len(w) >= MIN_FIX_LEN - 1
            )
        buckets: dict[tuple[str, int], list[tuple[str, int]]] = {}
        for word in counts:
            if _CYRILLIC.fullmatch(word):
                buckets.setdefault((word[0], len(word)), []).append((word, _letters(word)))
        # Одна подмена целиком: поиск не должен увидеть новые частоты со старыми
        # кандидатами или чужим кэшем.
        self._vocab = _Vocabulary(counts, buckets)

    def known(self, word: str) -> bool:
        return word in self._vocab.counts

    def fix_word(self, word: str) -> str:
        vocab = self._vocab
        low = word.lower().replace("ё", "е")
        if len(low) < MIN_FIX_LEN or low in vocab.counts or not _CYRILLIC.fullmatch(low):
            return word
        if low.startswith(COLLOQUIAL_STEMS):
            return word
        # Без морфологии не отличить опечатку от настоящего слова: «погода» ушла
        # бы в «подгода». Лучше не исправлять ничего, чем исправлять всё подряд.
        if _morph() is None:
            return word
        fixed = vocab.fixes.get(low)
        if fixed is None:
            fixed = low if is_dictionary_word(low) else _nearest(vocab, low)
            if len(vocab.fixes) >= _FIX_CACHE_SIZE:
                vocab.fixes.clear()
            vocab.fixes[low] = fixed
        return word if fixed == low else fixed

    def correct(self, text: str) -> str:
        """Текст с исправленными опечатками; слова вне словаря базы остаются как есть."""
        if not self._vocab.counts:
            return text
        return _WORD.sub(lambda m: self.fix_word(m.group(0)), text)


def _nearest(vocab: _Vocabulary, low: str) -> str:
    """Ближайшее слово словаря или само слово, если близких нет.

    Лучший кандидат — меньше правок, затем чаще в базе, затем по алфавиту. Поэтому
    сначала ищем на одну правку и только если таких нет — на две: результат тот
    же, что у общего перебора, а длинное слово почти всегда находится с первого
    прохода, где отсев по буквам вдвое строже.
    """
    mask = _letters(low)
    for limit in range(1, (2 if len(low) >= TWO_EDITS_FROM else 1) + 1):
        best: tuple[int, int, str] | None = None
        for length in range(len(low) - limit, len(low) + limit + 1):
            for candidate, letters in vocab.buckets.get((low[0], length), ()):
                if (letters ^ mask).bit_count() > 2 * limit:
                    continue
                distance = _distance_at_most(low, candidate, limit)
                if distance is None:
                    continue
                rank = (distance, -vocab.counts[candidate], candidate)
                if best is None or rank < best:
                    best = rank
        if best:
            return best[2]
    return low


def normalize_query(text: str, speller: Speller | None) -> str:
    """Опечатки → расшифровки сокращений: запрос для векторного поиска и реранкера."""
    corrected = speller.correct(text) if speller else text
    return expand_abbreviations(corrected)
