"""Роль посетителя: поставщик или заказчик.

Портал — две стороны одной сделки, и на один и тот же вопрос у них разные
ответы. «Как зарегистрироваться» для поставщика — это электронная подпись и
заявка на регистрацию компании, для заказчика — уполномоченный орган региона
или ЕАИСТ. База знаний портала и сама разделена на эти же разделы (supplier /
customer / general), поэтому статья несёт роль, а поиск отдаёт только то, что
подходит собеседнику.

Роль приходит тремя путями, и здесь они сведены к одному значению:
переключатель в интерфейсе, слова самого человека («я заказчик») и роль,
сохранённая у диалога. Неизвестная роль — это честное «all»: статьи обеих
сторон участвуют в поиске, а если две из них спорят за первое место, бот
переспрашивает вместо того, чтобы гадать.
"""

import re
from typing import Any, Final, Literal

#: Роль собеседника: единственный тип на поиск, промпт и контракт API.
Audience = Literal["all", "supplier", "customer"]

ANY: Final = "all"
SUPPLIER: Final = "supplier"
CUSTOMER: Final = "customer"
AUDIENCES = (ANY, SUPPLIER, CUSTOMER)
_CANONICAL: dict[str, Audience] = {ANY: ANY, SUPPLIER: SUPPLIER, CUSTOMER: CUSTOMER}

LABELS = {SUPPLIER: "Поставщик", CUSTOMER: "Заказчик"}
HINTS = {
    SUPPLIER: "продаю товары, работы или услуги",
    CUSTOMER: "закупаю для организации",
}

_ALIASES: dict[str, Audience] = {
    "поставщик": SUPPLIER,
    "поставщики": SUPPLIER,
    "vendor": SUPPLIER,
    "seller": SUPPLIER,
    "заказчик": CUSTOMER,
    "заказчики": CUSTOMER,
    "buyer": CUSTOMER,
    "покупатель": CUSTOMER,
    "": ANY,
    "any": ANY,
    "общий": ANY,
    "все": ANY,
    "general": ANY,
}

# Явное признание роли. Только формы, где человек говорит о себе: «для заказчика»
# или «как заказчик отклонил УПД» — это вопрос поставщика ПРО заказчика, и по ним
# роль менять нельзя. Основа слова обрезана до «поставщик»/«заказчик», падеж —
# отдельной группой, чтобы одно правило покрывало «я поставщик», «мы поставщики»,
# «являюсь поставщиком», «в качестве заказчика».
# Падеж решает, о ком речь. «Я поставщик» — о себе, «я поставщику отправил
# счёт» — о другой стороне: там винительный и дательный падежи объекта. Раньше
# суффиксы были общим списком, и «мы заказчику выставили счёт» переключало роль
# на заказчика — то есть ровно наоборот. Поэтому у каждой связки свой падеж:
# после «я/мы» — именительный, после «являюсь» — творительный, после
# «в качестве» — родительный.
_SELF = re.compile(
    r"(?:^|[^а-яё])(?:"
    r"(?:я|мы)\s*(?:[-—–]\s*)?(?:это\s+)?(?P<self>поставщик|заказчик)(?:и)?"
    r"|(?:являюсь|являемся|выступаю|выступаем|представляю|представляем)"
    r"\s+(?:как\s+)?(?P<role>поставщик|заказчик)(?:ом|ами)?"
    r"|в\s+качестве\s+(?P<of>поставщик|заказчик)(?:а|ов)"
    r")(?![а-яё])",
)
# «Как заказчику создать сессию» — вопрос от лица заказчика. Только дательный
# падеж: «как заказчик отклонил УПД» — уже про другую сторону.
_DATIVE = re.compile(
    r"(?:^|[^а-яё])(?:как|где|что|куда|можно\s+ли)\s+(?:мне\s+|нам\s+)?"
    r"(поставщик|заказчик)у(?![а-яё])",
)

_CHOICES: dict[tuple[str, ...], Audience] = {
    ("поставщик",): SUPPLIER,
    ("я", "поставщик"): SUPPLIER,
    ("мы", "поставщики"): SUPPLIER,
    ("я", "являюсь", "поставщиком"): SUPPLIER,
    ("заказчик",): CUSTOMER,
    ("я", "заказчик"): CUSTOMER,
    ("мы", "заказчики"): CUSTOMER,
    ("я", "являюсь", "заказчиком"): CUSTOMER,
}
_CHOICE_FILLERS = {"да", "нет", "ну", "я", "мы", "это", "именно", "всё", "все", "таки", "конечно"}
_SUPPLIER_CHOICES = {"поставщик", "поставщики", "поставщиком"}
_CUSTOMER_CHOICES = {"заказчик", "заказчики", "заказчиком"}


def choice(text: str) -> Audience | None:
    """Роль, если вся реплика — только ответ на вопрос о стороне сделки."""
    words = tuple(re.findall(r"[а-яё]+", text.casefold()))
    exact = _CHOICES.get(words)
    if exact is not None:
        return exact
    suppliers = set(words) & _SUPPLIER_CHOICES
    customers = set(words) & _CUSTOMER_CHOICES
    roles = suppliers | customers
    if len(roles) != 1 or any(word not in _CHOICE_FILLERS | roles for word in words):
        return None
    return SUPPLIER if suppliers else CUSTOMER


def is_choice(text: str) -> bool:
    """Не потеряется ли новый вопрос, если заменить реплику сохранённым старым."""
    return choice(text) is not None


def normalize(value: object) -> Audience:
    """Любая запись роли -> supplier | customer | all."""
    if not isinstance(value, str):
        return ANY
    key = value.strip().lower().replace("ё", "е")
    return _CANONICAL.get(key) or _ALIASES.get(key, ANY)


def is_known(value: object) -> bool:
    return normalize(value) != ANY


def allowed(audience: object) -> frozenset[str]:
    """Роли статей, которые подходят собеседнику с такой ролью."""
    role = normalize(audience)
    if role == ANY:
        return frozenset(AUDIENCES)
    return frozenset({ANY, role})


def fits(article_audience: object, audience: object) -> bool:
    """Подходит ли статья с ролью article_audience собеседнику с ролью audience.

    Пустая роль у статьи (строки до появления колонки) — это «для всех».
    """
    return normalize(article_audience) in allowed(audience)


def detect(text: str) -> str | None:
    """Роль из слов самого человека, либо None.

    Возвращает роль только когда она названа однозначно: «я поставщик, но иногда
    я заказчик» называет обе, и выбирать между ними здесь не наше дело.
    """
    direct = choice(text)
    if direct is not None:
        return direct
    lowered = text.lower().replace("ё", "е")
    found = {m.group("self") or m.group("role") or m.group("of") for m in _SELF.finditer(lowered)}
    found |= {m.group(1) for m in _DATIVE.finditer(lowered)}
    if len(found) != 1:
        return None
    return _ALIASES[found.pop()]


def label(audience: object) -> str:
    return LABELS.get(normalize(audience), "")


def options() -> list[dict[str, Any]]:
    """Варианты переключателя для интерфейса — единственный источник подписей."""
    return [{"key": key, "label": LABELS[key], "hint": HINTS[key]} for key in (SUPPLIER, CUSTOMER)]


def clarify_options() -> list[dict[str, Any]]:
    """Кнопки уточняющего вопроса «вы поставщик или заказчик?».

    Текст кнопки — реплика, которую detect() опознаёт как признание роли:
    нажатие — это обычное сообщение в диалоге, без отдельного состояния.
    """
    return [{"text": f"Я {LABELS[key].lower()}", "audience": key} for key in (SUPPLIER, CUSTOMER)]
