"""Диспетчер очереди: кто из операторов сейчас на смене и кому уходит обращение,
которое никто не разобрал.

Общая очередь работает, пока в ней кто-то копается. Если все заняты или отвлеклись,
обращение висит без адресата: оно всем показано и потому ничьё. Поэтому после паузы
оно назначается конкретному оператору — у обращения появляется владелец, а у
оператора уведомление.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select

from app.models import ChatSession, User

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

# ZSET «кто на связи»: элемент — id оператора, вес — время последнего сигнала.
PRESENCE_KEY = "operators:online"
# Сигнал шлёт каждое живое websocket-соединение операторской. Порог втрое больше
# периода: одна пропущенная отметка (перезапуск api, микро-разрыв сети) не должна
# выкидывать человека со смены.
PRESENCE_TTL_SECONDS = 90
PRESENCE_BEAT_SECONDS = 30

# Лок на проход диспетчера: при `--scale api=3` цикл крутится в каждом инстансе,
# и без него одно и то же обращение назначали бы трижды.
LOCK_KEY = "operators:autoassign:lock"
LOCK_TTL_SECONDS = 20


async def mark_online(cache: Redis, operator_id: str) -> None:
    await cache.zadd(PRESENCE_KEY, {operator_id: time.time()})


async def mark_offline(cache: Redis, operator_id: str) -> None:
    await cache.zrem(PRESENCE_KEY, operator_id)


async def online_operator_ids(cache: Redis) -> list[str]:
    """Кто на смене. Протухшие отметки убираем здесь же: отдельного уборщика
    заводить незачем, а вкладку оператора могли закрыть по-живому."""
    deadline = time.time() - PRESENCE_TTL_SECONDS
    await cache.zremrangebyscore(PRESENCE_KEY, "-inf", deadline)
    # Клиент создан с decode_responses=True: участники множества — строки
    return cast(list[str], await cache.zrange(PRESENCE_KEY, 0, -1))


async def autoassign_pending(rt: AppRuntime) -> list[dict[str, Any]]:
    """Assign within the operator's line, balancing load without a hard capacity limit."""
    from app.services.support_flow import changed, emit, lock_session

    if not rt.dyn.get("operator.autoassign_enabled"):
        return []
    online = await online_operator_ids(rt.cache)
    if not online:
        return []
    wait = int(rt.dyn.get("operator.autoassign_after_seconds") or 0)
    if wait <= 0:
        return []
    deadline = datetime.now(UTC) - timedelta(seconds=wait)
    assigned = []
    async with rt.db.session_factory() as db:
        users = (
            (
                await db.execute(
                    select(User)
                    .where(User.id.in_(online), User.is_active.is_(True), User.role == "operator")
                    .order_by(User.id)
                )
            )
            .scalars()
            .all()
        )
        ids = (
            (
                await db.execute(
                    select(ChatSession.id)
                    .where(
                        ChatSession.status == "waiting_operator",
                        ChatSession.operator_id.is_(None),
                        ChatSession.escalated_at <= deadline,
                    )
                    .order_by(ChatSession.escalated_at)
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        for sid in ids:
            s = await lock_session(db, sid)
            if s.status != "waiting_operator" or s.operator_id:
                continue
            rows = (
                await db.execute(
                    select(ChatSession.operator_id, func.count(ChatSession.id))
                    .where(ChatSession.status.in_(("operator_assigned", "operator_active", "waiting_user")))
                    .group_by(ChatSession.operator_id)
                )
            ).all()
            load = dict(rows)
            candidates = [
                u
                for u in users
                if u.line == s.line and u.id != s.last_operator_id
            ]
            if not candidates:
                continue
            user = min(candidates, key=lambda u: (load.get(u.id, 0), u.id))
            s.status, s.operator_id, s.assigned_at = "operator_assigned", user.id, datetime.now(UTC)
            item = {
                "session_id": sid,
                "operator_id": user.id,
                "operator_name": user.display_name,
                "line": s.line,
                "reason": s.escalation_reason,
                "question": s.escalation_reason,
                "waiting_seconds": int((datetime.now(UTC) - s.escalated_at).total_seconds()),
            }
            changed(db, s, "dispatcher", "assign")
            emit(db, sid, f"operator:{user.id}", {"type": "assigned", **item})
            assigned.append(item)
        await db.commit()
    return assigned


async def reclaim_stalled(rt: AppRuntime) -> list[dict[str, Any]]:
    """Only unaccepted assignments expire. Silence while working is not abandonment."""
    from app.services.support_flow import expire_assignments

    return await expire_assignments(rt)
