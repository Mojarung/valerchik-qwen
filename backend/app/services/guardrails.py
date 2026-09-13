"""Guardrails: нецензурная лексика, токсичность, prompt-инъекции, маскирование ПДн.

Детектор мата устойчив к обфускации: гомоглифы латиницей/цифрами (х→x, о→0),
разделители внутри слова (х*у_й), побуквенное написание («х у й»), ё/е, невидимые
символы-разделители, растянутые буквы («хуууй») и набор в латинской раскладке («[eq»).
Списки корней и исключений пополняются из админ-панели на лету (dynamic settings).

Отдельный слой — токсичность без мата: «побрей своё очко», «ты дебил», «заткнись»
не содержат матерных корней, но это ровно то же нарушение правил общения. Оценка
взвешенная, а не бинарная: прямые оскорбления и грубая анатомическая лексика весят
сами по себе, а слова, грубые только в адресном употреблении («ты баран» против
«баранина оптом»), добираются до порога лишь вместе с обращением на «ты».
"""

import re
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

from app.services.layout import convert_layout

# --- нормализация ---

_HOMOGLYPHS = str.maketrans(
    {
        "a": "а",
        "b": "б",
        "c": "с",
        "e": "е",
        "h": "н",
        "k": "к",
        "m": "м",
        "n": "п",
        "o": "о",
        "p": "р",
        "r": "г",
        "s": "с",
        "t": "т",
        "u": "у",
        "x": "х",
        "y": "у",
        "0": "о",
        "3": "з",
        "4": "ч",
        "6": "б",
        "@": "а",
        "$": "с",
        "ё": "е",
    }
)

_NON_LETTER = re.compile(r"[^а-яa-z0-9@$ё]+")
_INNER_SEP = re.compile(r"(?<=\w)[*.\-_+'`\"]+(?=\w)")
# Невидимые символы — самый дешёвый способ обойти любой словарный детектор:
# «х​уй» глазом неотличим от слова, а для регулярки это два разных токена.
_INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
# «хуууй»: три и более одинаковых буквы подряд в русском не встречаются
_STRETCHED = re.compile(r"(.)\1{2,}")


def normalize(text: str) -> str:
    """нижний регистр → склейка разорванных слов → гомоглифы → только буквы."""
    t = _INVISIBLE.sub("", text.lower())
    t = _INNER_SEP.sub("", t)  # "х*у.й" -> "хуй"
    t = t.translate(_HOMOGLYPHS)
    t = _NON_LETTER.sub(" ", t)
    t = _STRETCHED.sub(r"\1", t)  # "хуууй" -> "хуй"
    return t.strip()


def _normalize_wild(text: str) -> str:
    """Как normalize, но сепараторы внутри слова становятся '*' (цензура: «бл*дь»)."""
    t = _INVISIBLE.sub("", text.lower())
    t = _INNER_SEP.sub("*", t)
    t = t.translate(_HOMOGLYPHS)
    t = re.sub(r"[^а-яa-z0-9@$ё*]+", " ", t)
    return t.strip()


# Побуквенная склейка («х у й») — от двух одиночных букв подряд
_MIN_SPELLED_RUN = 2


def _tokens(norm: str) -> tuple[list[str], list[str]]:
    """-> (обычные слова, склейки побуквенных написаний «х у й» -> «хуй»)."""
    words = norm.split()
    plain: list[str] = []
    joined: list[str] = []
    run: list[str] = []
    for w in words:
        if len(w) == 1:
            run.append(w)
            continue
        if len(run) >= _MIN_SPELLED_RUN:
            joined.append("".join(run))
        run = []
        plain.append(w)
    if len(run) >= _MIN_SPELLED_RUN:
        joined.append("".join(run))
    return plain, joined


