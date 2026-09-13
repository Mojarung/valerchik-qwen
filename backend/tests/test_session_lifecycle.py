"""Неактивный AI-диалог завершается сервером, даже если вкладка закрыта."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models import AuditLog, ChatSession, Message
from app.services.session_lifecycle import close_idle_sessions


@pytest.fixture
async def rt(client):
    async with client.app.state.rt.db.session_factory() as db:
        await db.execute(update(ChatSession).values(status="resolved"))
        await db.commit()
    await client.app.state.rt.dyn.set_many({"chat.idle_close_seconds": 1800}, actor="test")
    return client.app.state.rt


async def _dialog(rt, *, status: str = "active", seconds_ago: int = 3600, with_user: bool = True) -> str:
    at = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    async with rt.db.session_factory() as db:
        session = ChatSession(status=status, channel="web", created_at=at)
        db.add(session)
        await db.flush()
        db.add(Message(session_id=session.id, role="assistant", content="Здравствуйте", created_at=at))
        if with_user:
            db.add(Message(session_id=session.id, role="user", content="Вопрос", created_at=at))
            db.add(Message(session_id=session.id, role="assistant", content="Ответ", created_at=at))
        await db.commit()
        return session.id


async def _status(rt, session_id: str) -> tuple[str, datetime | None]:
    async with rt.db.session_factory() as db:
        session = await db.get(ChatSession, session_id)
        return session.status, session.closed_at


async def test_closes_active_dialog_after_idle_timeout(rt):
    session_id = await _dialog(rt)

    assert await close_idle_sessions(rt) == [session_id]
    status, closed_at = await _status(rt, session_id)
    assert status == "resolved"
    assert closed_at is not None

    async with rt.db.session_factory() as db:
        request = (
            (
                await db.execute(
                    select(Message).where(
                        Message.session_id == session_id,
                        Message.role == "system",
                    )
                )
            )
            .scalars()
            .one()
        )
        audit = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.action == "session.idle_closed",
                        AuditLog.entity == session_id,
                    )
                )
            )
            .scalars()
            .one()
        )
    assert request.meta == {"kind": "feedback_request", "reason": "idle"}
    assert audit.payload == {"idle_seconds": 1800}


async def test_leaves_fresh_empty_and_operator_dialogs_open(rt):
    fresh = await _dialog(rt, seconds_ago=60)
    empty = await _dialog(rt, with_user=False)
    waiting = await _dialog(rt, status="waiting_operator")
    assigned = await _dialog(rt, status="operator_assigned")
    operator = await _dialog(rt, status="operator_active")

    assert await close_idle_sessions(rt) == []
    for session_id in (fresh, empty, waiting, assigned, operator):
        status, closed_at = await _status(rt, session_id)
        assert status != "resolved"
        assert closed_at is None


async def test_zero_disables_idle_close(rt):
    session_id = await _dialog(rt)
    await rt.dyn.set_many({"chat.idle_close_seconds": 0}, actor="test")
    try:
        assert await close_idle_sessions(rt) == []
        assert await _status(rt, session_id) == ("active", None)
    finally:
        await rt.dyn.set_many({"chat.idle_close_seconds": 1800}, actor="test")
