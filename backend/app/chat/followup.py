"""Реплика-продолжение: «а для заказчика?», «а если подписи нет?», «а где это найти?».

Такая реплика — вторая половина прошлого вопроса, а не новый вопрос. Одна она
находит в базе знаний что попало: «а если подписи нет?» после вопроса о подписании
контракта ложилась на кнопку «Принять участие» в сессии с оценкой 0.25, «а где это
сделать?» после вопроса о МЧД — на акт сверки. Поэтому ищем её вместе с прошлым
вопросом. Сообщение человека в диалоге остаётся как есть: склейка — только запрос
к базе знаний и агенту.

Правило без нейросети и нарочно осторожное: склеить самостоятельный вопрос с
прошлым хуже, чем не склеить продолжение. «Как подписать контракт?» после вопроса
о регистрации искался бы вместе с регистрацией и получал бы её же ответ. Поэтому
продолжение — только короткая реплика, которая сама показывает, что опирается на
прошлую: начинается с «а», «и», «если», с предлога без подлежащего («для
заказчика?», «в каком разделе?»), ссылается на сказанное («это», «там», «её») — и
не называет своего нового действия («а как войти?» — уже свой вопрос).

И даже такую склеиваем, только если одна она уверенного ответа не находит: прошлый
вопрос в склейке перетягивает поиск на себя. «а на какой срок?» после вопроса о
размещении оферты сама находит статью о сроке оферты (0.48), а вместе с прошлым
вопросом — снова статью о размещении (0.90). Исключение — реплика, в которой, кроме
стороны сделки, ничего нет («а для заказчика?»): своего предмета у неё нет, и
совпадение одного слова с заголовком чужой статьи ответом не считается.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.chat.turn import FollowUp, Turn
from app.models import Message
from app.services import audience as audience_of
from app.services.agent import wants_operator
from app.services.retrieval import tokenize

if TYPE_CHECKING:
    from app.services.chat import ChatService

log = logging.getLogger(__name__)

_WORD = re.compile(r"[а-яa-z0-9]+")
#: С чего начинается продолжение: «а сроки?», «и где это?», «если подписи нет?»
_LEADS = frozenset({"а", "и", "но", "ну", "еще", "тогда", "также", "тоже", "если"})
#: Предлог в начале — обрывок фразы без подлежащего: «для заказчика?», «в каком разделе?»
_PREPOSITIONS = frozenset(
    {"для", "в", "во", "на", "по", "при", "без", "с", "со", "у", "от", "до", "через", "после"}
    | {"из", "за", "про", "о", "об"}
)
#: Обрывок с предлога — не длиннее трёх слов: «Для чего нужна МЧД?» — уже свой вопрос
_SHORT_PHRASE = 3
#: Отсылка к сказанному и условие к нему: без прошлого вопроса реплике не на что опереться
_BACK_REFERENCES = frozenset(
    {"это", "этого", "этому", "этим", "этом", "эта", "эту", "эти", "этих", "этот"}
    | {"там", "туда", "тут", "здесь", "его", "ее", "их", "него", "нее", "них", "нему", "ней", "ним"}
    | {"нем", "он", "она", "оно", "они", "такой", "такая", "такие", "такую", "такого"}
    | {"тоже", "также", "тогда", "если"}
)
#: Вопросительные и служебные слова: своего предмета реплике они не дают
_GENERIC = frozenset(
    {"где", "когда", "куда", "откуда", "зачем", "почему", "сколько", "долго", "как", "что", "чем"}
    | {"кто", "какой", "какая", "какое", "какие", "каким", "каком", "каких", "какую", "какого"}
    | {"делать", "сделать", "найти", "посмотреть", "узнать", "нет", "может", "будет", "есть"}
    | {"быть", "стоит", "нужен", "нужна", "нужны", "нужно", "можно", "надо", "я", "мы", "вы"}
    | {"мне", "меня", "нам", "нас", "вам"}
)
#: Реплика из одних этих слов — вежливость или согласие, а не вопрос
_SOCIAL = frozenset(
    {"да", "нет", "ок", "окей", "ok", "хорошо", "понятно", "ясно", "ладно", "спасибо", "благодарю"}
    | {"спс", "отлично", "супер", "ага", "угу", "понял", "поняла", "здорово", "класс"}
)
#: «а что такое СТЕ?» — вопрос о новом термине, а не о прошлом ответе
_DEFINITION = re.compile(r"\bчто\s+(?:такое|значит|означает)\b")
#: Инфинитив — своё действие: «а как войти?», «и где скачать выписку?»
_INFINITIVE = re.compile(r"\w{2,}(?:ть|ти|чь)(?:ся|сь)?$")
_NOT_ACTIONS = frozenset({"есть", "быть"})
#: Своих значимых слов у продолжения не больше двух («а сроки оплаты?»): больше — свой вопрос
_MAX_OWN_WORDS = 2
#: Основы, которые не дают реплике своего предмета, — в том же виде, что у tokenize
_NOT_TOPIC = frozenset(word[:6] for word in _LEADS | _PREPOSITIONS | _BACK_REFERENCES | _GENERIC | _SOCIAL)
#: Сторона сделки одним словом: «а для заказчика?», «а если я поставщик?»
_SIDES = {"заказч": audience_of.CUSTOMER, "постав": audience_of.SUPPLIER}
#: Реплика после этих вопросов бота — ответ на них, а не продолжение своего:
#: выбор варианта уточнения и «я заказчик» разбирают свои шаги
_BOT_QUESTIONS = frozenset({"clarify", "clarify_audience"})


def _plain(text: str) -> str:
    return text.lower().replace("ё", "е")


def _is_action(word: str) -> bool:
    return word not in _NOT_ACTIONS and bool(_INFINITIVE.search(word))


def _own_words(text: str) -> set[str]:
    """Основы слов, которые задают реплике свой предмет: «сроки», «заказчика», «подписи»."""
    return set(tokenize(text)) - _NOT_TOPIC


def candidate(text: str, max_words: int) -> bool:
    """Похожа ли реплика на продолжение — по ней самой, ещё без прошлого вопроса.

    Дешёвая проверка до чтения истории: обычный вопрос не должен платить за
    лишний запрос к базе.
    """
    plain = _plain(text)
    words = _WORD.findall(plain)
    if not words or len(words) > max_words or set(words) <= _SOCIAL | _LEADS:
        return False
    # Просьбу позвать человека разбирают свои шаги, а «что такое» спрашивает о новом термине
    if wants_operator(text) or _DEFINITION.search(plain):
        return False
    vocab = set(words)
    refers_back = bool(vocab & _BACK_REFERENCES)
    elliptic = (
        words[0] in _LEADS
        or (words[0] in _PREPOSITIONS and len(words) <= _SHORT_PHRASE)
        # «Почему?», «а где?» — один вопросительный обрывок
        or vocab <= _GENERIC | _LEADS | _BACK_REFERENCES
    )
    if not (refers_back or elliptic):
        return False
    # Своё действие без отсылки к сказанному — новый вопрос: «а как войти?». С
    # отсылкой — прежний: «а где это найти?», «а как её продлить?».
    return refers_back or not any(_is_action(word) for word in words)


def continues(text: str, previous: str, max_words: int) -> bool:
    """Реплика продолжает прошлый вопрос, а не задаёт свой."""
    if not previous.strip() or _plain(text).strip() == _plain(previous).strip():
        return False
    if not candidate(text, max_words):
        return False
    # Слова, которые уже звучали в прошлом вопросе, своими не считаются: в «а если
    # подписи нет?» после «как подписать контракт» нового — ничего.
    return len(_own_words(text) - set(tokenize(previous))) <= _MAX_OWN_WORDS


def side_named(text: str) -> str | None:
    """Сторона сделки, если, кроме неё, в реплике ничего нет: «а для заказчика?» → customer.

    «а если заказчик не подписал?» — уже не про сторону, а про её действие, и
    искать там надо статьи того, кто спрашивает.
    """
    own = _own_words(text)
    if len(own) != 1:
        return None
    return _SIDES.get(next(iter(own)))


def glue(previous: str, text: str) -> str:
    """Запрос к базе знаний: прошлый вопрос и реплика. Тот же текст агент получает
    сообщением пользователя (agent._prompt), и офлайн-модель ищет ровно по нему."""
    return f"{previous.strip()}\n{text.strip()}"


async def _last_reply(svc: ChatService, session_id: str) -> Message | None:
    async with svc.rt.db.session_factory() as db:
        return (
            (
                await db.execute(
                    select(Message)
                    .where(Message.session_id == session_id, Message.role == "assistant")
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )


def _previous(reply: Message | None) -> FollowUp | None:
    """Вопрос, на который бот ответил последним, — если реплике есть что продолжать.

    Вопрос берём из meta ответа, а не из прошлой реплики человека: там уже
    подставлен вопрос после «я заказчик» и склейка прошлого продолжения. Цепочка
    «а если подписи нет?» → «а где её получить?» так и помнит, о чьей подписи речь.
    """
    if reply is None:
        return None
    meta = reply.meta or {}
    question = str(meta.get("question") or "").strip()
    if not question or meta.get("kind") in _BOT_QUESTIONS or meta.get("escalated"):
        return None
    sources = tuple(str(c["article_id"]) for c in reply.citations or [] if c.get("article_id"))
    return FollowUp(question, sources)


async def maybe_continue(svc: ChatService, turn: Turn) -> None:
    """Продолжение прошлого вопроса ищется вместе с ним: меняет текст, роль и выдачу хода.

    Зовётся после предварительного поиска по самой реплике: его выдача решает,
    нужна ли склейка вообще, а если нет — остаётся выдачей хода, второго поиска
    не будет.
    """
    dyn = svc.rt.dyn
    max_words = int(dyn.get("agent.follow_up_max_words"))
    # Текст уже подменён (ответ на «вы поставщик или заказчик?») или поиск сломался —
    # продолжать нечего и сравнивать не с чем.
    if not dyn.get("agent.follow_up_enabled") or turn.text != turn.said or turn.probe is None:
        return
    if not candidate(turn.said, max_words):
        return
    previous = _previous(await _last_reply(svc, turn.session_id))
    if previous is None or not continues(turn.said, previous.question, max_words):
        return
    side = side_named(turn.said)
    own_best = max((doc.score for doc in turn.probe), default=0.0)
    if side is None and own_best >= float(dyn.get("agent.confidence_threshold")):
        return
    turn.follow_up = previous
    turn.text = glue(previous.question, turn.said)
    # Сторона на один ход, в диалоге не запоминаем: «а для заказчика?» спрашивает
    # и поставщик, которому интересна другая сторона, — переключатель не его.
    if side is not None:
        turn.role = side
    turn.flags["follow_up_of"] = previous.question
    turn.probe = await svc._probe_kb(turn.text, turn.page_text, turn.role)
    log.info(
        "Продолжение в сессии %s: %r ищется вместе с %r (своя оценка %.3f)",
        turn.session_id,
        turn.said[:60],
        previous.question[:60],
        own_best,
    )


def without_repeats(turn: Turn) -> Turn:
    """Ход для дословного ответа — без статей, которыми уже ответили на прошлый вопрос.

    В склейке прошлый вопрос целиком, и его статья проходит проверку «тот же
    вопрос»: дословный ответ повторил бы человеку прошлый ответ слово в слово.
    Отвечать на уточнение пересказом той же статьи — дело агента: он видит и
    реплику, и историю.
    """
    if turn.follow_up is None or not turn.probe:
        return turn
    seen = set(turn.follow_up.sources)
    return dataclasses.replace(turn, probe=[doc for doc in turn.probe if doc.article_id not in seen])