# Базовые корни (пополняются из настроек guardrails.profanity_extra_roots)
BASE_ROOTS = [
    "хуй",
    "хуя",
    "хуе",
    "хуи",
    # «хер» ловит «какого хера», «похер», «охереть»; херес, херувим и Херсон — в исключениях
    "хер",
    "пизд",
    "пизж",
    "ебан",
    "ебат",
    "ебал",
    "ебун",
    "ебуч",
    "ебну",
    "ебыв",
    "ебар",
    "ебис",
    "ебош",
    "бля",
    "блят",
    "бляд",
    "сука",
    "суки",
    "сучар",
    # «мудо» — это мудозвон, мудохать и родня. Корень «муд» брать нельзя:
    # он забирает мудрость, мудреца и мудрый.
    "мудак",
    "мудач",
    "мудил",
    "мудо",
    "долбоеб",
    "далбаеб",
    "пидор",
    "пидар",
    "пидр",
    "гандон",
    "гондон",
    "залуп",
    "мразь",
    "мрази",
    "уебок",
    "уебан",
    "уебищ",
]

# Ловим латинскую транслитерацию по сырому тексту (диграфы ya/yu не покрываются гомоглифами)
LATIN_ROOTS = ["blyad", "blyat", "suka", "pizd", "ebat", "ebal", "huyn", "nahui", "nahuy", "pidor", "mudak"]

PREFIXES = [
    "за",
    "вы",
    "до",
    "недо",
    "от",
    "по",
    "под",
    "пере",
    "при",
    "раз",
    "рас",
    "с",
    "у",
    "на",
    "вз",
    "об",
    "из",
    "ис",
    "о",
    "в",
    "не",
    "ах",
    "ох",
]

BASE_EXCEPTIONS = {
    "себя",
    "тебя",
    "истребитель",
    "употреблять",
    "употребление",
    "оскорбление",
    "рубля",
    "рублях",
    "корабля",
    "гребля",
    "стебля",
    "заблуждение",
    "бляха",
    "бляхи",
    "бляшка",
    "бляшки",
    "бляшек",
    "команда",
    "команде",
    "командах",
    "хулиган",
    "хулиганство",
    "скипидар",
    "херес",
    "херувим",
    "херсон",
}

# --- токсичность без мата ---
# Веса подобраны под порог 1.0: одного слова из первых двух списков достаточно,
# «неоднозначным» нужна адресность. Пополняется из админки на лету.

# Переход на личности: оскорбительны сами по себе, легального употребления в
# обращении в поддержку нет.
INSULT_ROOTS = [
    "дебил",
    "идиот",
    "кретин",
    "тупиц",
    "тупарь",
    "тупор",
    "придур",
    "дурак",
    "дурач",
    "дурень",
    "болван",
    "олигофрен",
    "дегенерат",
    "имбецил",
    "ничтожеств",
    "чмо",
    "чмыр",
    "урод",
    "ублюд",
    "выродок",
    "тварь",
    "твари",
    "гнид",
    "лох",
    "лошар",
    "быдло",
    "хамло",
    "жлоб",
    "бездар",
    "рукожоп",
    "дармоед",
    "тупиц",
    "клоун",
    "клоунад",
    "паразит",
    "падл",
    "сволоч",
    "негодя",
    "мерзав",
    "хамьё",
    "хамье",
]

# Грубая анатомическая и сниженная лексика без матерных корней.
VULGAR_ROOTS = [
    "очко",
    "жоп",
    "задниц",
    "срак",
    "хлебал",
    "харя",
    "хари",
    "мурло",
    "падаль",
    "нахрен",
    "нафиг",
    "похрен",
    "похер",
    "дерьм",
    "говн",
    "срач",
    "блев",
]

# Грубы только в адресном употреблении: «ты баран» — оскорбление, «баранина оптом» —
# позиция закупки. Без обращения на «ты» до порога не дотягивают.
AMBIGUOUS_ROOTS = [
    "хрен",
    "хер",
    "туп",
    "морд",
    "свин",
    "козел",
    "козл",
    "баран",
    "осел",
    "ослин",
    "скотин",
    "корова",
    "жирдя",
    "тормоз",
    "бестолоч",
    "безмозгл",
    "неуч",
]

