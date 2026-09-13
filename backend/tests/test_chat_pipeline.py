"""Развилки конвейера реплики, которые рефакторинг обязан сохранить.

handle_user_message разложен на фазы; эти проверки держат его поведение на
краях: пустая и слишком длинная реплика, грубость с предупреждением, мат при
политике «сначала предупредить», сбой внешнего инструмента в meta ответа.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.chat import steps
from app.chat.errors import ChatError
from app.services import chat as chat_service
from app.services.agent import AgentOutcome


def _session(client) -> str:
    return client.post("/api/chat/sessions").json()["session_id"]


def _say(client, sid: str, text: str) -> dict:
    return client.post(f"/api/chat/sessions/{sid}/messages", json={"text": text}).json()


def test_blank_message_is_rejected(client):
    resp = client.post(f"/api/chat/sessions/{_session(client)}/messages", json={"text": "   "})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Пустое сообщение"


async def test_overlong_message_is_rejected(client):
    with pytest.raises(ChatError) as error:
        await client.app.state.rt.chat.handle_user_message(_session(client), "а" * 4001)
    assert error.value.status_code == 422


def test_rudeness_warns_first_then_closes(client):
    sid = _session(client)
    assert _say(client, sid, "ты баран")["status"] == "warned"
    assert _say(client, sid, "ты баран")["status"] == "closed_violation"


def test_profanity_under_warn_policy_warns_once(client, monkeypatch):
    monkeypatch.setitem(client.app.state.rt.dyn._values, "guardrails.profanity_action", "warn")
    sid = _session(client)
    assert _say(client, sid, "да пошло оно всё, хуйня ваш портал")["status"] == "warned"
    assert _say(client, sid, "хуйня")["status"] == "closed_violation"


class _Model:
    """Подмена нейросети: отдаёт заданные вероятности и помнит, о чём спросили."""

    def __init__(self, scores: dict | None) -> None:
        self._scores = scores
        self.asked: list[str] = []

    async def scores(self, text: str) -> dict | None:
        self.asked.append(text)
        return self._scores


_INSULT = {"non-toxic": 0.01, "insult": 0.97, "obscenity": 0.0, "threat": 0.0, "dangerous": 0.8}


def test_model_insult_without_bad_words_warns_then_closes(client, monkeypatch):
    """Оскорбление без единого «плохого» слова — работа нейросети, правила молчат."""
    monkeypatch.setattr(client.app.state.rt, "toxicity", _Model(_INSULT))
    sid = _session(client)
    assert _say(client, sid, "вы там совсем с головой не дружите?")["status"] == "warned"
    assert _say(client, sid, "вы там совсем с головой не дружите?")["status"] == "closed_violation"


def test_unavailable_model_blocks_nothing(client, monkeypatch):
    model = _Model(None)
    monkeypatch.setattr(client.app.state.rt, "toxicity", model)
    sid = _session(client)
    assert _say(client, sid, "Как зарегистрироваться на портале?")["status"] not in (
        "warned",
        "closed_violation",
    )
    assert model.asked == ["Как зарегистрироваться на портале?"]


def test_model_is_asked_only_when_rules_are_silent_and_can_be_disabled(client, monkeypatch):
    model = _Model(_INSULT)
    monkeypatch.setattr(client.app.state.rt, "toxicity", model)
    # правила уже поймали — нейросеть не нужна, лишний запрос не делаем
    assert _say(client, _session(client), "ты баран")["status"] == "warned"
    assert model.asked == []

    monkeypatch.setitem(client.app.state.rt.dyn._values, "guardrails.model_enabled", False)
    assert _say(client, _session(client), "вы там совсем с головой не дружите?")["status"] != "warned"
    assert model.asked == []


def _violation_notice(client, sid: str) -> dict | None:
    history = client.get(f"/api/chat/sessions/{sid}/messages").json()
    return next((m for m in history if m["role"] == "system" and m["meta"].get("kind") == "violation"), None)


def test_lexicon_word_closes_with_notice(client):
    """Производная мата из словаря — та же нецензурная лексика: обращение завершается сразу."""
    sid = _session(client)
    assert _say(client, sid, "вымандохивали мне весь день, где оператор")["status"] == "closed_violation"
    notice = _violation_notice(client, sid)
    assert notice is not None and "нарушает правила общения" in notice["content"]
    assert notice["meta"]["matched"] == ["вымандохивали"]


def test_model_obscenity_closes_like_profanity(client, monkeypatch):
    """«Мат» по оценке нейросети завершает обращение, оскорбление — только предупреждает."""
    obscene = {"non-toxic": 0.01, "insult": 0.1, "obscenity": 0.96, "threat": 0.0, "dangerous": 0.5}
    monkeypatch.setattr(client.app.state.rt, "toxicity", _Model(obscene))
    sid = _session(client)
    assert _say(client, sid, "что за фигня тут творится")["status"] == "closed_violation"
    assert _violation_notice(client, sid)["meta"]["matched"] == ["нейросеть: мат 0.96"]


def test_failed_external_tool_is_flagged_in_meta(client, monkeypatch):
    @asynccontextmanager
    async def failing_toolsets(session_factory, active):
        yield SimpleNamespace(toolsets=[], failed=[("каталог", RuntimeError("недоступен"))])

    async def fake_agent(*args, **kwargs):
        return AgentOutcome(
            answer="Нашёл два ноутбука.",
            answered=True,
            confidence=0.9,
            citations=[],
            escalated=False,
            line=None,
            tool_trace=[],
        )

    monkeypatch.setattr(chat_service, "mcp_toolsets", failing_toolsets)
    monkeypatch.setattr(chat_service, "run_support_agent", fake_agent)
    sid = _session(client)
    _say(client, sid, "найди ноутбук")
    answer = [m for m in client.get(f"/api/chat/sessions/{sid}/messages").json() if m["role"] == "assistant"][
        -1
    ]
    assert answer["meta"]["mcp_failed"] == ["каталог"]


@pytest.mark.parametrize(
    "text",
    [
        "спасибо",
        "Спасибо!",
        "спасибо огромное",
        "Благодарю",
        "спасибо большое за помощь, всё понятно",
        "спс",
        "спасибо валерчик",
        "Спасибо, Валера!",
        "благодарю вас, бот",
    ],
)
def test_gratitude_is_recognized(text):
    assert steps.is_gratitude(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "спасибо, а как подать заявку?",
        "да, спасибо",
        "нет, спасибо",
        "хорошо",
        "как поблагодарить заказчика в отзыве",
    ],
)
def test_question_or_answer_with_thanks_is_not_farewell(text):
    assert steps.is_gratitude(text) is False


def test_thanks_closes_dialog_with_farewell_and_feedback_request(client, monkeypatch):
    """«Спасибо огромное» завершает диалог, а не получает отказ базы знаний."""

    async def agent_must_not_run(*args, **kwargs):
        raise AssertionError("агент не нужен для благодарности")

    monkeypatch.setattr(chat_service, "run_support_agent", agent_must_not_run)
    sid = _session(client)

    result = _say(client, sid, "спасибо огромное")

    assert result["status"] == "resolved"
    assert result["answer"] == client.app.state.rt.dyn.get("chat.thanks_message")
    history = client.get(f"/api/chat/sessions/{sid}/messages").json()
    assert [m["meta"].get("kind") for m in history[-2:]] == ["farewell", "feedback_request"]
    resp = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "ещё вопрос"})
    assert resp.status_code == 409
