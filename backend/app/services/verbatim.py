"""Дословный ответ: когда вопрос уже отвечен в выданных данных.

Правило простое: если в выдаче есть авторская пара «вопрос — ответ» и её вопрос —
это буквально заданный вопрос, отдаём ответ так, как он написан. Пересказ готовой
формулировки — потеря, а не польза: в ней номера разделов, порядок шагов и слова
регламента, ради которых её и писали.

«Буквально тот же» проверяется дважды и по-разному: порогом схожести (сколько
слов общих) и покрытием (нет ли слова без пары). Одной схожести мало — лишнее
«не» или одно заменённое слово стоят в ней меньше порога, и вопрос с обратным
смыслом проходил как свой.

Шаг детерминированный и не зовёт модель. Поэтому он работает и в mock-режиме —
то есть на защите, где интернета может не быть.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.dynamic_settings import DynamicSettings
    from app.services.retrieval import RetrievedDoc

_WORD = re.compile(r"[а-яёa-z0-9]+")
# Слова, которые есть в каждом втором вопросе и потому ничего не различают.
# Без их отсева «Как создать сессию?» и «Как создать оферту?» дают 0.6 совпадения.
_STOP = frozenset(
    {
        "как",
        "что",
        "где",
        "когда",
        "почему",
        "зачем",
        "какой",
        "какая",
        "какие",
        "мне",
        "мной",
        "меня",
        "это",
        "для",
        "если",
        "или",
        "the",
        "how",
        "what",
    }
)


def _tokens(text: str) -> set[str]:
    words = set(_WORD.findall(text.lower().replace("ё", "е")))
    meaningful = words - _STOP
    # Вопрос из одних служебных слов — редкость, но пустое множество дало бы
    # деление на ноль и «совпадение» с чем угодно.
    return meaningful or words


def similarity(first: str, second: str) -> float:
    """Жаккар по значимым словам.

    Не эмбеддинги намеренно: здесь нужна дешёвая и предсказуемая проверка «это
    тот же самый вопрос», а не семантическая близость. Близость уже померена
    поиском и приехала отдельным порогом.
    """
    left, right = _tokens(first), _tokens(second)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


# Слова, которые переворачивают смысл вопроса целиком: «контракт подписан в срок»
# и «контракт НЕ подписан в срок» — два разных обращения с разными ответами.
_NEGATIONS = frozenset(
    {"не", "нет", "ни", "нельзя", "без", "кроме", "запрещено", "запрещен", "отказ", "отказано"}
)
# Ниже этой длины слово сравниваем только точно: у коротких слов («до» и «по»,
# «сдал» и «сдам») одна буква — это уже другое слово, а не описка.
_MIN_FUZZY = 5
_MAX_TAIL = 3


def _akin(first: str, second: str) -> bool:
    """Одно и то же слово в другой форме или с опиской — но не другое слово.

    Совпадения по корню хватает: «сессию»/«сессии», «оферту»/«оферта» человек
    пишет как придётся. А вот «продлить» и «продлевается» уже расходятся хвостом
    на семь букв, и складывать их в одно слово — начало ложного срабатывания.
    """
    if first == second:
        return True
    shorter, longer = sorted((first, second), key=len)
    if len(shorter) < _MIN_FUZZY:
        return False
    stem = 0
    for left_char, right_char in zip(shorter, longer, strict=False):
        if left_char != right_char:
            break
        stem += 1
    if stem >= _MIN_FUZZY and len(longer) - stem <= _MAX_TAIL:
        return True
    return len(longer) - len(shorter) <= 1 and _one_edit_apart(shorter, longer)


def _one_edit_apart(shorter: str, longer: str) -> bool:
    """Слова расходятся не больше чем на одну вставку, пропуск или замену."""
    if len(longer) == len(shorter):
        return sum(a != b for a, b in zip(shorter, longer, strict=True)) == 1
    for index in range(len(longer)):
        if longer[:index] + longer[index + 1 :] == shorter:
            return True
    return False


def _uncovered(words: set[str], other: set[str]) -> set[str]:
    """Значимые слова, которым в другом вопросе не нашлось пары."""
    return {word for word in words if not any(_akin(word, mate) for mate in other)}


# Слова, которые сами по себе меняют смысл вопроса: сторона сделки, момент
# времени, повторность, действие над объектом. Лишнее «подскажите» вопрос не
# меняет, лишнее «поставщику» — меняет: это уже другое обращение с другим ответом.
# Короткие сравниваем точно («до» — предлог, «доверенность» — не он), длинные по
# корню: падеж и род у них гуляют («повторную», «повторный», «повторно»).
_SENSE_EXACT = _NEGATIONS | {"до", "после", "снова", "заново", "впервые"}
_SENSE_STEMS = ("поставщик", "заказчик", "повторн", "отмен", "измен", "продл", "расторг", "запрещ", "отказ")


def _changes_meaning(word: str) -> bool:
    return word in _SENSE_EXACT or word.startswith(_SENSE_STEMS)


def is_same_question(first: str, second: str) -> bool:
    """Вопрос пользователя спрашивает ровно то же, что вопрос статьи.

    Жаккара на это не хватает, и порогом дело не поправить: лишнее «не» или одно
    заменённое слово стоят в нём 0.17–0.25, поэтому «Можно ли изменить цену ДО
    подписания» и «...ПОСЛЕ подписания» дают 0.78 — выше любого разумного порога.
    Дословный ответ на такой вопрос — уверенный ответ на чужое обращение, а это
    хуже отказа.

    Но покрытие в обе стороны оказалось лекарством хуже болезни: одно вежливое
    «подскажите» — и главная фича молча выключалась. Поэтому проверка
    несимметрична, как несимметричны и сами роли: статья обязана быть покрыта
    целиком (иначе человек спросил не про это), а лишние слова пользователя
    прощаются — кроме тех, что меняют смысл сами по себе.

    `first` — вопрос пользователя, `second` — вопрос статьи.
    """
    asked, article = _tokens(first), _tokens(second)
    if not asked or not article:
        return False
    # Отрицание проверяем отдельно от покрытия: оно меняет смысл, а не слова.
    if bool(asked & _NEGATIONS) != bool(article & _NEGATIONS):
        return False
    # Каждое значимое слово статьи должно прозвучать в вопросе: «как создать
    # сессию» не отвечает на «как создать котировочную сессию заказчику».
    if _uncovered(article, asked):
        return False
    # Лишние слова пользователя молчаливо прощаем — но не те, что задают сторону
    # сделки, момент или повторность: с ними это уже другое обращение.
    return not any(_changes_meaning(word) for word in _uncovered(asked, article))


def _answerable(
    doc: RetrievedDoc, origins: dict[str, dict[str, Any]], visuals: dict[str, list[dict[str, Any]]] | None
) -> bool:
    """Статью можно отдать дословно: она авторская и в ней есть что отдать.

    Пустой текст ответа — не всегда брак. В выгрузках поддержки на «где эта
    кнопка» отвечают скриншотом, и такая статья состоит из вопроса и картинки.
    Отдать её можно, а вот агенту она бесполезна: в промпт уедет пустая строка.
    """
    article_id = getattr(doc, "article_id", "")
    if not (origins.get(article_id) or {}).get("authored"):
        return False
    has_text = bool((getattr(doc, "answer", "") or "").strip())
    has_images = bool((visuals or {}).get(article_id))
    return has_text or has_images


def pick_verbatim(
    dyn: DynamicSettings,
    question: str,
    docs: list[RetrievedDoc],
    origins: dict[str, dict[str, Any]],
    visuals: dict[str, list[dict[str, Any]]] | None = None,
) -> RetrievedDoc | None:
    """Статья из выдачи, ответ которой можно отдать дословно, либо None.

    Четыре условия сразу: статья авторская, поиск в ней уверен, вопрос похож и
    вопрос — буквально тот же. Ослабить любое — и пользователь получит чужой
    готовый ответ на похожий, но не свой вопрос, причём с высокой уверенностью.

    Смотрим всю выдачу, а не только первую статью. Порядок задаёт поиск, и он
    ошибается на надмножествах: «Как подать жалобу в ФАС?» ставит первой статью
    «Как подать жалобу в ФАС ПОВТОРНО?» с оценкой 1.0, хотя дословно совпавшая
    статья лежит второй. Отдать в такой ситуации первую — значит ответить не на
    заданный вопрос ровно там, где ответ в выгрузке есть.
    """
    if not docs or not dyn.get("answer.verbatim_enabled"):
        return None
    min_score = float(dyn.get("answer.verbatim_score"))
    min_similarity = float(dyn.get("answer.verbatim_similarity"))

    best = None
    best_rank = (0.0, 0.0)
    for doc in docs:
        if float(getattr(doc, "score", 0.0)) < min_score:
            continue
        if not _answerable(doc, origins, visuals):
            continue
        doc_question = getattr(doc, "question", "")
        score = similarity(question, doc_question)
        if score < min_similarity or not is_same_question(question, doc_question):
            continue
        rank = (score, float(getattr(doc, "score", 0.0)))
        if best is None or rank > best_rank:
            best, best_rank = doc, rank
    return best


def visual_answer_text(dyn: DynamicSettings, images: list[dict[str, Any]]) -> str:
    """Текст к ответу-картинке: подпись автора, а если её нет — фраза из настроек.

    Совсем без текста ответ выглядел бы как сбой: в ленте пустой пузырь с
    картинкой неотличим от «бот промолчал и что-то прикрепил».
    """
    caption = next(
        (
            str(image.get("caption") or "").strip()
            for image in images
            if str(image.get("caption") or "").strip()
        ),
        "",
    )
    prefix = str(dyn.get("answer.visual_message"))
    return f"{prefix} {caption}".strip() if caption else prefix
