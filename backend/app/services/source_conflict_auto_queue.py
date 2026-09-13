"""Coalescing transactional outbox for changed sources; never submits paid work itself."""

from datetime import datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import Table, and_, case, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import KBArticle, new_id, utcnow
from app.models_conflicts import (
    SourceConflictAuditJob,
    SourceConflictAutoControl,
    SourceConflictAutoItem,
)
from app.services.source_conflict_auto_control import (
    CONTROL_ID,
    SourceConflictAutoControlStore,
    bounded_limit,
    insert_ignoring,
    owned,
)
from app.services.source_conflict_lifecycle import auditable_source

PAGE_SIZE = 500
MAX_LIST_LIMIT = 200
MAX_DELAY_SECONDS = 86400
ACTIVE_STATES = ("pending", "running", "retryable", "blocked")
FINAL_STATES = ("done", "retryable", "blocked", "superseded", "skipped")
ALL_STATES = (*ACTIVE_STATES, "done", "superseded", "skipped")


class AutoQueueItem(BaseModel):
    id: str
    article_id: str
    source_version: str
    status: str
    job_id: str | None
    owner: str | None
    priority: int = 0
    attempts: int
    available_at: datetime
    created_at: datetime
    updated_at: datetime
    last_error: str


def item_out(row: SourceConflictAutoItem) -> AutoQueueItem:
    values = {field: getattr(row, field) for field in AutoQueueItem.model_fields}
    values["priority"] = row.priority or 0
    return AutoQueueItem.model_validate(values)


def eligible(article: KBArticle) -> bool:
    return (article.status or "active") == "active" and auditable_source(article.origin)


def enqueue_article(
    session: Session, article: KBArticle, *, deleted: bool = False, priority: int = 0
) -> bool:
    """Called in before_flush: commit/rollback covers the article and outbox together."""
    item = SourceConflictAutoItem
    version = article.source_version
    statement = update(item).where(item.article_id == article.id, item.status.in_(ACTIVE_STATES))
    if not deleted and eligible(article):
        statement = statement.where(item.source_version != version)
    session.execute(statement.values(status="superseded", owner=None, updated_at=utcnow()))
    if deleted or not eligible(article) or not version:
        return False
    return _insert_item(session, article.id, version, priority)


def _insert_item(session: Session, article_id: str, version: str, priority: int) -> bool:
    now = utcnow()
    return insert_ignoring(
        session,
        cast(Table, SourceConflictAutoItem.__table__),
        {
            "id": new_id(),
            "article_id": article_id,
            "source_version": version,
            "status": "pending",
            "priority": priority,
            "created_at": now,
            "updated_at": now,
            "available_at": now,
        },
    )


def current_item() -> ColumnElement[bool]:
    return exists(
        select(KBArticle.id).where(
            KBArticle.id == SourceConflictAutoItem.article_id,
            KBArticle.source_version == SourceConflictAutoItem.source_version,
            KBArticle.status == "active",
        )
    )


def delay(seconds: float) -> timedelta:
    if not 0 <= seconds <= MAX_DELAY_SECONDS:
        raise ValueError("Invalid queue delay")
    return timedelta(seconds=seconds)