# Посылы, приказы замолчать, пожелания вреда — считаем по нормализованному тексту.
AGGRESSION_PATTERNS = [
    r"\bзаткнис",
    r"\bзаткни\s+(свой\s+)?(рот|пасть|хлебало)",
    r"\bзавали\b",
    r"\bотвали\b",
    r"\bотвянь\b",
    r"\bотстань\s+от\s+меня\b",
    r"\b(сдохни|сдохните|подохни|убейся|удавись|повесься|застрелис)\b",
    r"\b(иди|идите|пошел|пошла|пошли|катис|вали|свали)\s+"
    r"(нахрен|нафиг|на\s+хер|лесом|в\s+баню|к\s+черту|в\s+жопу|в\s+зад|на\s+все\s+четыре)\b",
    r"\bпошел\s+ты\b",
    r"\bчтоб\s+ты\s+(сдох|провалилс|подавилс)",
]
_AGGRESSION_RE = re.compile("|".join(AGGRESSION_PATTERNS))

# Обращение на «ты» / указание на собеседника — множитель адресности.
_ADDRESSING = {
    "ты",
    "тебя",
    "тебе",
    "тобой",
    "твой",
    "твоя",
    "твое",
    "твои",
    "твоего",
    "твоей",
    "твоих",
    "свое",
    "свою",
    "свой",
    "свои",
    "себе",
    "вы",
    "вас",
    "вам",
    "ваш",
    "ваша",
    "ваше",
    "ваши",
}

# Совпадения по корню, за которыми стоят обычные товары и услуги: «10 очков защитных»
# (род. п. от «очки») не должен ловиться корнем «очко», «свинина» — корнем «свин».
BASE_TOXICITY_EXCEPTIONS = {
    "очков",
    "очковый",
    "очковая",
    "очковое",
    "очковые",
    "очковых",
    "очковыми",
    "очкового",
    "очковтирательство",
    "свинина",
    "свинины",
    "свинине",
    "свининой",
    "свиной",
    "свиная",
    "свиные",
    "свиных",
    "баранина",
    "баранины",
    "баранине",
    "бараниной",
    "барания",
    "коровье",
    "коровьего",
    "коровы",
    "морда",
    "мордовия",
    "мордовии",
    "тупик",
    "тупика",
    "тупике",
    "тормозной",
    "тормозная",
    "тормозные",
    "тормозных",
    "тормозов",
    "тормоза",
    "хренов",  # «хреновина» — не о собеседнике
    "паразитный",
    "паразитная",
    "клоунский",
}

_WEIGHT_INSULT = 1.0
_WEIGHT_VULGAR = 1.0
_WEIGHT_AMBIGUOUS = 0.6
_WEIGHT_AGGRESSION = 1.0
_BONUS_ADDRESSED = 0.4

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|previous\s+|prior\s+)+(instructions|prompts)",
    r"disregard (the )?(system|previous)",
    r"act as ",
    r"jailbreak",
    r"developer mode",
    r"забудь (все|предыдущие|свои) (инструкции|указания|правила)",
    r"игнорируй (все|предыдущие|системные)",
    r"ты (теперь|больше не) (не )?(ассистент|бот|помощник)",
    r"(покажи|выведи|раскрой) (свой |системный )?(промпт|инструкции)",
    r"системное сообщение:",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

# --- ПДн ---
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"), "[телефон]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email]"),
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "[карта]"),
    (re.compile(r"\b\d{3}-\d{3}-\d{3}[\s-]?\d{2}\b"), "[СНИЛС]"),
    (re.compile(r"\bИНН\s*:?\s*\d{10,12}\b", re.IGNORECASE), "ИНН [скрыт]"),
]


@dataclass
class ProfanityVerdict:
    hit: bool
    matched: list[str] = field(default_factory=list)


