"""Диспетчер очереди: обращение, которое никто не разобрал, назначается оператору."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models import ChatSession, Message, User
from app.services.dispatch import (
    PRESENCE_KEY,
    autoassign_pending,
    mark_offline,
    mark_online,
    online_operator_ids,
    reclaim_stalled,
)


@pytest.fixture
async def rt(client):
    # Independent dispatch workload; both test operators cover this test line.
    async with client.app.state.rt.db.session_factory() as db:
        await db.execute(update(ChatSession).values(status="resolved"))
        await db.execute(update(User).where(User.role == "operator").values(line=1))
        await db.commit()
    return client.app.state.rt


async def _operator_id(rt) -> str:
    async with rt.db.session_factory() as db:
        user = (await db.execute(select(User).where(User.role == "operator"))).scalars().first()
    assert user is not None, "в сиде должен быть оператор"
    return user.id


async def _escalate(rt, *, seconds_ago: int) -> str:
    """Обращение, которое ждёт оператора уже seconds_ago секунд."""
    async with rt.db.session_factory() as db:
        session = ChatSession(
            status="waiting_operator",
            channel="web",
            line=1,
            escalation_reason="Тестовая эскалация",
            escalated_at=datetime.now(UTC) - timedelta(seconds=seconds_ago),
        )
        db.add(session)
        await db.commit()
        return session.id


async def _status(rt, session_id: str) -> tuple[str, str | None]:
    async with rt.db.session_factory() as db:
        session = await db.get(ChatSession, session_id)
        return session.status, session.operator_id


async def test_assigns_stale_ticket_to_online_operator(rt):
    operator_id = await _operator_id(rt)
    session_id = await _escalate(rt, seconds_ago=600)
    await mark_online(rt.cache, operator_id)
    try:
        assigned = await autoassign_pending(rt)
    finally:
        await mark_offline(rt.cache, operator_id)

    assert [a["session_id"] for a in assigned] == [session_id]
    assert assigned[0]["operator_id"] == operator_id
    assert assigned[0]["waiting_seconds"] >= 600
    assert await _status(rt, session_id) == ("operator_assigned", operator_id)


async def test_fresh_ticket_is_left_in_the_queue(rt):
    """Свежее обращение операторы разбирают сами — перехватывать его незачем."""
    operator_id = await _operator_id(rt)
    session_id = await _escalate(rt, seconds_ago=5)
    await mark_online(rt.cache, operator_id)
    try:
        assert await autoassign_pending(rt) == []
    finally:
        await mark_offline(rt.cache, operator_id)
    assert await _status(rt, session_id) == ("waiting_operator", None)


async def test_nobody_online_leaves_ticket_in_common_queue(rt):
    """Назначать отсутствующему хуже, чем оставить всем на виду."""
    await rt.cache.delete(PRESENCE_KEY)
    session_id = await _escalate(rt, seconds_ago=600)
    assert await autoassign_pending(rt) == []
    assert await _status(rt, session_id) == ("waiting_operator", None)


async def test_disabled_by_setting(rt):
    operator_id = await _operator_id(rt)
    session_id = await _escalate(rt, seconds_ago=600)
    await mark_online(rt.cache, operator_id)
    await rt.dyn.set_many({"operator.autoassign_enabled": False}, actor="test")
    try:
        assert await autoassign_pending(rt) == []
        assert await _status(rt, session_id) == ("waiting_operator", None)
    finally:
        await rt.dyn.set_many({"operator.autoassign_enabled": True}, actor="test")
        await mark_offline(rt.cache, operator_id)


async def test_presence_expires(rt):
    """Отметка присутствия протухает: вкладку могли закрыть по-живому."""
    operator_id = await _operator_id(rt)
    await mark_online(rt.cache, operator_id)
    assert operator_id in await online_operator_ids(rt.cache)

    # сдвигаем последний сигнал далеко в прошлое
    await rt.cache.zadd(PRESENCE_KEY, {operator_id: 0})
    assert operator_id not in await online_operator_ids(rt.cache)


async def test_load_is_spread_across_operators(rt):
    """Два обращения на двух свободных операторов — по одному каждому."""
    async with rt.db.session_factory() as db:
        operators = (await db.execute(select(User).where(User.role == "operator").limit(2))).scalars().all()
    if len(operators) < 2:
        pytest.skip("в сиде один оператор")
    ids = [o.id for o in operators]
    sessions = [await _escalate(rt, seconds_ago=600) for _ in range(2)]
    for oid in ids:
        await mark_online(rt.cache, oid)
    try:
        assigned = await autoassign_pending(rt)
    finally:
        for oid in ids:
            await mark_offline(rt.cache, oid)

    got = {a["session_id"]: a["operator_id"] for a in assigned}
    # В очереди могли остаться обращения от прошлых тестов модуля — важно, что
    # наши тоже разобраны и что нагрузка размазана, а не свалена на одного.
    assert set(got) >= set(sessions)
    assert len(set(got.values())) == 2, "все обращения ушли одному оператору"


# --- возврат брошенного обращения ---


async def _taken(rt, operator_id: str, *, last_role: str, silence_seconds: int) -> str:
    """Диалог в работе у оператора, где последним написал last_role."""
    async with rt.db.session_factory() as db:
        session = ChatSession(
            status="operator_active",
            channel="web",
            line=1,
            operator_id=operator_id,
            escalation_reason="Тестовая эскалация",
            escalated_at=datetime.now(UTC) - timedelta(seconds=silence_seconds + 60),
        )
        db.add(session)
        await db.flush()
        db.add(
            Message(
                session_id=session.id,
                role=last_role,
                content="последняя реплика",
                created_at=datetime.now(UTC) - timedelta(seconds=silence_seconds),
            )
        )
        await db.commit()
        return session.id


async def test_never_reclaims_active_dialog_for_silence(rt):
    operator_id = await _operator_id(rt)
    session_id = await _taken(rt, operator_id, last_role="user", silence_seconds=900)

    released = await reclaim_stalled(rt)

    assert session_id not in [r["session_id"] for r in released]
    assert await _status(rt, session_id) == ("operator_active", operator_id)
    async with rt.db.session_factory() as db:
        session = await db.get(ChatSession, session_id)
        assert session.reclaimed_count == 0
        assert session.escalated_at is not None, "время начала ожидания сбрасывать нельзя"


async def test_silence_after_operator_reply_is_not_a_stall(rt):
    """Последним написал оператор — ход за клиентом, и молчать он может сколько угодно.

    Без этого различия под возврат попадал бы любой диалог, где оператор всё
    объяснил и ждёт реакции: обращение отняли бы у того, кто отработал.
    """
    operator_id = await _operator_id(rt)
    session_id = await _taken(rt, operator_id, last_role="operator", silence_seconds=9000)

    released = await reclaim_stalled(rt)

    assert session_id not in [r["session_id"] for r in released]
    assert await _status(rt, session_id) == ("operator_active", operator_id)


async def test_reclaim_disabled_by_setting(rt):
    operator_id = await _operator_id(rt)
    session_id = await _taken(rt, operator_id, last_role="user", silence_seconds=900)
    await rt.dyn.set_many({"operator.reclaim_enabled": False}, actor="test")
    try:
        assert await reclaim_stalled(rt) == []
        assert await _status(rt, session_id) == ("operator_active", operator_id)
    finally:
        await rt.dyn.set_many({"operator.reclaim_enabled": True}, actor="test")


async def test_reclaimed_ticket_goes_to_another_operator(rt):
    """Главная защита от круга: диспетчер не отдаёт обращение обратно молчавшему."""
    async with rt.db.session_factory() as db:
        operators = (await db.execute(select(User).where(User.role == "operator").limit(2))).scalars().all()
    if len(operators) < 2:
        pytest.skip("в сиде один оператор")
    silent, other = operators[0].id, operators[1].id

    session_id = await _taken(rt, silent, last_role="user", silence_seconds=900)
    async with rt.db.session_factory() as db:
        s = await db.get(ChatSession, session_id)
        s.status = "operator_assigned"
        s.assigned_at = datetime.now(UTC) - timedelta(seconds=90)
        await db.commit()
    await reclaim_stalled(rt)

    for oid in (silent, other):
        await mark_online(rt.cache, oid)
    try:
        assigned = await autoassign_pending(rt)
    finally:
        for oid in (silent, other):
            await mark_offline(rt.cache, oid)

    got = {a["session_id"]: a["operator_id"] for a in assigned}
    assert got.get(session_id) == other, "обращение вернулось к молчавшему оператору"
