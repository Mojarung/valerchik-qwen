"""Автоисправление забытой раскладки клавиатуры: «Rfr pjhtubcnhbhjdfnmcz» → «Как зарегистрироваться».

Решение принимается статистически, по частотным буквенным парам: текст исправляется, только
если после перевода он выглядит как русский, а в исходном виде — не выглядит как осмысленный
английский. Так «hello» или «UPD» остаются нетронутыми, а набранная вслепую фраза чинится.

Тот же конвертер используют guardrails: мат, набранный в латинской раскладке, иначе прошёл бы
мимо детектора.
"""

import re

# QWERTY → ЙЦУКЕН: раскладка совпадает физически, поэтому карта посимвольная
_EN_TO_RU_PAIRS = (
    "qй wц eу rк tе yн uг iш oщ pз [х ]ъ aф sы dв fа gп hр jо kл lд ;ж 'э zя xч cс vм bи nт mь ,б .ю /."
)
_EN_TO_RU: dict[str, str] = {}
for _pair in _EN_TO_RU_PAIRS.split():
    _EN_TO_RU[_pair[0]] = _pair[1]
    # только для букв: у ';' и ',' upper() возвращает их же, и верхний регистр
    # затирал бы нижний — «gj;fkeqcnf» превращалось в «поЖалуйста»
    if _pair[0].isalpha():
        _EN_TO_RU[_pair[0].upper()] = _pair[1].upper()
# символы, у которых верхний регистр даёт другую русскую букву/знак
_EN_TO_RU.update({'"': "Э", ":": "Ж", "<": "Б", ">": "Ю", "?": ",", "{": "Х", "}": "Ъ", "|": "/"})

_TRANSLATION = str.maketrans(_EN_TO_RU)

_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
_LATIN = re.compile(r"[a-z]", re.IGNORECASE)
# не трогаем то, что похоже на адрес, почту, код или артикул
_TECHNICAL = re.compile(r"https?://|www\.|@|\{|\}|<[a-z/]|\b\d{4,}\b", re.IGNORECASE)
_WORD = re.compile(r"[а-яёa-z]+", re.IGNORECASE)

_RU_VOWELS = set("аеёиоуыэюя")

# Частотные пары букв. Списки намеренно короткие: задача не распознать язык вообще,
# а отличить осмысленную русскую фразу от случайной мешанины согласных.
_RU_BIGRAMS = frozenset(
    (
        "ст но то на ен ов ни ра во ко по ро го не из ер ал ль ор та ат ва ем ет ес ел ан ре ло "
        "ли ит ти ка ак ос от ом ин ис ог од ол он ой ый ая ое ми мо ма да де до ду ся сь тв тр "
        "пр пе ри ру чт ых их же за ег ги ир ть ьс рт ле ки ку ды ур ущ юч ки ня ей ии ат ша ще "
        "жи ша чи ци нн лн вы бы мы ты вс ск кл пл сл дл гр кр бр"
    ).split()
)

_EN_BIGRAMS = frozenset(
    (
        "th he in er an re on at en nd ti es or te of ed is it al ar st to nt ng se ha as ou io "
        "le ve co me de hi ri ro ic ne ea ra ce li ch ll be ma si om ur el ol ow ay ee wh"
    ).split()
)


def convert_layout(text: str) -> str:
    """Посимвольный перевод латинской раскладки в русскую (без всякой проверки уместности)."""
    return text.translate(_TRANSLATION)


def _bigram_score(text: str, table: frozenset[str]) -> float:
    """Доля буквенных пар слова, попавших в частотный список. Слова короче 2 букв не в счёт."""
    total = 0
    hits = 0
    for word in _WORD.findall(text.lower()):
        for i in range(len(word) - 1):
            total += 1
            if word[i : i + 2] in table:
                hits += 1
    return hits / total if total else 0.0


# Короткие слова в долю гласных не берём: предлоги и сокращения её искажают
_MIN_WORD_LEN = 3


def _vowel_ratio(text: str) -> float:
    """Доля слов с гласной: в русском слове без гласной жить почти невозможно."""
    words = [w for w in _WORD.findall(text.lower()) if len(w) >= _MIN_WORD_LEN]
    if not words:
        return 1.0
    with_vowel = sum(1 for w in words if any(ch in _RU_VOWELS for ch in w))
    return with_vowel / len(words)


# Длиннее тысячи символов — не реплика, а вставленный документ: раскладку не трогаем
_MAX_TEXT_LEN = 1000
# Меньше четырёх латинских букв — слишком мало, чтобы судить о раскладке
_MIN_LATIN_LETTERS = 4
# Доля кириллицы от латиницы, выше которой текст набран по-русски намеренно
_MAX_CYRILLIC_SHARE = 0.25
# Меньше 60 % слов с гласной — перекодировка дала абракадабру, а не русский текст
_MIN_VOWEL_RATIO = 0.6


def detect_wrong_layout(text: str, threshold: float = 0.5) -> str | None:
    """Возвращает исправленный текст, если пользователь явно забыл переключить раскладку.

    None — если исправлять не нужно (уже по-русски, осмысленный английский, слишком коротко,
    техническая строка вроде ссылки или артикула).
    """
    if not text or len(text) > _MAX_TEXT_LEN:
        return None
    letters = _LATIN.findall(text)
    if len(letters) < _MIN_LATIN_LETTERS:
        return None
    # уже есть заметная кириллица — человек раскладку не терял
    if len(_CYRILLIC.findall(text)) > len(letters) * _MAX_CYRILLIC_SHARE:
        return None
    if _TECHNICAL.search(text):
        return None

    converted = convert_layout(text)
    ru = _bigram_score(converted, _RU_BIGRAMS)
    en = _bigram_score(text, _EN_BIGRAMS)
    if ru < threshold or ru <= en:
        return None
    if _vowel_ratio(converted) < _MIN_VOWEL_RATIO:
        return None
    return converted
