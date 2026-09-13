"""Acceptance scenarios for ownership, routing, delivery and the full support lifecycle."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.models import ChatSession, Message, SupportEvent, User, new_id, utcnow
from app.security import create_token
from app.services import dispatch, support_flow


@pytest.fixture
def desk(client):
    async def setup():
        client.app.state.rt.dyn.override("rate_limit.enabled", False)
        async with client.app.state.rt.db.session_factory() as db:
            await db.execute(
                update(ChatSession)
                .where(ChatSession.status.in_(support_flow.SUPPORT_STATUSES))
                .values(status="resolved")
            )
            people = [
                User(
                    id=new_id(),
                    username=new_id(),
                    password_hash="unused",
                    display_name=f"Сотрудник {i}",
                    role="operator" if i < 3 else "admin",
                    line=1 if i != 2 else 2,
                )
                for i in range(4)
            ]
            db.add_all(people)
            await db.commit()
        return [(u.id, {"Authorization": f"Bearer {create_token(u.id, u.username, u.role)}"}) for u in people]

    return client.portal.call(setup)


def ticket(client, line=1):
    sid = client.post("/api/chat/sessions").json()["session_id"]

    async def seed():
        async with client.app.state.rt.db.session_factory() as db:
            s = await db.get(ChatSession, sid)
            s.status, s.line, s.escalated_at = "waiting_operator", line, utcnow() - timedelta(minutes=4)
            s.escalation_reason = "Не получается зарегистрироваться"
            db.add(
                Message(session_id=sid, role="user", content="Как зарегистрироваться?", meta={}, citations=[])
            )
            await db.commit()

    client.portal.call(seed)
    return sid


def act(client, sid, actor, action, **body):
    return client.post(f"/api/operator/sessions/{sid}/actions/{action}", headers=actor[1], json=body)


def test_confirmed_resolution_and_reopen(client, desk):
    sid = ticket(client)
    assert act(client, sid, desk[0], "take").status_code == 200
    assert client.post(
        f"/api/operator/sessions/{sid}/message",
        headers=desk[0][1],
        json={"text": "Проверьте вход после обновления сертификата"},
    ).status_code == 200
    assert act(client, sid, desk[0], "resolve").json()["status"] == "waiting_user"
    response = client.post(f"/api/chat/sessions/{sid}/solution", json={"solved": False})
    assert response.status_code == 200
    assert response.json()["status"] == "waiting_operator"
    assert act(client, sid, desk[1], "take").status_code == 200
    assert client.post(
        f"/api/operator/sessions/{sid}/message",
        headers=desk[1][1],
        json={"text": "Попробуйте ещё раз"},
    ).status_code == 200
    assert act(client, sid, desk[1], "resolve").status_code == 200
    assert (
        client.post(f"/api/chat/sessions/{sid}/solution", json={"solved": True}).json()["status"]
        == "resolved"
    )
    assert client.post(f"/api/chat/sessions/{sid}/solution", json={"solved": True}).status_code == 409


def test_one_owner_under_concurrent_take(client, desk):
    sid = ticket(client)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda actor: act(client, sid, actor, "take").status_code, desk[:2]))
    assert sorted(results) == [200, 409]


@pytest.mark.parametrize("path", ["messages", "suggestions", "draft"])
def test_other_line_cannot_read(client, desk, path):
    sid = ticket(client)
    method = client.post if path == "draft" else client.get
    assert method(f"/api/operator/sessions/{sid}/{path}", headers=desk[2][1]).status_code == 403


@pytest.mark.parametrize(
    "action,body",
    [
        ("transfer", {"line": 2, "reason": "Технический сбой"}),
        ("wait", {"reason": "Пришлите скриншот"}),
        ("resolve", {"reason": "Проверьте решение"}),
        ("decline", {}),
    ],
)
def test_nonowner_cannot_change(client, desk, action, body):
    sid = ticket(client)
    act(client, sid, desk[0], "take")
    assert act(client, sid, desk[1], action, **body).status_code == 403


def test_transfer_preserves_history_and_wait(client, desk):
    sid = ticket(client)
    act(client, sid, desk[0], "take")
    before = client.portal.call(client.app.state.rt.chat._get_session, sid)
    assert act(client, sid, desk[0], "transfer", line=2).status_code == 200
    after = client.portal.call(client.app.state.rt.chat._get_session, sid)
    assert before.escalated_at == after.escalated_at
    assert after.operator_id is None and after.line == 2
    assert act(client, sid, desk[2], "take").status_code == 200
    assert len(client.get(f"/api/operator/sessions/{sid}/messages", headers=desk[2][1]).json()) >= 4


def test_user_information_returns_to_work(client, desk):
    sid = ticket(client)
    act(client, sid, desk[0], "take")
    act(client, sid, desk[0], "wait", reason="Какой браузер?")
    response = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Использую Firefox"})
    assert response.status_code == 200, response.text
    assert client.get(f"/api/chat/sessions/{sid}").json()["status"] == "operator_active"


def test_idempotent_operator_message(client, desk):
    sid = ticket(client)
    act(client, sid, desk[0], "take")
    url = f"/api/operator/sessions/{sid}/message"
    body = {"text": "Пришлите скриншот ошибки", "request_id": "retry-1"}
    assert client.post(url, json=body, headers=desk[0][1]).status_code == 200
    assert client.post(url, json=body, headers=desk[0][1]).status_code == 200
    assert client.post(url, json={**body, "text": "Другой текст"}, headers=desk[0][1]).status_code == 409
    messages = client.get(f"/api/operator/sessions/{sid}/messages", headers=desk[0][1]).json()
    assert sum(m["content"] == body["text"] for m in messages) == 1


def test_version_conflict_reports_current_state(client, desk):
    sid = ticket(client)
    taken = act(client, sid, desk[0], "take").json()
    response = act(client, sid, desk[0], "wait", version=taken["version"] - 1, reason="Уточните браузер")
    assert response.status_code == 409
    assert response.json()["detail"]["state"]["status"] == "operator_active"


def test_assign_without_capacity_limit_preserves_line_and_expiry(client, desk):
    rt = client.app.state.rt
    ids = [ticket(client) for _ in range(5)]

    async def run():
        await rt.cache.delete(dispatch.PRESENCE_KEY)
        await dispatch.mark_online(rt.cache, desk[0][0])
        await dispatch.mark_online(rt.cache, desk[2][0])
        assignments = await dispatch.autoassign_pending(rt)
        assert len(assignments) == len(ids)
        assert {a["operator_id"] for a in assignments} == {desk[0][0]}
        async with rt.db.session_factory() as db:
            rows = (await db.execute(select(ChatSession).where(ChatSession.id.in_(ids)))).scalars().all()
            for s in rows:
                if s.status == "operator_assigned":
                    s.assigned_at = utcnow() - timedelta(seconds=70)
            await db.commit()
        expired = await dispatch.reclaim_stalled(rt)
        assert len(expired) == len(ids)
        again = await dispatch.autoassign_pending(rt)
        assert not ({a["session_id"] for a in again} & {a["session_id"] for a in expired})

    client.portal.call(run)


def test_visitor_retry_is_durable_and_has_one_message(client, desk):
    sid = ticket(client)
    url = f"/api/chat/sessions/{sid}/messages"
    body = {"text": "Ошибка воспроизводится в Firefox", "request_id": "visitor-retry-1"}
    first = client.post(url, json=body)
    assert first.status_code == 200
    assert client.post(url, json=body).json() == first.json()
    assert client.post(url, json={**body, "text": "Другой вопрос"}).status_code == 409
    history = client.get(url).json()
    assert sum(m["content"] == body["text"] for m in history) == 1


def test_reassign_requires_admin_and_matching_line_but_not_reason(client, desk):
    sid = ticket(client)
    act(client, sid, desk[0], "take")
    assert act(client, sid, desk[1], "reassign", operator_id=desk[1][0], reason="Смена").status_code == 403
    assert act(client, sid, desk[3], "reassign", operator_id=desk[2][0], reason="Смена").status_code == 422
    result = act(client, sid, desk[3], "reassign", operator_id=desk[1][0])
    assert result.status_code == 200 and result.json()["status"] == "operator_assigned"
    assert act(client, sid, desk[0], "resolve", reason="Решение").status_code == 403
    assert act(client, sid, desk[1], "take").status_code == 200


def test_take_and_reassign_allow_more_than_three_owned_tickets(client, desk):
    for _ in range(5):
        sid = ticket(client)
        assert act(client, sid, desk[0], "take").status_code == 200
    for _ in range(5):
        sid = ticket(client)
        result = act(client, sid, desk[3], "reassign", operator_id=desk[0][0], reason="Помощь с очередью")
        assert result.status_code == 200
        assert act(client, sid, desk[0], "take").status_code == 200


def test_solution_does_not_close_on_silence_and_must_be_a_solution(client, desk):
    sid = ticket(client)
    act(client, sid, desk[0], "take")
    act(client, sid, desk[0], "wait", reason="Какой браузер?")
    assert client.post(f"/api/chat/sessions/{sid}/solution", json={"solved": True}).status_code == 409
    client.portal.call(dispatch.reclaim_stalled, client.app.state.rt)
    assert client.get(f"/api/chat/sessions/{sid}").json()["status"] == "waiting_user"


def test_copilot_work_cannot_block_customer_message(client, desk, monkeypatch):
    import asyncio
    import time

    async def slow(*args, **kwargs):
        await asyncio.sleep(5)

    sid = ticket(client)
    monkeypatch.setattr(client.app.state.rt.chat, "_push_copilot", slow)
    started = time.monotonic()
    assert client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Дополнение"}).status_code == 200
    assert time.monotonic() - started < 2


def test_active_silence_does_not_reclaim(client, desk):
    sid = ticket(client)
    act(client, sid, desk[0], "take")
    client.portal.call(dispatch.reclaim_stalled, client.app.state.rt)
    assert client.get(f"/api/chat/sessions/{sid}").json()["status"] == "operator_active"


def test_unread_and_foreign_read_marker(client, desk):
    sid, other = ticket(client), ticket(client)
    act(client, sid, desk[0], "take")
    foreign = client.get(f"/api/chat/sessions/{other}/messages").json()[-1]["id"]
    assert client.post(f"/api/chat/sessions/{sid}/read", json={"message_id": foreign}).status_code == 422
    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    assert client.get(f"/api/chat/sessions/{sid}/support").json()["unread"] > 0
    assert (
        client.post(f"/api/chat/sessions/{sid}/read", json={"message_id": messages[-1]["id"]}).status_code
        == 200
    )
    assert client.get(f"/api/chat/sessions/{sid}/support").json()["unread"] == 0


def test_outbox_survives_redis_failure(client, desk, monkeypatch):
    sid = ticket(client)
    act(client, sid, desk[0], "take")
    rt = client.app.state.rt

    async def fail(*args, **kwargs):
        raise ConnectionError("offline")

    async def run():
        with monkeypatch.context() as m:
            m.setattr(rt.cache, "publish", fail)
            with pytest.raises(ConnectionError):
                await support_flow.flush_outbox(rt)
        async with rt.db.session_factory() as db:
            pending = (
                (
                    await db.execute(
                        select(SupportEvent).where(
                            SupportEvent.session_id == sid, SupportEvent.delivered_at.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert pending
        await support_flow.flush_outbox(rt)

    client.portal.call(run)
