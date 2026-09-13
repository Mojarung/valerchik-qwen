"""Реплика-продолжение: «а для заказчика?» ищется вместе с прошлым вопросом.

Правило нарочно осторожное: склеить новый вопрос с прошлым хуже, чем не склеить
продолжение. Поэтому проверок «не склеилось» здесь не меньше, чем «склеилось»,
а служебные ответы — согласие на оператора, роль, выбор варианта — идут своими
шагами, как и раньше.
"""

import pytest

from app.chat import followup
from app.chat.turn import FollowUp, Turn
from app.models import Message
from app.services import audience as audience_of
from app.services.retrieval import RetrievedDoc

REGISTRATION = "Как зарегистрироваться на Портале поставщиков?"
CONTRACT = "Как подписать контракт на портале?"
MCHD = "Как оформить машиночитаемую доверенность (МЧД)?"
OFFER = "Как разместить оферту на портале?"
MAX_WORDS = 6


@pytest.mark.parametrize(
    ("reply", "previous"),
    [
        ("а для заказчика?", REGISTRATION),
        ("а сроки?", CONTRACT),
        ("а если подписи нет?", CONTRACT),
        ("а где это найти?", MCHD),
        ("в каком разделе?", MCHD),
        ("Почему?", CONTRACT),
        ("Спасибо, а где это сделать?", MCHD),
        ("а если я заказчик?", REGISTRATION),
        ("А ИП тоже может регистрироваться?", REGISTRATION),
    ],
)
def test_short_reply_leaning_on_the_previous_question_continues_it(reply, previous):
    assert followup.continues(reply, previous, MAX_WORDS) is True


@pytest.mark.parametrize(
    ("reply", "previous"),
    [
        # самостоятельные вопросы: свой предмет, своё действие, новый термин
        ("Как подписать контракт?", REGISTRATION),
        ("А как подписать контракт?", REGISTRATION),
        ("а как войти?", REGISTRATION),
        ("а что такое СТЕ?", CONTRACT),
        ("Для чего нужна МЧД?", CONTRACT),
        ("а какие документы нужны поставщику и сколько ждать?", REGISTRATION),
        # служебные ответы: согласие, вежливость, роль, просьба о человеке
        ("да", CONTRACT),
        ("спасибо", CONTRACT),
        ("а, понятно", CONTRACT),
        ("Я заказчик", REGISTRATION),
        ("позовите оператора", CONTRACT),
        ("а оператора можно?", CONTRACT),
        # тот же вопрос ещё раз и первый вопрос диалога
        (CONTRACT, CONTRACT),
        ("а сроки?", ""),
    ],
)
def test_standalone_questions_and_service_replies_are_not_glued(reply, previous):
    assert followup.continues(reply, previous, MAX_WORDS) is False


def test_word_limit_is_a_setting(client):
    assert followup.continues("а если подписи нет?", CONTRACT, 3) is False


@pytest.mark.parametrize(
    ("reply", "side"),
    [
        ("а для заказчика?", audience_of.CUSTOMER),
        ("а для поставщиков?", audience_of.SUPPLIER),
        ("а если я заказчик?", audience_of.CUSTOMER),
        # действие другой стороны — вопрос того, кто спрашивает, а не смена стороны
        ("а если заказчик не подписал?", None),
        ("а сроки?", None),
    ],
)
def test_side_of_the_deal_comes_only_from_a_bare_reply(reply, side):
    assert followup.side_named(reply) == side


def _reply(meta: dict, citations: list | None = None) -> Message:
    return Message(session_id="s", role="assistant", content="…", meta=meta, citations=citations or [])


def test_previous_question_is_the_one_the_bot_answered():
    """Вопрос — из meta ответа: там уже подставлен вопрос после «я заказчик»."""
    answered = _reply({"question": CONTRACT}, [{"article_id": "a1"}, {"question": "без id"}])
    assert followup._previous(answered) == FollowUp(CONTRACT, ("a1",))
    # после вопроса бота реплика — ответ на него: выбор варианта и роль разбирают свои шаги
    assert followup._previous(_reply({"kind": "clarify", "options": []})) is None
    assert followup._previous(_reply({"kind": "clarify_audience", "question": REGISTRATION})) is None
    # приветствие — вопроса ещё не было; перевод на оператора — дальше отвечает человек
    assert followup._previous(_reply({"kind": "greeting"})) is None
    assert followup._previous(_reply({"question": CONTRACT, "escalated": True})) is None
    assert followup._previous(None) is None


def test_verbatim_does_not_repeat_the_previous_answer():
    """В склейке прошлый вопрос целиком — его статья прошла бы дословную проверку снова."""
    docs = [
        RetrievedDoc("a1", CONTRACT, "…", "К", 1, score=0.9),
        RetrievedDoc("a2", "Q", "…", "К", 1, score=0.5),
    ]
    turn = Turn("s", f"{CONTRACT}\nа сроки?", 0.0, probe=docs, follow_up=FollowUp(CONTRACT, ("a1",)))
    assert [doc.article_id for doc in followup.without_repeats(turn).probe] == ["a2"]
    assert turn.probe == docs, "выдача хода для остальных шагов остаётся прежней"
    standalone = Turn("s", CONTRACT, 0.0, probe=docs)
    assert followup.without_repeats(standalone) is standalone


# --- весь путь на настоящей базе: офлайн-модель, сидовые статьи ---


def _session(client) -> str:
    return client.post("/api/chat/sessions").json()["session_id"]


