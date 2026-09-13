"""Durable automatic source audits, sharing a global lease and daily model-call budget."""

import asyncio
import contextlib
import logging
import uuid

from openai import APIStatusError
from pydantic_ai.exceptions import ModelHTTPError
from sqlalchemy import or_, select, update

from app.adapters.embeddings import Embedder
from app.adapters.vectorstore import VectorStore
from app.db import SessionFactory
from app.models import KBArticle, utcnow
from app.models_conflicts import SourceConflictAuditJob
from app.schemas.source_conflicts import (
    AuditJobOut,
    AuditLimits,
    AutoAuditBackfillResponse,
    AutoAuditEntry,
    AutoAuditStatus,
    PairAssessment,
    SourceSnapshot,
)
from app.services.source_conflict_audit import SourceConflictAuditor
from app.services.source_conflict_auto_queue import AutoQueueItem, SourceConflictAutoQueue
from app.services.source_conflict_detector import (
    MAX_METADATA_CHARS,
    BatchAssessmentResult,
    SettingsReader,
    SourceConflictDetector,
    create_conflict_detector,
    metadata_chars,
)
from app.services.source_conflict_discovery import AnchoredCandidateFinder, ConflictNeighbourDiscovery
from app.services.source_conflict_lifecycle import auditable_source
from app.services.source_conflicts import SourceConflictRegistry

log = logging.getLogger(__name__)
_POLL_SECONDS = 2
_LEASE_SECONDS = 600
_DISCOVERY_TIMEOUT_SECONDS = 60
_JOB_TIMEOUT_SECONDS = 300
_MAX_NEIGHBOURS = 20
_MAX_DAILY_CALLS = 50000
_MAX_ATTEMPTS = 1
_MAX_ARTICLE_CHARS = 8000
_AUTH_FAILURES = frozenset({401, 403})
_PROVIDER_FAILURES = frozenset({429, 502, 503, 504})


def _setting(dyn: SettingsReader, name: str, default: int, ceiling: int) -> int:
    value = dyn.get(f"source_conflicts.{name}")
    return max(1, min(ceiling, int(default if value is None else value)))


class _DeferredCall(ValueError):
    """No model request was sent because an execution prerequisite changed."""


class _ReservedDetector(SourceConflictDetector):
    def __init__(
        self, inner: SourceConflictDetector, worker: SourceConflictAutoWorker, item: AutoQueueItem
    ) -> None:
        self.inner, self.worker, self.item = inner, worker, item
        self.version = inner.version
        self.deferred_reason = ""
        self.request_started = False

    async def _reserve(self, pairs: list[tuple[SourceSnapshot, SourceSnapshot]]) -> None:
        reason = self.worker.configuration_block()
        if not await self.worker.current(self.item):
            reason = "source_changed"
        sources = {source.id: source for pair in pairs for source in pair}
        if not reason and not await self.worker.sources_current(*sources.values()):
            reason = "neighbour_changed"
        if not reason and not await self.worker.queue.reserve_call(
            self.worker.owner, self.worker.daily_limit
        ):
            reason = "daily_call_limit"
        if reason:
            self.deferred_reason = reason
            raise _DeferredCall(reason)

    async def assess(
        self, left: SourceSnapshot, right: SourceSnapshot, *, timeout_s: float, max_output_tokens: int
    ) -> PairAssessment:
        await self._reserve([(left, right)])
        self.request_started = True
        try:
            result = await self.inner.assess(
                left, right, timeout_s=timeout_s, max_output_tokens=max_output_tokens
            )
            if not await self.worker.sources_current(left, right):
                self.deferred_reason = "neighbour_changed"
                raise _DeferredCall(self.deferred_reason)
            return result
        except (APIStatusError, ModelHTTPError) as exc:
            if exc.status_code in _AUTH_FAILURES | _PROVIDER_FAILURES:
                await self.worker.queue.pause_provider(
                    self.worker.owner, 300 if exc.status_code in _AUTH_FAILURES else 60
                )
            raise

    async def assess_many(
        self,
        pairs: list[tuple[SourceSnapshot, SourceSnapshot]],
        *,
        timeout_s: float,
        max_output_tokens: int,
    ) -> BatchAssessmentResult:
        await self._reserve(pairs)
        self.request_started = True
        try:
            result = await self.inner.assess_many(
                pairs, timeout_s=timeout_s, max_output_tokens=max_output_tokens
            )
            sources = {source.id: source for pair in pairs for source in pair}
            if not await self.worker.sources_current(*sources.values()):
                self.deferred_reason = "neighbour_changed"
                raise _DeferredCall(self.deferred_reason)
            return result
        except (APIStatusError, ModelHTTPError) as exc:
            if exc.status_code in _AUTH_FAILURES | _PROVIDER_FAILURES:
                await self.worker.queue.pause_provider(
                    self.worker.owner, 300 if exc.status_code in _AUTH_FAILURES else 60
                )
            raise


