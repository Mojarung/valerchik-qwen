"""Audits are resumable bounded jobs and never report omitted articles as checked."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.adapters.embeddings import HashEmbedder
from app.schemas.source_conflicts import AuditJobOut, AuditLimits, AuditReport
from app.services.source_conflict_audit import AuditAlreadyRunning, SourceConflictAuditor
from app.services.source_conflict_candidates import SourceConflictCandidateFinder
from tests.test_source_conflict_detector import assessment, snapshot


class MemoryRegistry:
    def __init__(self, sources):
        self.sources = sources
        self.job = None
        self.pairs = {}
        self.claimable = True

    async def snapshot_articles(self, ids):
        return [s for s in self.sources if s.article_id in ids]

    async def create_job(self, scope, limits, processing_version, actor=""):
        if self.job is None:
            self.job = AuditJobOut(
                id="job",
                scope=scope,
                limits=limits,
                processing_version=processing_version,
                actor=actor,
                status="pending",
                report=AuditReport(),
                owner=None,
                lease_until=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                finished_at=None,
            )
        return self.job

    async def get_job(self, job_id):
        return self.job

    async def claim_job(self, *args, **kwargs):
        return self.claimable

    async def checkpoint_job(self, job_id, owner, report, **kwargs):
        self.job = self.job.model_copy(update={"report": report.model_copy(deep=True)})
        return True

    async def finish_job(self, job_id, owner, status, report):
        self.job = self.job.model_copy(update={"status": status, "report": report.model_copy(deep=True)})
        return True

    async def get_pair(self, left_id, right_id, processing_version):
        return self.pairs.get((left_id, right_id, processing_version))

    async def record_pair(self, left, right, result, processing_version, job_id=None):
        pair = SimpleNamespace(outcome=result.outcome, stale=False)
        self.pairs[(left.id, right.id, processing_version)] = pair
        return pair


def auditor(sources=None):
    registry = MemoryRegistry(sources or [snapshot(), snapshot("b", "До 20 МБ.")])
    detector = SimpleNamespace(version="fake-v1", assess=AsyncMock(return_value=assessment()))
    result = SourceConflictAuditor(registry, SourceConflictCandidateFinder(HashEmbedder()), detector)
    return result, registry, detector


async def test_completed_pairs_are_reused_without_model_calls():
    service, registry, detector = auditor()
    limits = AuditLimits()
    first = await service.run(["a", "b"], limits)
    assert first.report.conflicts == 1
    assert first.report.attempted_calls == 1
    assert first.status == "done"
    registry.job = None
    second = await service.run(["a", "b"], limits)
    assert second.report.cached_pairs == 1
    assert second.report.attempted_calls == 0
    assert detector.assess.await_count == 1


async def test_budget_is_persisted_before_call_and_resume_keeps_spent_calls():
    service, registry, detector = auditor(
        [snapshot(), snapshot("b", "До 20 МБ."), snapshot("c", "До 20 МБ.")]
    )
    detector.assess.side_effect = TimeoutError("slow model")
    limits = AuditLimits(max_calls=2)
    first = await service.run(["a", "b", "c"], limits)
    assert first.status == "retryable"
    assert registry.job.report.attempted_calls == 1
    detector.assess.side_effect = None
    second = await service.run(["a", "b", "c"], limits, resume_job_id="job")
    assert second.status == "budget_exhausted"
    assert second.report.attempted_calls == 2
    assert detector.assess.await_count == 2


async def test_long_and_ineligible_sources_are_reported_without_truncation():
    sources = [snapshot(), snapshot("b", "До 20 МБ."), snapshot("c", "X" * 1000)]
    service, _, detector = auditor(sources)
    result = await service.run(["a", "b", "c", "missing"], AuditLimits(max_article_chars=100))
    assert result.report.skipped_long_articles == 1
    assert result.report.skipped_ineligible_articles == 1
    assert result.report.selected_articles == 2
    assert detector.assess.await_count == 1


async def test_whole_job_timeout_includes_embedding():
    service, _, detector = auditor()

    async def slow_embed(texts):
        await asyncio.sleep(5)
        return []

    service.finder.embedder.embed = slow_embed
    result = await service.run(["a", "b"], AuditLimits(timeout_seconds=0.01))
    assert result.status == "retryable"
    assert result.report.stop_reason == "timeout"
    assert detector.assess.await_count == 0


async def test_duplicate_job_lease_rejects_parallel_run_without_model_calls():
    service, registry, detector = auditor()
    registry.claimable = False
    with pytest.raises(AuditAlreadyRunning):
        await service.run(["a", "b"], AuditLimits())
    assert detector.assess.await_count == 0


async def test_resume_rejects_changed_pool_or_config():
    service, _, _ = auditor()
    await service.run(["a", "b"], AuditLimits())
    with pytest.raises(ValueError, match="resume"):
        await service.run(["a", "c"], AuditLimits(), resume_job_id="job")


async def test_explicit_pool_limit_is_reported_and_bounded():
    service, _, detector = auditor([snapshot(), snapshot("b", "До 20 МБ."), snapshot("c", "До 20 МБ.")])
    result = await service.run(["c", "b", "a"], AuditLimits(max_articles=2))
    assert result.report.pool_limited is True
    assert result.report.selected_articles == 2
    assert detector.assess.await_count == 1


async def test_sqlite_audit_persists_results_and_reuses_them_in_next_job(tmp_path):
    from app.db import Database
    from app.models import KBArticle
    from app.services.source_conflicts import SourceConflictRegistry

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    await database.create_all()
    try:
        async with database.session_factory() as session:
            session.add_all(
                [
                    KBArticle(id="a", question="Размер файла?", answer="До 10 МБ.", category="Файлы"),
                    KBArticle(id="b", question="Размер файла?", answer="До 20 МБ.", category="Файлы"),
                ]
            )
            await session.commit()
        registry = SourceConflictRegistry(database.session_factory)
        detector = SimpleNamespace(version="fixture", assess=AsyncMock(return_value=assessment()))
        service = SourceConflictAuditor(registry, SourceConflictCandidateFinder(HashEmbedder()), detector)
        first = await service.run(["a", "b"], AuditLimits(), actor="admin")
        persisted = await registry.get_job(first.id)
        assert persisted.status == "done"
        assert persisted.report.conflicts == 1
        results = await registry.list_results()
        assert len(results) == 1
        assert results[0].job_id == first.id
        assert not results[0].stale
        second = await service.run(["a", "b"], AuditLimits(), actor="admin")
        assert second.id != first.id
        assert second.status == "done"
        assert second.report.cached_pairs == 1
        assert second.report.attempted_calls == 0
        assert detector.assess.await_count == 1
    finally:
        await database.dispose()


async def test_resume_reloads_calls_spent_by_previous_owner_before_claim():
    service, registry, detector = auditor()
    limits = AuditLimits(max_calls=1)
    await registry.create_job({"article_ids": ["a", "b"]}, limits, service.processing_version(limits))

    async def intervening_owner_then_claim(*args, **kwargs):
        # This owner fetched the job before the previous owner spent its last call.
        # The previous owner then checkpointed and released the lease as retryable.
        registry.job = registry.job.model_copy(
            update={"status": "running", "report": AuditReport(attempted_calls=1, failures=1)}
        )
        return True

    registry.claim_job = intervening_owner_then_claim
    result = await service.run(["a", "b"], limits, resume_job_id="job")
    assert result.status == "budget_exhausted"
    assert result.report.attempted_calls == 1
    assert result.report.failures == 1
    detector.assess.assert_not_awaited()


async def test_short_article_with_oversized_metadata_is_skipped_without_model_call():
    service, _, detector = auditor([snapshot(), snapshot("b", "До 20 МБ.", origin={"file": "X" * 5000})])
    result = await service.run(["a", "b"], AuditLimits())
    assert result.report.skipped_long_articles == 1
    assert result.report.selected_articles == 1
    detector.assess.assert_not_awaited()


@pytest.mark.parametrize("malformed", ["quote", "schema"])
async def test_automatic_finishes_other_pairs_then_retries_only_invalid_pair(malformed):
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    from app.services.source_conflict_candidates import CandidatePair
    from app.services.source_conflict_detector import EvidenceValidationError

    sources = [snapshot(), snapshot("b", "До 20 МБ."), snapshot("c", "До 30 МБ.")]
    registry = MemoryRegistry(sources)
    pairs = [
        CandidatePair(sources[0], sources[1], 1),
        CandidatePair(sources[0], sources[2], 1),
        CandidatePair(sources[1], sources[2], 1),
    ]
    finder = SimpleNamespace(version="ordered", find=AsyncMock(return_value=pairs))
    calls = []

    async def respond(left, right, **kwargs):
        calls.append((left.article_id, right.article_id))
        if len(calls) == 2:
            error = EvidenceValidationError if malformed == "quote" else UnexpectedModelBehavior
            raise error("invalid model evidence")
        if len(calls) == 3:
            assert registry.job.report.failures == 1  # Failure checkpointed before next call.
        return assessment()

    detector = SimpleNamespace(version="fake", assess=AsyncMock(side_effect=respond))
    service = SourceConflictAuditor(registry, finder, detector, continue_invalid_evidence=True)
    limits = AuditLimits(max_calls=6)
    first = await service.run(["a", "b", "c"], limits)
    assert first.status == "retryable"
    assert first.report.attempted_calls == 3
    assert first.report.evaluated_pairs == 2
    assert first.report.failures == 1
    assert first.report.stop_reason == "invalid_evidence"
    assert first.report.errors == [
        "invalid model evidence" if malformed == "quote" else "UnexpectedModelBehavior"
    ]
    second = await service.run(["a", "b", "c"], limits, resume_job_id=first.id)
    assert second.status == "done"
    assert second.report.cached_pairs == 2
    assert second.report.evaluated_pairs == 1
    assert second.report.attempted_calls == 4
    assert calls == [("a", "b"), ("a", "c"), ("b", "c"), ("a", "c")]


@pytest.mark.parametrize("error_name", ["provider", "deferred", "timeout", "value"])
async def test_continue_evidence_does_not_continue_provider_or_execution_errors(error_name):
    from pydantic_ai.exceptions import ModelHTTPError

    from app.services.source_conflict_auto import _DeferredCall

    service, _, detector = auditor([snapshot(), snapshot("b", "20 МБ"), snapshot("c", "30 МБ")])
    service.continue_invalid_evidence = True
    errors = {
        "provider": ModelHTTPError(503, "fixture"),
        "deferred": _DeferredCall("disabled"),
        "timeout": TimeoutError(),
        "value": ValueError("changed source"),
    }
    detector.assess.side_effect = errors[error_name]
    result = await service.run(["a", "b", "c"], AuditLimits())
    assert result.status == "retryable"
    assert result.report.attempted_calls == 1
    assert detector.assess.await_count == 1


async def test_manual_audit_still_stops_at_invalid_evidence_by_default():
    from app.services.source_conflict_detector import EvidenceValidationError

    service, _, detector = auditor([snapshot(), snapshot("b", "20 МБ"), snapshot("c", "30 МБ")])
    detector.assess.side_effect = EvidenceValidationError("invalid quote")
    result = await service.run(["a", "b", "c"], AuditLimits())
    assert result.status == "retryable"
    assert detector.assess.await_count == 1