def _say(client, sid: str, text: str, audience: str | None = None) -> dict:
    body: dict = {"text": text}
    if audience:
        body["audience"] = audience
    return client.post(f"/api/chat/sessions/{sid}/messages", json=body).json()


def _last(client, sid: str, role: str) -> dict:
    return [m for m in client.get(f"/api/chat/sessions/{sid}/messages").json() if m["role"] == role][-1]


def test_reply_is_searched_together_with_the_previous_question(client):
    """«а где это сделать?» одна находит акт сверки (0.27) — вместе с вопросом о МЧД находит МЧД."""
    sid = _session(client)
    assert _say(client, sid, MCHD)["status"] == "answered"

    data = _say(client, sid, "а где это сделать?")

    assert data["status"] == "answered"
    assert data["citations"][0]["question"] == MCHD
    # в диалоге — слова человека как есть: склейка — только запрос к базе и агенту
    assert _last(client, sid, "user")["content"] == "а где это сделать?"
    meta = _last(client, sid, "assistant")["meta"]
    assert meta["follow_up_of"] == MCHD
    assert meta["question"] == f"{MCHD}\nа где это сделать?"
    # офлайн-модель искала по склейке, а не по обрывку
    assert meta["tool_trace"][0]["query"] == meta["question"]


def test_bare_side_of_the_deal_asks_the_previous_question_for_that_side(client):
    sid = _session(client)
    assert "ЕСИА" in _say(client, sid, REGISTRATION, audience="supplier")["answer"]

    data = _say(client, sid, "а для заказчика?")

    assert data["status"] == "answered"
    assert "уполномоченный орган" in data["answer"].lower()
    assert "заказчик" in data["citations"][0]["question"].lower()
    # сторона — на один ход: переключатель диалога остался у поставщика
    assert client.get(f"/api/chat/sessions/{sid}").json()["audience"] == "supplier"


def test_condition_on_the_previous_question_stays_on_its_topic(client):
    """«а если подписи нет?» одна ложилась на кнопку «Принять участие» в сессии."""
    sid = _session(client)
    _say(client, sid, CONTRACT, audience="supplier")

    data = _say(client, sid, "а если подписи нет?")

    assert data["status"] == "answered"
    assert data["citations"][0]["question"] == CONTRACT


def test_new_question_is_not_glued_to_the_previous_one(client):
    sid = _session(client)
    _say(client, sid, REGISTRATION, audience="supplier")

    data = _say(client, sid, "Как подписать контракт?")

    assert data["citations"][0]["question"] == CONTRACT
    meta = _last(client, sid, "assistant")["meta"]
    assert meta["question"] == "Как подписать контракт?"
    assert "follow_up_of" not in meta


@pytest.mark.parametrize(("own_best", "glued"), [(0.9, False), (0.1, True)])
async def test_reply_that_finds_its_own_answer_is_not_glued(client, own_best, glued):
    """«а на какой срок?» сама находит статью о сроке оферты. Склейка с прошлым
    вопросом вернула бы статью о размещении (0.90 против 0.21).

    Правило проверяется на выдаче пробного поиска, заданной явно: какую оценку
    реплика получает на сидовой базе, решает поиск, и в lite-режиме (лексическая
    уверенность RagRetriever) «а на какой срок?» одна уверенного ответа не находит."""
    rt = client.app.state.rt
    sid = (await rt.chat.create_session())["session_id"]
    await rt.chat._save_message(sid, "assistant", "…", meta={"question": OFFER})
    reply = "а на какой срок?"
    term = "На какой срок размещается оферта и как её продлить?"
    own = RetrievedDoc("own", term, "…", "К", 1, score=own_best)
    turn = Turn(sid, reply, 0.0, probe=[own], said=reply)

    await followup.maybe_continue(rt.chat, turn)

    assert (turn.follow_up is not None) is glued
    assert turn.text == (f"{OFFER}\n{reply}" if glued else reply)


def test_first_question_has_nothing_to_continue(client):
    sid = _session(client)
    _say(client, sid, "а сроки?")
    meta = _last(client, sid, "assistant")["meta"]
    assert "follow_up_of" not in meta
    assert meta.get("question", "а сроки?") == "а сроки?"


def test_answer_to_the_role_question_is_not_a_follow_up(client):
    sid = _session(client)
    assert _say(client, sid, REGISTRATION)["status"] == "clarify"

    assert _say(client, sid, "Я заказчик")["status"] == "answered"

    meta = _last(client, sid, "assistant")["meta"]
    assert meta["question"] == REGISTRATION
    assert "follow_up_of" not in meta


def test_yes_to_the_operator_offer_is_still_consent(client):
    sid = _session(client)
    assert (
        _say(client, sid, "Как заказать пиццу с доставкой в переговорку через портал?")["status"]
        == "no_answer"
    )
    assert _say(client, sid, "да")["escalated"] is True


def test_kill_switch(client, admin_headers):
    """Без продолжений — прежнее поведение: обрывок ищется сам по себе и не находится."""
    client.put(
        "/api/admin/settings", headers=admin_headers, json={"updates": {"agent.follow_up_enabled": False}}
    )
    try:
        sid = _session(client)
        _say(client, sid, MCHD)
        assert _say(client, sid, "а где это сделать?")["status"] == "no_answer"
        assert "follow_up_of" not in _last(client, sid, "assistant")["meta"]
    finally:
        client.put(
            "/api/admin/settings", headers=admin_headers, json={"updates": {"agent.follow_up_enabled": True}}
        )
