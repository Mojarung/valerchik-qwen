"""Atomic, durable job leases; expired workers cannot checkpoint or finish."""

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app.db import SessionFactory
from app.models import utcnow
from app.models_conflicts import SourceConflictAuditJob
from app.schemas.source_conflicts import AuditJobOut, AuditLimits, AuditReport

RESUMABLE = ("pending", "running", "retryable", "error")
TERMINAL = ("done", "retryable", "error", "budget_exhausted", "cancelled", "stopped")
MAX_PAGE_SIZE = 200
MAX_LEASE_SECONDS = 7200
VERSION_MAX_LENGTH = 128
OWNER_MAX_LENGTH = 64


def job_out(row: SourceConflictAuditJob) -> AuditJobOut:
    return AuditJobOut(
        id=row.id,
        scope=row.scope,
        limits=AuditLimits.model_validate(row.limits),
        processing_version=row.processing_version,
        actor=row.actor,
        status=row.status,
        report=AuditReport.model_validate(row.report),
        owner=row.owner,
        lease_until=row.lease_until,
        created_at=row.created_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
    )


def _lease(seconds: float) -> timedelta:
    if not 0 < seconds <= MAX_LEASE_SECONDS:
        raise ValueError("lease_seconds must be between 0 and 7200")
    return timedelta(seconds=seconds)


class SourceConflictJobs:
    def __init__(self, session_factory: SessionFactory) -> None:
        self.session_factory = session_factory

    async def create_job(
        self, scope: dict[str, Any], limits: AuditLimits, processing_version: str, actor: str = ""
    ) -> AuditJobOut:
        if not processing_version or len(processing_version) > VERSION_MAX_LENGTH:
            raise ValueError("processing_version must contain 1..128 characters")
        key_data = {"scope": scope, "limits": limits.model_dump(), "version": processing_version}
        active_key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
        async with self.session_factory() as db:
            row = SourceConflictAuditJob(
                scope=scope,
                limits=limits.model_dump(),
                active_key=active_key,
                processing_version=processing_version,
                actor=actor,
            )
            db.add(row)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                row = (
                    await db.scalars(
                        select(SourceConflictAuditJob).where(SourceConflictAuditJob.active_key == active_key)
                    )
                ).one()
            return job_out(row)

    async def get_job(self, job_id: str) -> AuditJobOut | None:
        async with self.session_factory() as db:
            row = await db.get(SourceConflictAuditJob, job_id)
            return job_out(row) if row else None

    async def list_jobs(self, limit: int = 50, offset: int = 0) -> list[AuditJobOut]:
        if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
            raise ValueError("invalid pagination")
        async with self.session_factory() as db:
            rows = (
                await db.scalars(
                    select(SourceConflictAuditJob)
                    .order_by(SourceConflictAuditJob.created_at.desc(), SourceConflictAuditJob.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return [job_out(row) for row in rows]

    async def claim_job(self, job_id: str, owner: str, lease_seconds: float = 120) -> bool:
        if not owner or len(owner) > OWNER_MAX_LENGTH:
            raise ValueError("owner must contain 1..64 characters")
        now = utcnow()
        stmt = (
            update(SourceConflictAuditJob)
            .where(
                SourceConflictAuditJob.id == job_id,
                SourceConflictAuditJob.status.in_(RESUMABLE),
                or_(SourceConflictAuditJob.lease_until.is_(None), SourceConflictAuditJob.lease_until <= now),
            )
            .values(
                status="running",
                owner=owner,
                lease_until=now + _lease(lease_seconds),
                updated_at=now,
                finished_at=None,
            )
            .returning(SourceConflictAuditJob.id)
        )
        async with self.session_factory() as db:
            claimed = (await db.execute(stmt)).scalar_one_or_none() is not None
            await db.commit()
            return claimed

    async def checkpoint_job(
        self, job_id: str, owner: str, report: AuditReport, lease_seconds: float = 120
    ) -> bool:
        return await self._owned_update(
            job_id,
            owner,
            {
                "report": report.model_dump(),
                "lease_until": utcnow() + _lease(lease_seconds),
            },
        )

    async def finish_job(self, job_id: str, owner: str, status: str, report: AuditReport) -> bool:
        if status not in TERMINAL:
            raise ValueError("invalid terminal job status")
        values: dict[str, Any] = {
            "status": status,
            "report": report.model_dump(),
            "owner": None,
            "lease_until": None,
            "finished_at": utcnow(),
        }
        if status not in RESUMABLE:
            values["active_key"] = None
        return await self._owned_update(job_id, owner, values)

    async def _owned_update(self, job_id: str, owner: str, values: dict[str, Any]) -> bool:
        now = utcnow()
        stmt = (
            update(SourceConflictAuditJob)
            .where(
                SourceConflictAuditJob.id == job_id,
                SourceConflictAuditJob.status == "running",
                SourceConflictAuditJob.owner == owner,
                SourceConflictAuditJob.lease_until > now,
            )
            .values(**values, updated_at=now)
            .returning(SourceConflictAuditJob.id)
        )
        async with self.session_factory() as db:
            changed = (await db.execute(stmt)).scalar_one_or_none() is not None
            await db.commit()
            return changed
