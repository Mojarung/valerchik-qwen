"""Оценка — один голос, воспроизводимое замечание и явное продолжение диалога."""

from unittest.mock import AsyncMock

import pytest

from app.models import ChatSession
from app.services import lines
from app.services.routing import RoutePrediction


def _answer(client):
    sid = client.post("/api/chat/sessions", json={"audience": "supplier"}).json()["session_id"]
    response = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться?"})
    assert response.status_code == 200
    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    msg = next(m for m in reversed(messages) if m["role"] == "assistant" and not m["meta"].get("kind"))
    return sid, msg


def _answered_message(client, question):
    sid = client.post("/api/chat/sessions", json={"audience": "supplier"}).json()["session_id"]

    async def seed_answer():
        chat = client.app.state.rt.chat
        await chat._save_message(sid, "user", question, meta={"audience": "supplier"})
        answer = await chat._save_message(
            sid,
            "assistant",
            "Перерегистрируйте контракт и создайте новое исполнение.",
            meta={"answered": True, "question": question, "audience": "supplier"},
        )
        return {"id": answer.id}

    return sid, client.portal.call(seed_answer)


def _rate(client, sid, mid, **body):
    return client.post(f"/api/chat/sessions/{sid}/messages/{mid}/rating", json=body)


def _review(client, headers, mid):
    page = client.get("/api/admin/answer-feedback?status=all", headers=headers).json()
    return next(r for r in page["items"] if r["message_id"] == mid)


def test_one_vote_can_be_changed_and_removed(client, admin_headers):
    sid, msg = _answer(client)
    mid = msg["id"]
    before = client.get("/api/admin/answer-feedback", headers=admin_headers).json()
    body = {"value": -1, "reason": "unclear", "comment": "Непонятен второй шаг"}
    for _ in range(3):
        assert _rate(client, sid, mid, **body).status_code == 200
    after = client.get("/api/admin/answer-feedback", headers=admin_headers).json()
    assert after["negative"] == before["negative"] + 1
    row = _review(client, admin_headers, mid)
    assert row["reason"] == "unclear"
    assert row["snapshot"]["answer"] == msg["content"]
    assert row["snapshot"]["question"] == "Как зарегистрироваться?"
    _rate(client, sid, mid, value=1)
    after = client.get("/api/admin/answer-feedback", headers=admin_headers).json()
    assert after["negative"] == before["negative"]
    assert after["positive"] == before["positive"] + 1
    _rate(client, sid, mid, value=0)
    stored = client.get(f"/api/chat/sessions/{sid}/messages").json()
    rated = next(m for m in stored if m["id"] == mid)
    assert rated["meta"]["rating"] == 0
    assert rated["meta"]["rating_comment"] == ""


def test_feedback_permissions_and_validation(client, admin_headers, operator_headers):
    sid, msg = _answer(client)
    base = f"/api/chat/sessions/{sid}/messages/{msg['id']}"
    assert (
        client.post(base + "/rating", json={"value": -1}, headers={"X-Session-Owner": "wrong"}).status_code
        == 403
    )
    assert _rate(client, sid, msg["id"], value=-1, reason="invented").status_code == 422
    assert _rate(client, sid, msg["id"], value=0.5).status_code == 422
    assert _rate(client, sid, msg["id"], value=-1).status_code == 200
    review = f"/api/admin/answer-feedback/{msg['id']}"
    assert client.patch(review, json={"status": "in_progress"}, headers=operator_headers).status_code == 403
    assert (
        client.patch(
            review, json={"status": "verified", "note": "Не проверял"}, headers=admin_headers
        ).status_code
        == 409
    )
    assert client.patch(review, json={"status": "dismissed"}, headers=admin_headers).status_code == 422


def test_review_lifecycle_and_repeat_delivery(client, admin_headers):
    sid, msg = _answer(client)
    _rate(client, sid, msg["id"], value=-1, reason="incorrect")
    base = f"/api/admin/answer-feedback/{msg['id']}"
    article = {
        "question": "Как проверить исправление оценки?",
        "answer": "Откройте раздел замечаний и проверьте ответ.",
        "category": "Проверки",
        "audience": "supplier",
    }
    body = {"article": article, "note": "Добавлена проверенная инструкция"}
    first = client.post(base + "/resolve", json=body, headers=admin_headers)
    assert first.status_code == 200, first.text
    second = client.post(base + "/resolve", json=body, headers=admin_headers)
    assert second.json()["article_id"] == first.json()["article_id"]
    assert _review(client, admin_headers, msg["id"])["status"] == "fixed"
    verified = client.patch(
        base,
        json={"status": "verified", "note": "Проверено по опубликованному источнику"},
        headers=admin_headers,
    )
    assert verified.status_code == 200
    # Повторная доставка того же голоса не открывает разобранное замечание.
    _rate(client, sid, msg["id"], value=-1, reason="incorrect")
    assert _review(client, admin_headers, msg["id"])["status"] == "verified"
    _rate(
        client, sid, msg["id"], value=-1, reason="not_working", comment="После исправления проблема осталась"
    )
    assert _review(client, admin_headers, msg["id"])["status"] == "open"


