"""Bounded, lease-protected audit jobs shared by manual and automatic runners."""

import asyncio
import hashlib
import json
import math
import uuid
from typing import TYPE_CHECKING, Any

import httpx
from openai import APIError
from pydantic_ai.exceptions import AgentRunError, UnexpectedModelBehavior
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.source_conflicts import (
    AuditJobOut,
    AuditLimits,
    AuditReport,
    ConflictResult,
    PairAssessment,
)
from app.services.source_conflict_candidates import CandidatePair, SourceConflictCandidateFinder, article_text
from app.services.source_conflict_detector import (
    MAX_METADATA_CHARS,
    EvidenceValidationError,
    SourceConflictDetector,
    metadata_chars,
)

if TYPE_CHECKING:
    from app.services.source_conflicts import SourceConflictRegistry

_MAX_POOL_IDS = 1000
_LEASE_MARGIN_SECONDS = 30


class AuditAlreadyRunning(RuntimeError):
    """Another owner holds this audit's lease."""


class AuditLeaseLost(RuntimeError):
    """Do not continue paid work after losing ownership."""


class SourceConflictAuditor:
    def __init__(
        self,
        registry: SourceConflictRegistry,
        finder: SourceConflictCandidateFinder,
        detector: SourceConflictDetector,
        *,
        continue_invalid_evidence: bool = False,
    ) -> None:
        self.registry = registry
        self.finder = finder
        self.detector = detector
        self.continue_invalid_evidence = continue_invalid_evidence

    def processing_version(self, limits: AuditLimits) -> str:
        # Changes affecting evidence selection invalidate cache; spending/time budgets do not.
        config = {
            "finder": self.finder.version,
            "detector": self.detector.version,
            "top_k": limits.top_k,
            "max_article_chars": limits.max_article_chars,
        }
        return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

    async def run(
        self,
        article_ids: list[str],
        limits: AuditLimits,
        *,
        resume_job_id: str | None = None,
        actor: str = "",
    ) -> AuditJobOut:
        ids = sorted(set(article_ids))
        if not ids or len(ids) > _MAX_POOL_IDS or any(not value.strip() for value in ids):
            raise ValueError(f"Provide between 1 and {_MAX_POOL_IDS} nonempty article IDs")
        scope: dict[str, Any] = {"article_ids": ids}
        anchor_id = getattr(self.finder, "anchor_id", None)
        if anchor_id is not None:
            scope["anchor_id"] = anchor_id
        version = self.processing_version(limits)
        job = await self._get_job(scope, limits, version, resume_job_id, actor)
        if job.status == "done":
            return job
        owner = uuid.uuid4().hex
        lease = math.ceil(limits.timeout_seconds) + _LEASE_MARGIN_SECONDS
        if not await self.registry.claim_job(job.id, owner, lease_seconds=lease):
            raise AuditAlreadyRunning("Audit has an active owner or cannot be resumed in its current state")
        # Another owner may have checkpointed after our initial read and before this claim.
        # Only the report loaded while holding the lease is a valid remaining-call budget.
        claimed_job = await self.registry.get_job(job.id)
        if claimed_job is None:
            raise AuditLeaseLost("Audit job disappeared after claiming its lease")
        job = claimed_job
        report = AuditReport(
            attempted_calls=job.report.attempted_calls,
            failures=job.report.failures,
            errors=list(job.report.errors),
            pool_limited=len(ids) > limits.max_articles,
        )
        status = await self._execute(job, owner, ids[: limits.max_articles], report)
        if not await self.registry.finish_job(job.id, owner, status, report):
            raise AuditLeaseLost("Audit lease expired before completion was saved")
        finished = await self.registry.get_job(job.id)
        if finished is None:
            raise RuntimeError("Audit job disappeared after completion")
        return finished

    async def _get_job(
        self,
        scope: dict[str, Any],
        limits: AuditLimits,
        version: str,
        resume_id: str | None,
        actor: str,
    ) -> AuditJobOut:
        if resume_id is None:
            return await self.registry.create_job(scope, limits, version, actor=actor)
        job = await self.registry.get_job(resume_id)
        if job is None or job.scope != scope or job.limits != limits or job.processing_version != version:
            raise ValueError(
                "Cannot resume audit with missing job or changed pool, limits, or processing version"
            )
        return job

    async def _execute(self, job: AuditJobOut, owner: str, ids: list[str], report: AuditReport) -> str:
        invalid_evidence = False
        try:
            async with asyncio.timeout(job.limits.timeout_seconds):
                pairs = await self._candidates(ids, job.limits, report)
                batches = (
                    [pairs]
                    if getattr(self.finder, "anchor_id", None) is not None
                    else [[pair] for pair in pairs]
                )
                for batch in batches:
                    try:
                        if not await self._evaluate_batch(job, owner, batch, report):
                            report.stop_reason = "max_calls"
                            return "budget_exhausted"
                    except (EvidenceValidationError, UnexpectedModelBehavior) as exc:
                        if not self.continue_invalid_evidence:
                            raise
                        invalid_evidence = True
                        report.failures += 1
                        report.errors.append(
                            str(exc) if isinstance(exc, EvidenceValidationError) else type(exc).__name__
                        )
                        report.stop_reason = "invalid_evidence"
                        lease = math.ceil(job.limits.timeout_seconds) + _LEASE_MARGIN_SECONDS
                        if not await self.registry.checkpoint_job(job.id, owner, report, lease_seconds=lease):
                            raise AuditLeaseLost("Audit lease was lost after invalid model evidence") from exc
            if invalid_evidence or report.stop_reason == "invalid_evidence":
                report.stop_reason = "invalid_evidence"
                return "retryable"
            report.stop_reason = "done"
            return "done"
        except TimeoutError:
            report.failures += 1
            report.stop_reason = "timeout"
            report.errors.append("A model, embedding, or database operation exceeded the audit time budget")
            return "retryable"
        except (httpx.HTTPError, APIError, AgentRunError, ValueError, SQLAlchemyError) as exc:
            # Persist only the class: transport exceptions can contain URLs, source text or API credentials.
            report.failures += 1
            report.stop_reason = "upstream_error"
            report.errors.append(type(exc).__name__)
            return "retryable"

    async def _candidates(
        self, ids: list[str], limits: AuditLimits, report: AuditReport
    ) -> list[CandidatePair]:
        sources = await self.registry.snapshot_articles(ids)
        report.eligible_articles = len(sources)
        report.skipped_ineligible_articles = len(ids) - len(sources)
        selected = [
            source
            for source in sources
            if len(article_text(source)) <= limits.max_article_chars
            and metadata_chars(source) <= MAX_METADATA_CHARS
        ]
        report.selected_articles = len(selected)
        report.skipped_long_articles = len(sources) - len(selected)
        pairs = await self.finder.find(selected, top_k=limits.top_k, max_pairs=limits.max_pairs + 1)
        report.candidate_pairs_truncated = len(pairs) > limits.max_pairs
        report.candidate_pairs = min(len(pairs), limits.max_pairs)
        return pairs[: limits.max_pairs]

    async def _evaluate_batch(
        self, job: AuditJobOut, owner: str, pairs: list[CandidatePair], report: AuditReport
    ) -> bool:
        pending: list[CandidatePair] = []
        for pair in pairs:
            cached = await self.registry.get_pair(pair.left.id, pair.right.id, job.processing_version)
            if cached is not None and not cached.stale:
                report.cached_pairs += 1
                _count_outcome(report, cached)
            else:
                pending.append(pair)
        if not pending:
            return True
        if report.attempted_calls >= job.limits.max_calls:
            return False
        report.attempted_calls += 1
        lease = math.ceil(job.limits.timeout_seconds) + _LEASE_MARGIN_SECONDS
        if not await self.registry.checkpoint_job(job.id, owner, report, lease_seconds=lease):
            raise AuditLeaseLost("Audit lease was lost before model request")
        assessments = await self._assess_pending(job, pending, report)
        for pair, assessment in zip(pending, assessments, strict=True):
            if assessment is None:
                continue
            result = await self.registry.record_pair(
                pair.left, pair.right, assessment, job.processing_version, job_id=job.id
            )
            report.evaluated_pairs += 1
            _count_outcome(report, result)
        return True

    async def _assess_pending(
        self, job: AuditJobOut, pending: list[CandidatePair], report: AuditReport
    ) -> list[PairAssessment | None]:
        if len(pending) == 1 and getattr(self.finder, "anchor_id", None) is None:
            return [
                await self.detector.assess(
                    pending[0].left,
                    pending[0].right,
                    timeout_s=job.limits.request_timeout_seconds,
                    max_output_tokens=job.limits.max_output_tokens,
                )
            ]
        batch = await self.detector.assess_many(
            [(pair.left, pair.right) for pair in pending],
            timeout_s=job.limits.request_timeout_seconds,
            max_output_tokens=job.limits.max_output_tokens,
        )
        if len(batch.assessments) != len(pending):
            raise EvidenceValidationError("Model returned an incomplete assessment batch")
        if not batch.errors:
            return batch.assessments
        if not self.continue_invalid_evidence:
            raise EvidenceValidationError(batch.errors[0])
        report.failures += len(batch.errors)
        report.errors.extend(batch.errors)
        report.stop_reason = "invalid_evidence"
        return batch.assessments


def _count_outcome(report: AuditReport, result: ConflictResult) -> None:
    if result.stale:
        report.stale_pairs += 1
    elif result.outcome == "conflict":
        report.conflicts += 1
    elif result.outcome == "compatible":
        report.compatible += 1
    else:
        report.uncertain += 1
