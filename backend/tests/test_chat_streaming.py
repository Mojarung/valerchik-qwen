"""Анимация набора — для того, кто смотрит диалог через сокет, и только для него.

Сервис резал готовый ответ на кусочки и публиковал их с паузой. REST-клиент
(расширение, MCP, curl) ждал все паузы, прежде чем получить ответ, а паузы
входили в latency_ms и в медиану времени ответа на дашборде.
"""

from app.services import chat as chat_service

QUESTION = "Как зарегистрироваться на портале поставщиков?"


def _session(client) -> str:
    return client.post("/api/chat/sessions", json={}).json()["session_id"]


def _record_events(client, monkeypatch) -> list[dict]:
    ws = client.app.state.rt.ws
    events: list[dict] = []
    publish = ws.publish

    async def recording(room: str, event: dict) -> None:
        events.append(event)
        await publish(room, event)

    monkeypatch.setattr(ws, "publish", recording)
    return events


def test_rest_answer_without_watchers_is_not_chopped_into_chunks(client, monkeypatch):
    events = _record_events(client, monkeypatch)
    sid = _session(client)

    resp = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": QUESTION})

    assert resp.status_code == 200
    assert "assistant_chunk" not in [e["type"] for e in events]
    assert any(e["type"] == "message" and e["message"]["role"] == "assistant" for e in events)


def test_latency_excludes_typing_animation(client, monkeypatch):
    monkeypatch.setattr(chat_service, "CHUNK_PAUSE_S", 0.3)
    sid = _session(client)
    with client.websocket_connect(f"/ws/chat/{sid}") as ws:
        resp = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": QUESTION})
        chunks = 0
        for _ in range(200):
            event = ws.receive_json()
            if event["type"] == "assistant_chunk":
                chunks += 1
            if event["type"] == "message" and event["message"]["role"] == "assistant":
                break

    assert chunks >= 2, "тому, кто смотрит через сокет, ответ по-прежнему печатается"
    assert resp.json()["latency_ms"] < chunks * 300