def _word_matches_root(word: str, root: str) -> bool:
    if word.startswith(root):
        return True
    return any(word.startswith(p) and word[len(p) :].startswith(root) for p in PREFIXES)


_EXCEPTION_ENDING = re.compile(r"[аяиыеоуюэ]{1,2}$")


_MIN_STEM_LEN = 4  # основу короче не режем — см. _exception_stem


def _exception_stem(word: str) -> str:
    """Исключение без окончания: «бляшка» -> «бляшк», «команда» -> «команд».

    Короче четырёх букв основу не режем — «себя» дало бы «себ», и под него попало
    бы слишком многое.
    """
    stem = _EXCEPTION_ENDING.sub("", word)
    return stem if len(stem) >= _MIN_STEM_LEN else word


def _excepted(word: str, exceptions: set[str]) -> bool:
    """Слово из списка исключений и всё, что от него образовано.

    Список ведётся руками и содержит отдельные словоформы, поэтому «бляшкой»
    отличалось от «бляшка» на одну букву — и обращение закрывалось автоматически,
    хотя «бляшка» в исключениях есть. По той же причине исключения не работали для
    слов через дефис: нормализация склеивает «бляшка-имплант» в одно слово ЕЩЁ ДО
    этой проверки, и в списке его, разумеется, нет.

    Сравнение по основе снимает оба случая разом и заодно перестаёт требовать от
    администратора перечислять все падежи руками.
    """
    if word in exceptions:
        return True
    return any(word.startswith(_exception_stem(e)) for e in exceptions)


# Слова и корни короче трёх букв не сверяем: ложных совпадений больше, чем пойманных
_MIN_CHECKED_LEN = 3


def _wildcard_hit(word: str, roots: list[str]) -> bool:
    """«бл*дь»: '*' — любой символ; корень должен совпасть с началом слова."""
    if "*" not in word:
        return False
    for root in roots:
        if len(root) < _MIN_CHECKED_LEN or len(word) < len(root):
            continue
        pattern = word[: len(root)].replace("*", ".")
        if "." in pattern and re.fullmatch(pattern, root):
            return True
    return False


def check_profanity(
    text: str,
    extra_roots: list[str] | None = None,
    extra_exceptions: list[str] | None = None,
    check_layout: bool = True,
) -> ProfanityVerdict:
    roots = BASE_ROOTS + [r.lower() for r in (extra_roots or [])]
    exceptions = BASE_EXCEPTIONS | {e.lower() for e in (extra_exceptions or [])}
    matched: list[str] = []

    plain, joined = _tokens(normalize(text))
    for word in plain:
        if len(word) < _MIN_CHECKED_LEN or _excepted(word, exceptions):
            continue
        if any(_word_matches_root(word, root) for root in roots):
            matched.append(word)
    # побуквенные склейки («х у й», «и с у к а») — корень ищем как подстроку
    matched.extend(
        word
        for word in joined
        if len(word) >= _MIN_CHECKED_LEN
        and not _excepted(word, exceptions)
        and any(root in word for root in roots if len(root) >= _MIN_CHECKED_LEN)
    )
    # цензурированные формы со звёздочками
    matched.extend(word for word in _normalize_wild(text).split() if _wildcard_hit(word, roots))

    raw = text.lower()
    matched.extend(root for root in LATIN_ROOTS if re.search(rf"(?<![a-z]){re.escape(root)}", raw))

    # Мат, набранный в латинской раскладке («[eq» = «хуй»), — иначе детектор обходится
    # одним нажатием Alt+Shift. Конвертируем только сплошную латиницу: внутри русской
    # фразы случайное английское слово после перевода может дать ложный корень.
    # Обратная сторона — редкие английские слова, которые сами переводятся в корень
    # (например «cerf» → «сука»); на этот случай проверка отключается настройкой.
    if check_layout and _mostly_latin(text):
        converted = convert_layout(text)
        if converted != text:
            for word in normalize(converted).split():
                if len(word) < _MIN_CHECKED_LEN or _excepted(word, exceptions):
                    continue
                if any(_word_matches_root(word, root) for root in roots):
                    matched.append(word)

    return ProfanityVerdict(hit=bool(matched), matched=sorted(set(matched)))


