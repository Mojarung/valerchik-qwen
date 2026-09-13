"""Cross-process ownership and durable UTC provider-call quota for automatic audits."""

from datetime import timedelta
from typing import Any, cast

from sqlalchemy import Table, case, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.db import SessionFactory
from app.models import utcnow
from app.models_conflicts import SourceConflictAutoControl

CONTROL_ID = "automatic"
MAX_DAILY_CALLS = 50000
MAX_LEASE_SECONDS = 7200
MAX_OWNER_LENGTH = 64
MAX_PROVIDER_PAUSE_SECONDS = 3600


def insert_ignoring(session: Session, table: Table, values: dict[str, Any]) -> bool:
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(table).values(**values).on_conflict_do_nothing().returning(table.c.id)
    return session.execute(statement).scalar_one_or_none() is not None


def ensure_control(session: Session) -> None:
    insert_ignoring(session, cast(Table, SourceConflictAutoControl.__table__), {"id": CONTROL_ID})


def owned(owner: str) -> ColumnElement[bool]:
    return exists(
        select(SourceConflictAutoControl.id).where(
            SourceConflictAutoControl.id == CONTROL_ID,
            SourceConflictAutoControl.owner == owner,
            SourceConflictAutoControl.lease_until > utcnow(),
        )
    )


def bounded_limit(daily_limit: int) -> int:
    return min(MAX_DAILY_CALLS, max(0, daily_limit))


def lease_duration(seconds: float) -> timedelta:
    if not 0 < seconds <= MAX_LEASE_SECONDS:
        raise ValueError("Invalid automatic lease duration")
    return timedelta(seconds=seconds)


class SourceConflictAutoControlStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self.session_factory = session_factory

    async def acquire(self, owner: str, lease_seconds: float = 600) -> bool:
        if not owner or len(owner) > MAX_OWNER_LENGTH:
            raise ValueError("Invalid automatic worker owner")
        now = utcnow()
        statement = (
            update(SourceConflictAutoControl)
            .where(
                SourceConflictAutoControl.id == CONTROL_ID,
                provider_ready(),
                or_(
                    SourceConflictAutoControl.lease_until.is_(None),
                    SourceConflictAutoControl.lease_until <= now,
                ),
            )
            .values(owner=owner, lease_until=now + lease_duration(lease_seconds), updated_at=now)
        )
        async with self.session_factory() as db:
            await db.run_sync(ensure_control)
            changed = await db.scalar(statement.returning(SourceConflictAutoControl.id))
            await db.commit()
            return changed is not None

    async def renew(self, owner: str, lease_seconds: float = 600) -> bool:
        async with self.session_factory() as db:
            changed = await db.scalar(
                update(SourceConflictAutoControl)
                .where(
                    SourceConflictAutoControl.id == CONTROL_ID,
                    owned(owner),
                )
                .values(lease_until=utcnow() + lease_duration(lease_seconds), updated_at=utcnow())
                .returning(SourceConflictAutoControl.id)
            )
            await db.commit()
            return changed is not None

    async def release(self, owner: str) -> bool:
        async with self.session_factory() as db:
            changed = await db.scalar(
                update(SourceConflictAutoControl)
                .where(
                    SourceConflictAutoControl.id == CONTROL_ID,
                    SourceConflictAutoControl.owner == owner,
                )
                .values(owner=None, lease_until=None, updated_at=utcnow())
                .returning(SourceConflictAutoControl.id)
            )
            await db.commit()
            return changed is not None

    async def reserve_call(self, owner: str, daily_limit: int) -> bool:
        limit = bounded_limit(daily_limit)
        if not limit:
            return False
        now = utcnow()
        today = now.date().isoformat()
        control = SourceConflictAutoControl
        statement = (
            update(control)
            .where(
                control.id == CONTROL_ID,
                owned(owner),
                provider_ready(),
                or_(control.budget_day != today, control.calls < limit),
            )
            .values(
                budget_day=today,
                calls=case((control.budget_day != today, 1), else_=control.calls + 1),
                updated_at=now,
            )
            .returning(control.id)
        )
        async with self.session_factory() as db:
            changed = await db.scalar(statement)
            await db.commit()
            return changed is not None

    async def pause_provider(self, owner: str, seconds: float) -> bool:
        if not 0 < seconds <= MAX_PROVIDER_PAUSE_SECONDS:
            raise ValueError("Invalid provider cooldown")
        control = SourceConflictAutoControl
        until = utcnow() + timedelta(seconds=seconds)
        async with self.session_factory() as db:
            changed = await db.scalar(
                update(control)
                .where(control.id == CONTROL_ID, owned(owner))
                .values(
                    paused_until=case(
                        (or_(control.paused_until.is_(None), control.paused_until < until), until),
                        else_=control.paused_until,
                    ),
                    updated_at=utcnow(),
                )
                .returning(control.id)
            )
            await db.commit()
            return changed is not None


def provider_ready() -> ColumnElement[bool]:
    return or_(
        SourceConflictAutoControl.paused_until.is_(None),
        SourceConflictAutoControl.paused_until <= utcnow(),
    )