class SourceConflictAutoQueue(SourceConflictAutoControlStore):
    async def get_item(self, item_id: str) -> AutoQueueItem | None:
        async with self.session_factory() as db:
            row = await db.get(SourceConflictAutoItem, item_id)
            return item_out(row) if row else None

    async def list_items(self, limit: int = 50, offset: int = 0) -> list[AutoQueueItem]:
        if not 1 <= limit <= MAX_LIST_LIMIT or offset < 0:
            raise ValueError("Invalid queue pagination")
        async with self.session_factory() as db:
            rows = (
                await db.scalars(
                    select(SourceConflictAutoItem)
                    .order_by(
                        SourceConflictAutoItem.updated_at.desc(),
                        SourceConflictAutoItem.id,
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return [item_out(row) for row in rows]

    async def claim_next(self, owner: str, debounce_seconds: float = 5) -> AutoQueueItem | None:
        now = utcnow()
        quiet_before = now - delay(debounce_seconds)
        item = SourceConflictAutoItem
        due = or_(
            item.status == "running",
            and_(
                item.status.in_(("pending", "retryable")),
                item.available_at <= now,
                item.updated_at <= quiet_before,
            ),
        )
        async with self.session_factory() as db:
            # Lost or superseded work can never hold up the current revision.
            await db.execute(
                update(item)
                .where(owned(owner), item.status.in_(ACTIVE_STATES), ~current_item())
                .values(status="superseded", owner=None, updated_at=now)
            )
            row = await db.scalar(
                select(item)
                .where(owned(owner), current_item(), due)
                .order_by(
                    case((item.status == "running", 0), else_=1),
                    func.coalesce(item.priority, 0),
                    item.available_at,
                    item.id,
                )
                .limit(1)
            )
            if row is None:
                await db.commit()
                return None
            changed = await db.scalar(
                update(item)
                .where(item.id == row.id, owned(owner), current_item(), due)
                .values(status="running", owner=owner, attempts=item.attempts + 1, updated_at=now)
                .returning(item.id)
            )
            if changed is None:
                await db.rollback()
                return None
            await db.commit()
            await db.refresh(row)
            return item_out(row)

    async def attach_job(self, item_id: str, owner: str, job_id: str) -> bool:
        return await self._change(item_id, owner, {"job_id": job_id})

    async def finish(
        self,
        item_id: str,
        owner: str,
        status: str,
        error: str = "",
        retry_after_seconds: float = 0,
    ) -> bool:
        if status not in FINAL_STATES:
            raise ValueError("Invalid automatic work status")
        return await self._change(
            item_id,
            owner,
            {
                "status": status,
                "last_error": error[:500],
                "owner": None,
                "available_at": utcnow() + delay(retry_after_seconds),
            },
        )

    async def defer(self, item_id: str, owner: str, reason: str, retry_after_seconds: float) -> bool:
        return await self._change(
            item_id,
            owner,
            {
                "status": "retryable",
                "last_error": reason[:500],
                "owner": None,
                "available_at": utcnow() + delay(retry_after_seconds),
                "attempts": case(
                    (SourceConflictAutoItem.attempts > 0, SourceConflictAutoItem.attempts - 1), else_=0
                ),
            },
        )

    async def _change(self, item_id: str, owner: str, values: dict[str, Any]) -> bool:
        item = SourceConflictAutoItem
        async with self.session_factory() as db:
            changed = await db.scalar(
                update(item)
                .where(
                    item.id == item_id,
                    item.owner == owner,
                    item.status == "running",
                    owned(owner),
                    current_item(),
                )
                .values(**values, updated_at=utcnow())
                .returning(item.id)
            )
            await db.commit()
            return changed is not None

    async def retry(self, item_id: str) -> AutoQueueItem:
        item = SourceConflictAutoItem
        async with self.session_factory() as db:
            row = await db.get(item, item_id)
            if row is None:
                raise LookupError("Automatic work not found")
            previous_job_id = row.job_id
            changed = await db.scalar(
                update(item)
                .where(item.id == item_id, current_item(), item.status.in_(("retryable", "blocked")))
                .values(
                    status="pending",
                    attempts=0,
                    job_id=None,
                    owner=None,
                    available_at=utcnow(),
                    updated_at=utcnow(),
                    last_error="",
                )
                .returning(item.id)
            )
            if changed is None:
                raise ValueError("Only current blocked or retryable work can be retried")
            if previous_job_id:
                await _retire_job(db, previous_job_id)
            await db.commit()
            await db.refresh(row)
            return item_out(row)

    async def summary(self, daily_limit: int) -> dict[str, Any]:
        async with self.session_factory() as db:
            rows = (
                await db.execute(
                    select(SourceConflictAutoItem.status, func.count()).group_by(
                        SourceConflictAutoItem.status
                    )
                )
            ).all()
            control = await db.get(SourceConflictAutoControl, CONTROL_ID)
        counts = dict.fromkeys(ALL_STATES, 0)
        counts.update({status: count for status, count in rows})
        calls = control.calls if control and control.budget_day == utcnow().date().isoformat() else 0
        limit = bounded_limit(daily_limit)
        return {
            "counts": counts,
            "calls_today": calls,
            "daily_limit": limit,
            "remaining_calls": max(0, limit - calls),
            "lease_until": control.lease_until if control else None,
            "paused_until": control.paused_until if control else None,
            "provider_paused": bool(control and control.paused_until and control.paused_until > utcnow()),
        }

    async def estimate(self, max_neighbours: int = 5) -> dict[str, int]:
        # Neighbours are assessed together: at most one provider request per article.
        _ = max_neighbours
        counts = {
            "eligible_articles": 0,
            "unchecked_articles": 0,
            "completed_articles": 0,
            "queued_articles": 0,
        }
        cursor = ""
        while True:
            async with self.session_factory() as db:
                articles = await _page(db, cursor)
                if not articles:
                    break
                states = await _states(db, articles)
            for article in articles:
                if not eligible(article):
                    continue
                counts["eligible_articles"] += 1
                state = states.get((article.id, article.source_version or ""))
                if state is None:
                    counts["unchecked_articles"] += 1
                elif state == "done":
                    counts["completed_articles"] += 1
                elif state in ACTIVE_STATES:
                    counts["queued_articles"] += 1
            cursor = articles[-1].id
        counts["estimated_max_calls"] = counts["unchecked_articles"]
        return counts

    async def enqueue_unchecked(self) -> dict[str, int]:
        counts = {"scanned": 0, "eligible": 0, "queued": 0}
        cursor = ""
        while True:
            async with self.session_factory() as db:
                articles = await _page(db, cursor)
                if not articles:
                    break
                for article in articles:
                    counts["scanned"] += 1
                    if not eligible(article):
                        continue
                    counts["eligible"] += 1
                    counts["queued"] += int(await _enqueue_backfill(db, article))
                await db.commit()
            cursor = articles[-1].id
        return counts


async def _page(db: AsyncSession, cursor: str) -> list[KBArticle]:
    return list(
        (
            await db.scalars(
                select(KBArticle)
                .where(KBArticle.id > cursor, KBArticle.status == "active")
                .order_by(KBArticle.id)
                .limit(PAGE_SIZE)
            )
        ).all()
    )


async def _states(db: AsyncSession, articles: list[KBArticle]) -> dict[tuple[str, str], str]:
    rows = (
        await db.execute(
            select(
                SourceConflictAutoItem.article_id,
                SourceConflictAutoItem.source_version,
                SourceConflictAutoItem.status,
            ).where(SourceConflictAutoItem.article_id.in_([article.id for article in articles]))
        )
    ).all()
    return {(article_id, version): status for article_id, version, status in rows}


async def _enqueue_backfill(db: AsyncSession, article: KBArticle) -> bool:
    # A page may predate a concurrent edit: historical enqueue must never coalesce
    # newer lifecycle work or overwrite the editor's source revision.
    version = article.source_version
    if not version:
        version = await db.scalar(
            update(KBArticle)
            .where(
                KBArticle.id == article.id, KBArticle.source_version.is_(None), KBArticle.status == "active"
            )
            .values(source_version=new_id())
            .returning(KBArticle.source_version)
            .execution_options(synchronize_session=False)
        )
    if not version:
        return False
    return await db.run_sync(_insert_item, article.id, version, 1)


async def _retire_job(db: AsyncSession, job_id: str) -> None:
    # Retiring the old deduplication key and resetting the queue are one
    # transaction. A live auditor retains its lease and cannot be displaced.
    job = SourceConflictAuditJob
    now = utcnow()
    retired = await db.scalar(
        update(job)
        .where(
            job.id == job_id,
            job.status.in_(("pending", "running", "retryable", "error", "budget_exhausted")),
            or_(job.lease_until.is_(None), job.lease_until <= now),
        )
        .values(
            status="stopped", active_key=None, owner=None, lease_until=None, finished_at=now, updated_at=now
        )
        .returning(job.id)
    )
    if retired is None:
        current = await db.get(job, job_id)
        if current and current.lease_until and current.lease_until > now:
            raise ValueError("The associated audit is still running")
