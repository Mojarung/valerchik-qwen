"""Задачи импорта документов в базу знаний: строки очереди вне HTTP-слоя."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditLog, IngestJob
from app.schemas.kb import IngestJobOut, JobStatus

SessionFactory = async_sessionmaker[AsyncSession]


def _iso(moment: datetime | None) -> str | None:
    """ISO-время или None. Смещение гарантирует тип столбца (app.db.UTCDateTime)."""
    return moment.isoformat() if moment else None


def _out(job: IngestJob) -> IngestJobOut:
    return IngestJobOut(
        id=job.id,
        title=job.title,
        filename=job.filename,
        status=cast(JobStatus, job.status),
        total=job.total,
        processed=job.processed,
        created_articles=job.created_articles,
        error=job.error,
        actor=job.actor,
        created_at=job.created_at.isoformat(),
        finished_at=_iso(job.finished_at),
    )


@dataclass(frozen=True)
class NewIngestJob:
    """Что ставим в очередь: откуда документ, кто загрузил и как его разбирать."""

    actor: str
    title: str
    filename: str
    params: dict[str, Any]
    text: str = ""
    source_key: str = ""
    total: int = 0
    content_sha256: str = ""


async def create(session_factory: SessionFactory, new: NewIngestJob) -> str:
    """Записать задачу импорта (и строку аудита); возвращает её id."""
    async with session_factory() as db:
        job = IngestJob(
            title=new.title,
            filename=new.filename,
            text=new.text,
            source_key=new.source_key,
            params=new.params,
            actor=new.actor,
            total=new.total,
            content_sha256=new.content_sha256,
        )
        db.add(job)
        db.add(
            AuditLog(
                actor=new.actor,
                action="kb.ingest",
                entity=new.title,
                payload={"file": new.filename, "bytes": len(new.text) or None},
            )
        )
        await db.commit()
        return job.id


async def mark_unqueued(session_factory: SessionFactory, job_id: str, reason: str) -> None:
    """Задача записана, но брокер её не принял: помечаем ошибкой. Иначе она навсегда
    осталась бы «в очереди», а админка опрашивала бы её вечно."""
    async with session_factory() as db:
        failed = await db.get(IngestJob, job_id)
        if failed is not None:
            failed.status = "error"
            failed.error = f"Не удалось поставить задачу в очередь: {reason}"
            failed.finished_at = datetime.now(UTC)
            await db.commit()


async def recent(session_factory: SessionFactory, limit: int) -> list[IngestJobOut]:
    """Последние задачи импорта, свежие сверху."""
    async with session_factory() as db:
        jobs = (
            (await db.execute(select(IngestJob).order_by(desc(IngestJob.created_at)).limit(limit)))
            .scalars()
            .all()
        )
    return [_out(job) for job in jobs]


async def get(session_factory: SessionFactory, job_id: str) -> IngestJobOut | None:
    async with session_factory() as db:
        job = await db.get(IngestJob, job_id)
    return _out(job) if job is not None else None