@dataclass
class ToxicityVerdict:
    hit: bool
    score: float = 0.0
    matched: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)


def check_toxicity(
    text: str,
    threshold: float = 1.0,
    extra_terms: list[str] | None = None,
    extra_exceptions: list[str] | None = None,
) -> ToxicityVerdict:
    """Оскорбления и грубость без матерных корней. Взвешенная оценка против порога.

    Нормализация общая с детектором мата, поэтому обфускации («д*еб*ил», «ты т у п о й»,
    гомоглифы) отсекаются здесь бесплатно.
    """
    norm = normalize(text)
    plain, joined = _tokens(norm)
    exceptions = BASE_TOXICITY_EXCEPTIONS | {e.lower() for e in (extra_exceptions or [])}
    insults = INSULT_ROOTS + [t.lower() for t in (extra_terms or [])]
    tiers = (
        (insults, _WEIGHT_INSULT, "insult"),
        (VULGAR_ROOTS, _WEIGHT_VULGAR, "vulgar"),
        (AMBIGUOUS_ROOTS, _WEIGHT_AMBIGUOUS, "ambiguous"),
    )

    score = 0.0
    matched: list[str] = []
    categories: list[str] = []
    for word in plain + joined:
        if word in exceptions or len(word) < _MIN_CHECKED_LEN:
            continue
        for roots, weight, category in tiers:
            if any(_word_matches_root(word, root) for root in roots):
                score += weight
                matched.append(word)
                categories.append(category)
                break

    aggression = _AGGRESSION_RE.search(norm)
    if aggression:
        score += _WEIGHT_AGGRESSION
        matched.append(aggression.group(0).strip())
        categories.append("aggression")

    # Адресность добирает «неоднозначные» слова до порога: «ты баран» — оскорбление,
    # «баран» в перечне позиций — нет. Сама по себе, без грубых слов, ничего не значит.
    if score > 0 and any(word in _ADDRESSING for word in plain):
        score += _BONUS_ADDRESSED
        categories.append("addressed")

    return ToxicityVerdict(
        hit=score >= threshold,
        score=round(score, 2),
        matched=sorted(set(matched)),
        categories=sorted(set(categories)),
    )


# --- словарь производных мата и жаргона ---
# Корни выше ловят основу («пизд», «ебан»), но у мата тысячи производных с другими
# основами — «мандохать», «хитрожопый», «дрочить». Перечислить их корнями нельзя:
# короткая основа забирает обычные слова. Поэтому — словарь целых слов, собранный
# scripts/build_profanity_lexicon.py из открытых списков, и pymorphy3, который
# приводит слово к начальной форме: «вымандохивали» находится по «вымандохивать».

# Не в папке data/: её целиком игнорирует .gitignore (там данные lite-стенда),
# и словарь молча не попадал бы в репозиторий, а без файла проверка падала бы на каждой реплике.
_LEXICON_PATH = Path(__file__).parent / "lexicons" / "profanity.txt"


@cache
def _lexicon() -> frozenset[str]:
    lines = _LEXICON_PATH.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))


@cache
def morph() -> Any:
    """Морфоанализатор, один на процесс: им пользуются и словарь мата, и
    исправление опечаток (services/querytext.py). Два экземпляра — вдвое больше
    памяти в каждом процессе и вдвое дольше загрузка."""
    # Импорт здесь, а не наверху: словари грузятся около полусекунды, а guardrails
    # импортирует каждый процесс, включая воркер, которому словарь не нужен.
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=20_000)
def _lemma(word: str) -> str:
    return str(morph().parse(word)[0].normal_form).replace("ё", "е")