class SourceConflictAutoWorker:
    def __init__(
        self, factory: SessionFactory, dyn: SettingsReader, embedder: Embedder, store: VectorStore
    ) -> None:
        self.factory, self.dyn, self.embedder = factory, dyn, embedder
        self.registry = SourceConflictRegistry(factory)
        self.queue = SourceConflictAutoQueue(factory)
        self.discovery = ConflictNeighbourDiscovery(factory, embedder, store)
        self.owner = uuid.uuid4().hex
        self._task: asyncio.Task[None] | None = None
        self._tick_lock = asyncio.Lock()

    @property
    def neighbours(self) -> int:
        return _setting(self.dyn, "auto_max_neighbours", 5, _MAX_NEIGHBOURS)

    @property
    def attempts_limit(self) -> int:
        return _setting(self.dyn, "auto_max_attempts", 1, _MAX_ATTEMPTS)

    @property
    def daily_limit(self) -> int:
        value = self.dyn.get("source_conflicts.auto_daily_call_limit")
        return max(0, min(_MAX_DAILY_CALLS, int(100 if value is None else value)))

    def configuration_block(self) -> str:
        if not self.dyn.get("source_conflicts.auto_enabled"):
            return "disabled"
        if not self.dyn.get("llm.base_url") or not self.dyn.get("llm.model"):
            return "model_not_configured"
        return ""

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.queue.release(self.owner)

    async def _loop(self) -> None:
        while True:
            try:
                if await self.tick():
                    continue
            except Exception as exc:  # noqa: BLE001 — isolate provider errors without logging credentials
                log.error("Automatic source audit paused: %s", type(exc).__name__)
            await asyncio.sleep(_POLL_SECONDS)

    async def tick(self) -> bool:
        async with self._tick_lock:
            if self.configuration_block():
                return False
            summary = await self.queue.summary(self.daily_limit)
            if summary.get("provider_paused") or not summary["remaining_calls"]:
                return False
            if not await self.queue.acquire(self.owner, _LEASE_SECONDS):
                return False
            try:
                debounce = max(0, min(300, int(self.dyn.get("source_conflicts.auto_debounce_seconds") or 0)))
                item = await self.queue.claim_next(self.owner, debounce_seconds=debounce)
                if item is None:
                    return False
                await self._process(item)
                return True
            finally:
                await self.queue.release(self.owner)

    async def current(self, item: AutoQueueItem) -> bool:
        async with self.factory() as db:
            article = await db.get(KBArticle, item.article_id)
            return bool(
                article
                and article.source_version == item.source_version
                and article.status == "active"
                and auditable_source(article.origin)
            )

    async def sources_current(self, *sources: SourceSnapshot) -> bool:
        async with self.factory() as db:
            articles = (
                await db.scalars(
                    select(KBArticle).where(KBArticle.id.in_([source.article_id for source in sources]))
                )
            ).all()
        current = {article.id: article for article in articles}
        return all(
            source.article_id in current
            and current[source.article_id].source_version == source.source_version
            and current[source.article_id].status == "active"
            and auditable_source(current[source.article_id].origin)
            for source in sources
        )

    async def _process(self, item: AutoQueueItem) -> None:
        if not await self.current(item):
            await self.queue.finish(item.id, self.owner, "superseded", error="source_changed")
            return
        attempts = self.attempts_limit
        if item.attempts > attempts:
            await self.queue.finish(item.id, self.owner, "blocked", error="attempts_exhausted")
            return
        detector: SourceConflictDetector | None = None
        try:
            if await self._skip_large(item):
                return
            detector = create_conflict_detector(self.dyn)
            reserved = _ReservedDetector(detector, self, item)
            job = await self._audit(item, reserved)
            if job is not None:
                await self._complete(item, job, reserved.deferred_reason, reserved.request_started)
        except asyncio.CancelledError:
            await self.queue.defer(item.id, self.owner, "worker_stopped", retry_after_seconds=5)
            raise
        except _DeferredCall as exc:
            await self._failed(item, str(exc))
        except Exception as exc:  # noqa: BLE001 — persist bounded safe error class, never provider payload
            await self._failed(item, type(exc).__name__)
        finally:
            if detector is not None:
                await detector.aclose()

    async def _skip_large(self, item: AutoQueueItem) -> bool:
        snapshots = await self.registry.snapshot_articles([item.article_id])
        if not snapshots:
            await self.queue.finish(item.id, self.owner, "superseded", error="source_changed")
            return True
        source = snapshots[0]
        if (
            len(source.question) + len(source.answer) + 1 > _MAX_ARTICLE_CHARS
            or metadata_chars(source) > MAX_METADATA_CHARS
        ):
            await self.queue.finish(item.id, self.owner, "skipped", error="source_too_long")
            return True
        return False

    async def _audit(self, item: AutoQueueItem, detector: SourceConflictDetector) -> AuditJobOut | None:
        old = await self.registry.get_job(item.job_id) if item.job_id else None
        matching_anchor = old is not None and old.scope.get("anchor_id") == item.article_id
        if old is not None and matching_anchor and old.status == "done":
            return old
        if old is not None and old.status == "running" and old.lease_until and old.lease_until > utcnow():
            delay = max(1, int((old.lease_until - utcnow()).total_seconds()) + 1)
            await self.queue.defer(item.id, self.owner, "audit_lease_active", retry_after_seconds=delay)
            return None
        limits = AuditLimits(
            max_articles=self.neighbours + 1,
            max_pairs=self.neighbours,
            max_calls=self.attempts_limit,
            top_k=self.neighbours,
            timeout_seconds=_JOB_TIMEOUT_SECONDS,
            max_output_tokens=4000,
        )
        auditor = SourceConflictAuditor(
            self.registry,
            AnchoredCandidateFinder(self.embedder, item.article_id),
            detector,
            continue_invalid_evidence=True,
        )
        if (
            old is not None
            and matching_anchor
            and item.last_error != "neighbour_changed"
            and old.processing_version == auditor.processing_version(limits)
        ):
            job = old
        else:
            if old is not None:
                remaining = min(limits.max_calls, old.limits.max_calls - old.report.attempted_calls)
                if remaining <= 0:
                    await self.queue.finish(item.id, self.owner, "blocked", error="pair_budget_exhausted")
                    return None
                limits = limits.model_copy(update={"max_calls": remaining})
                await self._retire_job(old.id)
            async with asyncio.timeout(_DISCOVERY_TIMEOUT_SECONDS):
                neighbours = await self.discovery.find(item.article_id, limit=self.neighbours)
            if not neighbours:
                await self.queue.finish(item.id, self.owner, "skipped", error="no_candidates")
                return None
            job = await self.registry.create_job(
                {"article_ids": sorted({item.article_id, *neighbours}), "anchor_id": item.article_id},
                limits,
                auditor.processing_version(limits),
                actor="automatic",
            )
        if not await self.queue.attach_job(item.id, self.owner, job.id):
            raise _DeferredCall("queue_lease_lost")
        return await auditor.run(
            job.scope["article_ids"], job.limits, resume_job_id=job.id, actor="automatic"
        )

    async def _retire_job(self, job_id: str) -> None:
        async with self.factory() as db:
            await db.execute(
                update(SourceConflictAuditJob)
                .where(
                    SourceConflictAuditJob.id == job_id,
                    or_(
                        SourceConflictAuditJob.lease_until.is_(None),
                        SourceConflictAuditJob.lease_until <= utcnow(),
                    ),
                )
                .values(status="stopped", active_key=None, owner=None, lease_until=None, finished_at=utcnow())
            )
            await db.commit()

    async def _complete(
        self, item: AutoQueueItem, job: AuditJobOut, deferred: str, request_started: bool
    ) -> None:
        if not await self.current(item):
            deferred = "source_changed"
        if deferred == "source_changed":
            await self.queue.finish(item.id, self.owner, "superseded", error=deferred)
        elif deferred:
            if request_started:
                await self.queue.finish(item.id, self.owner, "blocked", error=deferred)
            else:
                await self.queue.defer(item.id, self.owner, deferred, retry_after_seconds=60)
        elif job.status == "done":
            state = "done" if job.report.evaluated_pairs or job.report.cached_pairs else "skipped"
            await self.queue.finish(
                item.id, self.owner, state, error="" if state == "done" else "no_candidates"
            )
        else:
            await self._failed(item, job.report.errors[-1] if job.report.errors else job.status)

    async def _failed(self, item: AutoQueueItem, error: str) -> None:
        delay = max(0, min(3600, int(self.dyn.get("source_conflicts.auto_retry_seconds") or 0)))
        await self.queue.finish(
            item.id,
            self.owner,
            "blocked",
            error=error,
            retry_after_seconds=delay,
        )

    async def status(self, limit: int = 50, offset: int = 0) -> AutoAuditStatus:
        summary = await self.queue.summary(self.daily_limit)
        estimate = await self.queue.estimate(self.neighbours)
        items = await self.queue.list_items(limit=limit, offset=offset)
        entries = await self._entries(items)
        blocked = self.configuration_block() or (
            "provider_unavailable"
            if summary.get("provider_paused")
            else "daily_call_limit"
            if not summary["remaining_calls"]
            else ""
        )
        pending = sum(summary["counts"].get(state, 0) for state in ("pending", "running", "retryable"))
        return AutoAuditStatus(
            enabled=bool(self.dyn.get("source_conflicts.auto_enabled")),
            configured=bool(self.dyn.get("llm.base_url") and self.dyn.get("llm.model")),
            blocked_reason=blocked,
            daily_call_limit=self.daily_limit,
            calls_used_today=summary["calls_today"],
            remaining_calls_today=summary["remaining_calls"],
            eligible_articles=estimate["eligible_articles"],
            unchecked_articles=estimate["unchecked_articles"],
            estimated_backfill_calls=estimate["estimated_max_calls"] * self.attempts_limit,
            estimated_pending_calls=pending * self.attempts_limit,
            counts=summary["counts"],
            entries=entries,
        )

    async def _entries(self, items: list[AutoQueueItem]) -> list[AutoAuditEntry]:
        async with self.factory() as db:
            rows = (
                await db.execute(
                    select(KBArticle.id, KBArticle.question).where(
                        KBArticle.id.in_([item.article_id for item in items])
                    )
                )
            ).all()
            titles = {article_id: question for article_id, question in rows}
        result = []
        for item in items:
            job = await self.registry.get_job(item.job_id) if item.job_id else None
            result.append(
                AutoAuditEntry.model_validate(
                    {
                        **item.model_dump(),
                        "question": titles.get(item.article_id, "Удалённая статья"),
                        "report": job.report if job else None,
                    }
                )
            )
        return result

    async def backfill(self) -> AutoAuditBackfillResponse:
        result = await self.queue.enqueue_unchecked()
        return AutoAuditBackfillResponse(
            scanned=result["scanned"],
            eligible=result["eligible"],
            enqueued=result["queued"],
            estimated_calls=result["queued"] * self.attempts_limit,
        )

    async def retry(self, item_id: str) -> AutoAuditEntry:
        item = await self.queue.retry(item_id)
        return (await self._entries([item]))[0]
