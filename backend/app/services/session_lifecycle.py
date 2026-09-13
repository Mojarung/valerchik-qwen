"""Автоматическое завершение неактивных диалогов с ИИ."""

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import exists, func, select

from app.models import AuditLog, ChatSession, Message

if TYPE_CHECKING:
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

# За один проход закрываем ограниченную пачку: диспетчер держит короткий
# распределённый лок, а накопившиеся старые диалоги доберутся на следующих тиках.
IDLE_CLOSE_BATCH = 100


async def close_idle_sessions(rt: AppRuntime) -> list[str]:
    """Завершить содержательные AI-диалоги после настраиваемой паузы.

    Только ``active``: очередь, назначение и разговор с оператором имеют свой
    жизненный цикл, и тишина там не означает, что обращение можно потерять.
    Сессию с одним приветствием не закрываем — пользователь ещё не начинал чат.
    """
    idle_seconds = int(rt.dyn.get("chat.idle_close_seconds") or 0)
    if idle_seconds <= 0:
        return []

    now = datetime.now(UTC)
    deadline = now - timedelta(seconds=idle_seconds)
    last_message_at = (
        select(func.max(Message.created_at))
        .where(Message.session_id == ChatSession.id)
        .correlate(ChatSession)
        .scalar_subquery()
    )
    has_user_message = exists(
        select(Message.id).where(Message.session_id == ChatSession.id, Message.role == "user")
    )
    closed: list[tuple[str, Message]] = []

    async with rt.db.session_factory() as db:
        sessions = (
            (
                await db.execute(
                    select(ChatSession)
                    .where(
                        ChatSession.status == "active",
                        has_user_message,
                        last_message_at <= deadline,
                    )
                    .order_by(last_message_at)
                    .limit(IDLE_CLOSE_BATCH)
                )
            )
            .scalars()
            .all()
        )
        if not sessions:
            return []

        request = str(rt.dyn.get("feedback.request_message"))
        for session in sessions:
            session.status = "resolved"
            session.closed_at = now
            message = Message(
                session_id=session.id,
                role="system",
                content=request,
                meta={"kind": "feedback_request", "reason": "idle"},
            )
            db.add(message)
            db.add(
                AuditLog(
                    actor="system",
                    action="session.idle_closed",
                    entity=session.id,
                    payload={"idle_seconds": idle_seconds},
                )
            )
            closed.append((session.id, message))
        await db.commit()

    for session_id, message in closed:
        await rt.ws.publish(f"session:{session_id}", rt.chat._msg_event(message))
        # Для клиента это обычное успешное завершение: он видит карточку оценки
        # и может начать новый диалог. Техническая причина остаётся в meta и audit.
        await rt.ws.publish(
            f"session:{session_id}", {"type": "session_closed", "reason": "resolved"}
        )
        log.info("Диалог %s завершён после %d с без активности", session_id, idle_seconds)

    return [session_id for session_id, _ in closed]