def warm_lexicon() -> None:
    """Загрузить словарь и морфологию заранее, чтобы первая реплика не ждала их."""
    _lexicon()
    _lemma("прогрев")


def check_lexicon(text: str, extra_exceptions: list[str] | None = None) -> ToxicityVerdict:
    """Производные мата и жаргон, которых корни не знают.

    Слово сверяется и как написано, и в начальной форме — так находится любой
    падеж. Словарь собран из списков нецензурной лексики, поэтому и действует
    как фильтр мата: исключения — те же, что у корней, ложное срабатывание
    админ снимает одним списком.
    """
    lexicon = _lexicon()
    exceptions = BASE_EXCEPTIONS | BASE_TOXICITY_EXCEPTIONS | {e.lower() for e in (extra_exceptions or [])}
    plain, joined = _tokens(normalize(text))
    matched = sorted(
        {
            word
            for word in plain + joined
            if len(word) >= _MIN_CHECKED_LEN
            and not _excepted(word, exceptions)
            and (word in lexicon or _lemma(word) in lexicon)
        }
    )
    return ToxicityVerdict(
        hit=bool(matched),
        score=1.0 if matched else 0.0,
        matched=matched,
        categories=["lexicon"] if matched else [],
    )


def detect_profanity(
    text: str,
    extra_roots: list[str] | None = None,
    extra_exceptions: list[str] | None = None,
    *,
    check_layout: bool = True,
    lexicon: bool = True,
) -> ProfanityVerdict | ToxicityVerdict:
    """Мат целиком: корни с обходами обфускации, затем словарь производных.

    Одна точка для входящих реплик клиента и исходящих реплик оператора — чтобы
    «вымандохивали» не проходило у оператора, когда у клиента уже ловится.
    """
    verdict = check_profanity(text, extra_roots, extra_exceptions, check_layout=check_layout)
    if verdict.hit or not lexicon:
        return verdict
    return check_lexicon(text, extra_exceptions)


# --- вердикт нейросети ---
# «dangerous» («вредит репутации говорящего») не берём вовсе: на замерах жалобы
# без оскорблений — «поставщик мошенник», «оператор нахамил» — получали по ней
# 0.35–0.7, а это законное недовольство, а не грубость. Остальные метки делятся
# по последствиям: «мат» — нецензурная лексика, обращение завершается, как по
# корням; оскорбление и угроза — грубость, сначала предупреждение.
MODEL_OBSCENITY = {"obscenity": "мат"}
MODEL_ABUSE = {"insult": "оскорбление", "threat": "угроза"}
MODEL_LABELS = MODEL_OBSCENITY | MODEL_ABUSE


def model_verdict(
    scores: dict[str, float] | None, threshold: float, labels: dict[str, str] = MODEL_LABELS
) -> ToxicityVerdict:
    """Вероятности меток модели -> вердикт по заданным меткам. None (модель недоступна) — не нарушение."""
    if not scores:
        return ToxicityVerdict(hit=False)
    label, worst = max(((name, scores.get(name, 0.0)) for name in labels), key=lambda item: item[1])
    if worst < threshold:
        return ToxicityVerdict(hit=False, score=round(worst, 2))
    return ToxicityVerdict(
        hit=True,
        score=round(worst, 2),
        matched=[f"нейросеть: {labels[label]} {worst:.2f}"],
        categories=[f"model:{label}"],
    )


# Латиница «в основном» — от трёх латинских букв и вдвое больше, чем кириллицы
_MIN_LATIN_LETTERS = 3
_LATIN_DOMINANCE = 2


def _mostly_latin(text: str) -> bool:
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    cyrillic = sum(1 for ch in text if "а" <= ch.lower() <= "я")
    return latin >= _MIN_LATIN_LETTERS and latin > cyrillic * _LATIN_DOMINANCE


def detect_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text))


def mask_pii(text: str) -> str:
    out = text
    for pattern, repl in _PII_PATTERNS:
        out = pattern.sub(repl, out)
    return out
