"""Журнал аудита: кто и что поменял в админке."""

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditLog
from app.schemas.admin import AuditEntry

SessionFactory = async_sessionmaker[AsyncSession]
#: Записей на страницу журнала.
PAGE_SIZE = 50


async def record(
    session_factory: SessionFactory, *, actor: str, action: str, entity: str, payload: dict[str, Any]
) -> None:
    async with session_factory() as db:
        db.add(AuditLog(actor=actor, action=action, entity=entity, payload=payload))
        await db.commit()


async def recent(session_factory: SessionFactory, page: int = 1) -> list[AuditEntry]:
    """Записи журнала, свежие сверху, по PAGE_SIZE на страницу."""
    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(AuditLog)
                    .order_by(desc(AuditLog.created_at))
                    .offset((page - 1) * PAGE_SIZE)
                    .limit(PAGE_SIZE)
                )
            )
            .scalars()
            .all()
        )
    return [
        AuditEntry(
            actor=a.actor,
            action=a.action,
            entity=a.entity,
            payload=a.payload,
            created_at=a.created_at.isoformat(),
        )
        for a in rows
    ]
