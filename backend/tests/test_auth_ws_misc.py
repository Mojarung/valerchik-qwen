"""Аутентификация и роли, WebSocket live-чат, health, голос (выключенный), SPA-метаданные."""

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import login


def test_login_and_roles(client, operator_headers):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401

    headers = login(client, "admin", "admin12345")
    me = client.get("/api/auth/me", headers=headers).json()
    assert (me["role"], me["line"]) == ("admin", None)
    # оператор видит, за какую линию отвечает
    assert client.get("/api/auth/me", headers=operator_headers).json()["line"] == 1

    # у оператора нет доступа к админским ручкам
    assert client.get("/api/admin/settings", headers=operator_headers).status_code == 403
    # аноним не попадает в операторские
    assert client.get("/api/operator/sessions").status_code == 401


def test_ws_chat_streams_answer(client):
    sid = client.post("/api/chat/sessions").json()["session_id"]
    with client.websocket_connect(f"/ws/chat/{sid}") as ws:
        ws.send_json({"type": "message", "text": "Как восстановить доступ к личному кабинету?"})
        got_chunks = 0
        answer = None
        for _ in range(200):
            event = ws.receive_json()
            if event["type"] == "assistant_chunk":
                got_chunks += 1
            if event["type"] == "message" and event["message"]["role"] == "assistant":
                answer = event["message"]
                break
        assert got_chunks >= 2, "ответ должен стримиться кусками"
        assert answer and answer["citations"]


def test_ws_operator_requires_token(client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/operator"):
            pass
    assert exc_info.value.code == 4401


def test_ws_operator_receives_ticket(client):
    # Сокет читает httpOnly-куку: токен в query-строке больше не принимается.
    client.post("/api/auth/login", json={"username": "op_maria", "password": "operator12345"})
    try:
        with client.websocket_connect("/ws/operator", headers={"origin": "http://testserver"}) as ws:
            sid = client.post("/api/chat/sessions").json()["session_id"]
            client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "нужен оператор срочно"})
            for _ in range(50):
                event = ws.receive_json()
                if event.get("type") == "ticket_changed" and event["session_id"] == sid:
                    break
            else:
                raise AssertionError("оператор не получил уведомление о новом тикете")
    finally:
        client.post("/api/auth/logout", headers={"X-Requested-With": "valerchik"})


def test_health_and_meta(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["checks"]["db"] == "ok"
    assert int(health["checks"]["vector_index"]) >= 50
    # Точек не меньше, чем статей: у статьи бывают ещё и формулировки вопроса
    assert int(health["checks"]["vector_points"]) >= int(health["checks"]["vector_index"])

    meta = client.get("/api/meta").json()
    assert meta["mode"] == "lite"
    assert meta["voice_available"] is False  # STT не настроен в lite
    assert "1" in meta["lines"]
    # чат сам предлагает оценку после паузы — параметры приходят с метой
    assert meta["feedback"]["idle_prompt_seconds"] > 0
    assert meta["feedback"]["request_message"]


def test_idle_feedback_prompt_is_tunable(client, admin_headers):
    """Паузу и текст просьбы крутят в админке, фронт читает их из меты."""
    resp = client.put(
        "/api/admin/settings", headers=admin_headers, json={"updates": {"feedback.idle_prompt_seconds": 45}}
    )
    assert resp.status_code == 200
    assert client.get("/api/meta").json()["feedback"]["idle_prompt_seconds"] == 45

    # 0 — не предлагать вовсе
    client.put(
        "/api/admin/settings", headers=admin_headers, json={"updates": {"feedback.idle_prompt_seconds": 0}}
    )
    assert client.get("/api/meta").json()["feedback"]["idle_prompt_seconds"] == 0

    client.put(
        "/api/admin/settings", headers=admin_headers, json={"updates": {"feedback.idle_prompt_seconds": 120}}
    )


def test_voice_disabled_returns_503(client):
    resp = client.post("/api/voice/transcribe", files={"file": ("a.webm", b"fake-bytes", "audio/webm")})
    assert resp.status_code == 503


def test_metrics_endpoint(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "app_requests_total" in resp.text


def test_ws_chat_respects_rate_limit(client, admin_headers):
    """Лимит общий с REST: перейти на сокет и обойти ограничение нельзя.

    Раньше WS-путь не проверял лимит вовсе — а сообщение стоит одинаково в
    обоих каналах, включая guardrails и вызов LLM.
    """
    # Диалог заводим ДО понижения лимита: создание сессии считается тем же
    # счётчиком, и на порезанном лимите тест падал бы на подготовке.
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.put(
        "/api/admin/settings", headers=admin_headers, json={"updates": {"rate_limit.chat_per_minute": 2}}
    )
    try:
        with client.websocket_connect(f"/ws/chat/{sid}") as ws:
            denied = None
            for attempt in range(6):
                ws.send_json({"type": "message", "text": f"Что такое СТЕ? Попытка {attempt}"})
                for _ in range(200):
                    event = ws.receive_json()
                    if event["type"] == "error":
                        denied = event["detail"]
                        break
                    if event["type"] == "message" and event["message"]["role"] == "assistant":
                        break
                if denied:
                    break
            assert denied and "Лимит" in denied, "сокет обязан упираться в тот же лимит"
    finally:
        client.put(
            "/api/admin/settings", headers=admin_headers, json={"updates": {"rate_limit.chat_per_minute": 20}}
        )


async def test_sqlite_runs_in_wal(client):
    """lite-режим держит журнал в WAL: иначе долгий SELECT (выгрузка диалогов)
    блокирует запись сообщений в чат на всё время чтения."""
    from sqlalchemy import text

    rt = client.app.state.rt
    if not str(rt.db.engine.url).startswith("sqlite"):
        return
    async with rt.db.session_factory() as db:
        mode = (await db.execute(text("PRAGMA journal_mode"))).scalar()
    assert mode == "wal", f"журнал должен быть WAL, а не {mode}"
