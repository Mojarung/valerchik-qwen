"""Рабочее место оператора вне HTTP-слоя: очередь обращений и шаблоны ответов."""

from datetime import UTC, datetime

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.models import ChatSession, Message, QuickReply, User
from app.schemas import QuickReplyIn
from app.schemas.operator import QueueItem, QuickReplyOut

#: Сколько обращений показывать в очереди: свежие сверху.
QUEUE_LIMIT = 100
#: «Очередь» — ждущие оператора и уже взятые в работу.
QUEUE_STATUSES = ("waiting_operator", "operator_assigned", "operator_active", "waiting_user")


async def _last_messages(db: AsyncSession, session_ids: list[str]) -> dict[str, Message]:
    """Последнее сообщение каждой сессии одним запросом.

    Оконная функция, а не GROUP BY: нужна вся строка, а не только время. На
    SQLite (lite-режим и тесты) row_number() тоже поддерживается — с 3.25.
    """
    if not session_ids:
        return {}
    ranked = (
        select(
            Message,
            func.row_number()
            .over(
                partition_by=Message.session_id,
                order_by=desc(Message.created_at),
            )
            .label("rn"),
        )
        .where(Message.session_id.in_(session_ids))
        .subquery()
    )
    last = aliased(Message, ranked)
    rows = (await db.execute(select(last).where(ranked.c.rn == 1))).scalars().all()
    return {m.session_id: m for m in rows}


def _preview(message: Message | None) -> str:
    """Строка последней реплики для очереди.

    У сообщения-вложения текста может не быть вовсе (оператор отправил скриншот
    без подписи) — тогда в очереди была бы пустая строка. Показываем, что это
    файл, и как он называется.
    """
    if message is None:
        return ""
    if message.content.strip():
        return message.content[:140]
    attachment = (message.meta or {}).get("attachment") or {}
    name = attachment.get("name")
    return f"📎 {name}"[:140] if name else ""


async def queue(
    session_factory: async_sessionmaker[AsyncSession],
    status: str = "queue",
    line: int | None = None,
    *,
    actor: str | None = None,
) -> list[QueueItem]:
    """Обращения для очереди: «queue» — ждущие и взятые, «all» — все, иначе —
    с этим статусом; line сужает до линии поддержки."""
    stmt = select(ChatSession).order_by(desc(ChatSession.created_at)).limit(QUEUE_LIMIT)
    if status == "queue":
        stmt = stmt.where(ChatSession.status.in_(QUEUE_STATUSES))
    elif status != "all":
        stmt = stmt.where(ChatSession.status == status)
    if line:
        stmt = stmt.where(ChatSession.line == line)
    is_admin = False
    async with session_factory() as db:
        if actor:
            user = await db.get(User, actor)
            if not user or not user.is_active:
                return []
            is_admin = user.role == "admin"
            if user.role != "admin":
                if user.line is None:
                    return []
                stmt = stmt.where(ChatSession.line == user.line)
            if status == "operator_assigned":
                stmt = stmt.where(ChatSession.operator_id == actor)
        sessions = (await db.execute(stmt)).scalars().all()
        # Последние реплики — одним запросом на всю выдачу. Раньше здесь был
        # SELECT на каждую сессию, то есть до ста round-trip'ов; очередь
        # перезапрашивается у каждого оператора на любое событие в комнате,
        # и один проход диспетчера превращал это в тысячи запросов.
        last_by_session = await _last_messages(db, [s.id for s in sessions])
    now = datetime.now(UTC)
    items = []
    for s in sessions:
        last = last_by_session.get(s.id)
        items.append(
            QueueItem(
                session_id=s.id,
                status=s.status,
                version=s.workflow_version or 0,
                waiting_reason=s.waiting_reason,
                routing=s.routing or {},
                audience=s.audience or "all",
                assigned_at=s.assigned_at.isoformat() if s.assigned_at else None,
                available_actions=(
                    ["take"]
                    if s.status == "waiting_operator"
                    else ["take", "decline"]
                    if s.status == "operator_assigned" and s.operator_id == actor
                    else ["message", "transfer", "wait", "resolve"]
                    if s.status in ("operator_active", "waiting_user") and s.operator_id == actor
                    else []
                )
                + (["reassign"] if is_admin and s.status in QUEUE_STATUSES else []),
                line=s.line,
                operator_id=s.operator_id,
                reason=s.escalation_reason,
                created_at=s.created_at.isoformat(),
                escalated_at=s.escalated_at.isoformat() if s.escalated_at else None,
                waiting_seconds=int((now - s.escalated_at).total_seconds()) if s.escalated_at else None,
                reclaimed_count=s.reclaimed_count or 0,
                last_message=_preview(last),
                last_role=last.role if last else "",
            )
        )
    return items


async def quick_replies(session_factory: async_sessionmaker[AsyncSession]) -> list[QuickReplyOut]:
    """Шаблоны ответов оператора, частые — сверху."""
    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(QuickReply)
                    .where(QuickReply.audience == "operator")
                    .order_by(desc(QuickReply.usage_count))
                )
            )
            .scalars()
            .all()
        )
    return [QuickReplyOut(id=r.id, title=r.title, text=r.text, usage_count=r.usage_count) for r in rows]


async def create_quick_reply(session_factory: async_sessionmaker[AsyncSession], body: QuickReplyIn) -> str:
    async with session_factory() as db:
        row = QuickReply(title=body.title, text=body.text, audience=body.audience, line=body.line)
        db.add(row)
        await db.commit()
        return row.id


async def count_quick_reply_use(session_factory: async_sessionmaker[AsyncSession], reply_id: str) -> None:
    """Учесть использование шаблона: частые поднимаются в списке. Неизвестный id
    молча пропускаем — шаблон могли удалить, пока он висел у оператора на экране."""
    async with session_factory() as db:
        row = await db.get(QuickReply, reply_id)
        if row:
            row.usage_count += 1
            await db.commit()
