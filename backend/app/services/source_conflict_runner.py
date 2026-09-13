"""One explicitly requested audit task per API process, with durable job leases.

No broker await_inplace calls: an HTTP request only saves and schedules work.
On shutdown the task is cancelled; a saved running job can be explicitly resumed
once its lease expires. There is no automatic paid retry or startup scheduler.
"""

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from app.adapters.embeddings import Embedder
from app.db import SessionFactory
from app.models import utcnow
from app.schemas.source_conflicts import AuditJobOut, ConflictAuditRequest
from app.services.source_conflict_audit import AuditAlreadyRunning, SourceConflictAuditor
from app.services.source_conflict_candidates import SourceConflictCandidateFinder
from app.services.source_conflict_detector import SourceConflictDetector, create_conflict_detector
from app.services.source_conflicts import SourceConflictRegistry

if TYPE_CHECKING:
    from app.dynamic_settings import DynamicSettings

log = logging.getLogger(__name__)


class AuditQueueBusy(ValueError):
    """An audit is already active, or the requested saved job cannot resume."""


class AuditConfigurationError(ValueError):
    """No usable model configuration; do not create a queued job."""


class SourceConflictRunner:
    def __init__(self, session_factory: SessionFactory, dyn: DynamicSettings, embedder: Embedder) -> None:
        self.registry = SourceConflictRegistry(session_factory)
        self.dyn = dyn
        self.embedder = embedder
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    async def start(self, body: ConflictAuditRequest, actor: str) -> AuditJobOut:
        return await self._queue(body, actor)

    async def resume(self, job_id: str, actor: str) -> AuditJobOut:
        job = await self.registry.get_job(job_id)
        if job is None:
            raise LookupError("Audit job not found")
        if job.status not in ("pending", "running", "retryable", "error"):
            raise AuditQueueBusy("Для этого задания нужен новый аудит с новым бюджетом")
        body = ConflictAuditRequest(article_ids=job.scope["article_ids"], limits=job.limits)
        return await self._queue(body, actor, resume_id=job_id)

    async def _queue(
        self, body: ConflictAuditRequest, actor: str, resume_id: str | None = None
    ) -> AuditJobOut:
        async with self._lock:
            if self._task is not None and not self._task.done():
                raise AuditQueueBusy("Аудит уже выполняется. Дождитесь завершения текущего задания")
            try:
                detector = create_conflict_detector(self.dyn)
            except ValueError as exc:
                raise AuditConfigurationError("Для аудита настройте адрес и модель LLM") from exc
            try:
                auditor = SourceConflictAuditor(
                    self.registry, SourceConflictCandidateFinder(self.embedder), detector
                )
                job = await self._prepare(auditor, body, actor, resume_id)
                self._task = asyncio.create_task(self._execute(auditor, detector, job))
                return job
            except BaseException:
                await detector.aclose()
                raise

    async def _prepare(
        self, auditor: SourceConflictAuditor, body: ConflictAuditRequest, actor: str, resume_id: str | None
    ) -> AuditJobOut:
        version = auditor.processing_version(body.limits)
        if resume_id is not None:
            job = await self.registry.get_job(resume_id)
            if job is None:
                raise LookupError("Audit job not found")
            if job.processing_version != version:
                raise AuditQueueBusy("Модель или параметры обработки изменились. Запустите новый аудит")
            if job.lease_until is not None and job.lease_until > utcnow():
                raise AuditQueueBusy("Аудит выполняется в другом процессе. Дождитесь освобождения задания")
            return job
        return await self.registry.create_job(
            {"article_ids": sorted(body.article_ids)}, body.limits, version, actor=actor
        )

    async def _execute(
        self, auditor: SourceConflictAuditor, detector: SourceConflictDetector, job: AuditJobOut
    ) -> None:
        try:
            await auditor.run(job.scope["article_ids"], job.limits, resume_job_id=job.id, actor=job.actor)
        except AuditAlreadyRunning:
            # Another process won the durable lease for the same saved request.
            pass
        except Exception as exc:  # noqa: BLE001 — keep provider details out of logs
            log.error("Source audit %s stopped: %s", job.id, type(exc).__name__)
        finally:
            await detector.aclose()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