def test_index_retry_does_not_create_another_article(client, admin_headers, monkeypatch):
    sid, msg = _answer(client)
    _rate(client, sid, msg["id"], value=-1)
    rt = client.app.state.rt
    index = AsyncMock(side_effect=[RuntimeError("index unavailable"), None])
    monkeypatch.setattr(rt.kb, "index_article", index)
    body = {
        "article": {
            "question": "Как повторить индексацию исправления?",
            "answer": "Повторите сохранение замечания.",
            "category": "Проверки",
        },
        "note": "Исправление после сбоя индекса",
    }
    path = f"/api/admin/answer-feedback/{msg['id']}/resolve"
    with pytest.raises(RuntimeError, match="index unavailable"):
        client.post(path, json=body, headers=admin_headers)
    pending = _review(client, admin_headers, msg["id"])
    assert pending["status"] == "in_progress"
    repeated = client.post(path, json=body, headers=admin_headers)
    assert repeated.status_code == 200
    assert repeated.json()["article_id"] == pending["article_id"]
    assert index.await_count == 2


@pytest.mark.parametrize("action", ["explain", "clarify"])
def test_recovery_uses_feedback_skips_verbatim_and_is_idempotent(client, monkeypatch, admin_headers, action):
    sid, msg = _answer(client)
    _rate(client, sid, msg["id"], value=-1, reason="unclear", comment="Расскажите по шагам")
    from app.services.agent import AgentOutcome

    response = AgentOutcome(
        answer="1. Откройте регистрацию.\n2. Выберите роль поставщика.",
        answered=True,
        confidence=0.9,
        citations=msg["citations"],
        escalated=False,
        line=None,
        tool_trace=[],
    )
    agent = AsyncMock(return_value=response)
    monkeypatch.setattr("app.services.chat.run_support_agent", agent)
    quick = AsyncMock(side_effect=AssertionError("recovery must not repeat verbatim"))
    monkeypatch.setattr(client.app.state.rt.chat, "_quick_answer", quick)
    path = f"/api/chat/sessions/{sid}/messages/{msg['id']}/recovery"
    body = {"action": action, "detail": "Уточните шаг регистрации"}
    first = client.post(path, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["answer"].startswith("1.")
    second = client.post(path, json=body)
    assert second.json() == first.json()
    assert agent.await_count == 1
    request = agent.call_args.args[3]
    assert request.response_style == ("steps" if action == "explain" else "clarified")
    assert "Расскажите по шагам" in request.user_text
    assert all(role == "user" for role, _ in request.history)
    if action == "clarify":
        assert msg["content"] not in request.user_text
    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    assert messages[-1]["meta"]["recovery_of"] == msg["id"]
    assert messages[-1]["meta"].get("verbatim") is not True


def test_operator_recovery_uses_classifier_route_and_is_idempotent(client, monkeypatch):
    question = "При формировании УПД возникает ошибка РДИК"
    comment = "РДИК всё ещё возникает"
    reason = f"{question}\nСделал по инструкции, но не работает\n{comment}"
    sid, msg = _answered_message(client, question)
    _rate(client, sid, msg["id"], value=-1, reason="not_working", comment=comment)
    prediction = RoutePrediction(
        line=2,
        proba=0.94,
        source="topics",
        topic=lines.EXECUTION_EIS,
        topic_proba=0.94,
    )
    classifier = AsyncMock(return_value=prediction)
    monkeypatch.setattr(client.app.state.rt.classifier, "predict", classifier)
    path = f"/api/chat/sessions/{sid}/messages/{msg['id']}/recovery"

    first = client.post(path, json={"action": "operator"})
    second = client.post(path, json={"action": "operator"})

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "forwarded_to_operator"
    assert first.json()["escalated"] is True
    assert second.json() == first.json()

    async def load_session():
        async with client.app.state.rt.db.session_factory() as db:
            return await db.get(ChatSession, sid)

    session = client.portal.call(load_session)
    assert session.status == "waiting_operator"
    assert session.line == 2
    assert session.topic == lines.EXECUTION_EIS
    assert session.escalation_reason == reason
    classifier.assert_awaited_once()
    classified = classifier.await_args.args[0]
    assert reason in classified and "предыдущий ответ не помог" in classified
    assert classifier.await_args.kwargs == {"audience": "supplier", "dyn": client.app.state.rt.dyn}


def test_closed_session_cannot_start_recovery(client, admin_headers):
    sid, msg = _answer(client)
    _rate(client, sid, msg["id"], value=-1)
    client.portal.call(client.app.state.rt.chat.operator_close, sid)
    response = client.post(
        f"/api/chat/sessions/{sid}/messages/{msg['id']}/recovery", json={"action": "explain"}
    )
    assert response.status_code == 409


@pytest.mark.parametrize("action", ["clarify", "explain"])
def test_recovery_keeps_current_audience(client, monkeypatch, action):
    sid, msg = _answer(client)
    _rate(client, sid, msg["id"], value=-1, reason="irrelevant")
    if action == "explain":
        client.post(f"/api/chat/sessions/{sid}/audience", json={"audience": "customer"})
    agent = AsyncMock(return_value={"status": "answered", "answer": "Инструкция для заказчика"})
    monkeypatch.setattr(client.app.state.rt.chat, "_agent_answer", agent)
    result = client.post(
        f"/api/chat/sessions/{sid}/messages/{msg['id']}/recovery",
        json={"action": action, "detail": "Я заказчик, как подписать контракт со своей стороны?"},
    )
    assert result.status_code == 200, result.text
    assert agent.call_args.args[0].role == "customer"


def test_resolution_rejects_blank_note(client, admin_headers):
    sid, msg = _answer(client)
    _rate(client, sid, msg["id"], value=-1)
    response = client.post(
        f"/api/admin/answer-feedback/{msg['id']}/resolve",
        json={
            "article": {
                "question": "Контрольная статья",
                "answer": "Проверенный ответ",
                "category": "Проверки",
            },
            "note": "   ",
        },
        headers=admin_headers,
    )
    assert response.status_code == 422
