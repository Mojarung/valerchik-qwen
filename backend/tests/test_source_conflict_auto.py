"""Automatic auditing is durable, source-versioned and bounded before paid calls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.adapters.embeddings import HashEmbedder
from app.db import Database
from app.models import KBArticle
from app.services.source_conflict_auto import SourceConflictAutoWorker
from app.services.source_conflict_detector import BatchAssessmentResult
from tests.test_source_conflict_detector import assessment


def batch_assessments(pairs, **kwargs):
    return BatchAssessmentResult(
        assessments=[
            assessment(
                left_quote=left.answer,
                right_quote=right.answer,
                left_scope_quote=left.answer,
                right_scope_quote=right.answer,
            )
            for left, right in pairs
        ],
        errors=[],
    )


@pytest.fixture
async def automatic(tmp_path, monkeypatch):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'auto.db'}")
    await database.create_all()
    settings = {
        "llm.base_url": "https://model.invalid/v1",
        "llm.model": "fake",
        "llm.api_key": "test",
        "source_conflicts.auto_enabled": True,
        "source_conflicts.auto_debounce_seconds": 0,
        "source_conflicts.auto_max_neighbours": 1,
        "source_conflicts.auto_daily_call_limit": 100,
        "source_conflicts.auto_max_attempts": 1,
        "source_conflicts.auto_retry_seconds": 0,
    }
    worker = SourceConflictAutoWorker(database.session_factory, settings, HashEmbedder(), object())
    worker.discovery = SimpleNamespace(find=AsyncMock(return_value=["b"]))
    detector = SimpleNamespace(
        version="fake",
        assess=AsyncMock(return_value=assessment()),
        assess_many=AsyncMock(side_effect=batch_assessments),
        aclose=AsyncMock(),
    )
    monkeypatch.setattr("app.services.source_conflict_auto.create_conflict_detector", lambda dyn: detector)
    async with database.session_factory() as db:
        db.add_all(
            [
                KBArticle(id="a", question="Размер файла?", answer="До 10 МБ.", category="Файлы"),
                KBArticle(id="b", question="Размер файла?", answer="До 20 МБ.", category="Файлы"),
            ]
        )
        await db.commit()
    yield worker, database, settings, detector
    await worker.stop()
    await database.dispose()


async def test_created_article_is_processed_without_manual_audit(automatic):
    worker, _, _, detector = automatic
    assert await worker.tick()
    entries = await worker.queue.list_items()
    first = next(item for item in entries if item.article_id == "a")
    assert first.status == "done"
    assert first.job_id
    assert detector.assess_many.await_count == 1
    assert (await worker.registry.get_job(first.job_id)).report.conflicts == 1
    assert (await worker.queue.summary(100))["calls_today"] == 1


async def test_missing_model_and_disabled_setting_never_consume_calls(automatic):
    worker, _, settings, detector = automatic
    settings["llm.model"] = ""
    assert not await worker.tick()
    assert (await worker.status()).blocked_reason == "model_not_configured"
    settings["llm.model"] = "fake"
    settings["source_conflicts.auto_enabled"] = False
    assert not await worker.tick()
    detector.assess_many.assert_not_awaited()
    assert all(item.status == "pending" for item in await worker.queue.list_items())


async def test_exhausted_daily_budget_blocks_before_model_request(automatic):
    worker, _, settings, detector = automatic
    settings["source_conflicts.auto_daily_call_limit"] = 1
    assert await worker.queue.acquire(worker.owner)
    assert await worker.queue.reserve_call(worker.owner, 1)
    await worker.queue.release(worker.owner)
    assert not await worker.tick()
    assert (await worker.status()).blocked_reason == "daily_call_limit"
    detector.assess_many.assert_not_awaited()


async def test_provider_failure_is_persisted_without_automatic_second_call(automatic):
    worker, _, _, detector = automatic
    detector.assess_many.side_effect = TimeoutError("provider timeout")
    assert await worker.tick()
    first = next(item for item in await worker.queue.list_items() if item.article_id == "a")
    assert first.status == "blocked"
    assert first.attempts == 1
    assert detector.assess_many.await_count == 1


async def test_edit_supersedes_old_version_and_is_automatically_queued(automatic):
    worker, database, _, detector = automatic
    async with database.session_factory() as db:
        article = await db.get(KBArticle, "a")
        article.answer = "До 15 МБ."
        await db.commit()
    rows = [item for item in await worker.queue.list_items() if item.article_id == "a"]
    assert {item.status for item in rows} == {"pending", "superseded"}
    assert detector.assess_many.await_count == 0


async def test_backfill_estimate_and_enqueue_are_idempotent(automatic):
    worker, _, _, detector = automatic
    first = await worker.backfill()
    second = await worker.backfill()
    assert first.enqueued == second.enqueued == 0
    assert first.eligible == 2
    detector.assess_many.assert_not_awaited()


async def test_restart_reuses_completed_pairs_and_durable_queue(automatic):
    worker, database, settings, detector = automatic
    assert await worker.tick()
    restarted = SourceConflictAutoWorker(database.session_factory, settings, HashEmbedder(), object())
    restarted.discovery = SimpleNamespace(find=AsyncMock(return_value=["a"]))
    try:
        assert await restarted.tick()
        assert all(item.status == "done" for item in await restarted.queue.list_items())
        assert detector.assess_many.await_count == 1
        assert (await restarted.queue.summary(100))["calls_today"] == 1
    finally:
        await restarted.stop()


async def test_edit_during_discovery_prevents_spending_on_superseded_source(automatic):
    worker, database, _, detector = automatic

    async def changed_during_discovery(article_id, limit):
        async with database.session_factory() as db:
            article = await db.get(KBArticle, article_id)
            article.answer = "До 15 МБ."
            await db.commit()
        return ["b"]

    worker.discovery.find.side_effect = changed_during_discovery
    assert await worker.tick()
    detector.assess_many.assert_not_awaited()
    assert (await worker.queue.summary(100))["calls_today"] == 0
    items = [item for item in await worker.queue.list_items() if item.article_id == "a"]
    assert {item.status for item in items} == {"superseded", "pending"}


async def test_restart_waits_for_unexpired_audit_lease_without_consuming_retry(automatic):
    import asyncio
    from datetime import timedelta

    from sqlalchemy import delete

    from app.models import utcnow
    from app.models_conflicts import SourceConflictAutoItem

    worker, database, settings, detector = automatic
    async with database.session_factory() as db:
        await db.execute(delete(SourceConflictAutoItem).where(SourceConflictAutoItem.article_id == "b"))
        await db.commit()
    entered = asyncio.Event()

    async def interrupted(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    detector.assess_many.side_effect = interrupted
    task = asyncio.create_task(worker.tick())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    item = (await worker.queue.list_items())[0]
    job = await worker.registry.get_job(item.job_id)
    assert job.status == "running" and job.lease_until > utcnow()
    async with database.session_factory() as db:
        row = await db.get(SourceConflictAutoItem, item.id)
        row.available_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    restarted = SourceConflictAutoWorker(database.session_factory, settings, HashEmbedder(), object())
    try:
        assert await restarted.tick()
        waiting = await restarted.queue.get_item(item.id)
        assert waiting.status == "retryable"
        assert waiting.available_at >= job.lease_until
        assert waiting.attempts == item.attempts
        assert detector.assess_many.await_count == 1
    finally:
        await restarted.stop()


async def test_zero_daily_limit_pauses_without_spending(automatic):
    worker, _, settings, detector = automatic
    settings["source_conflicts.auto_daily_call_limit"] = 0
    assert not await worker.tick()
    detector.assess_many.assert_not_awaited()
    state = await worker.status()
    assert state.daily_call_limit == state.remaining_calls_today == 0
    assert state.blocked_reason == "daily_call_limit"


async def test_explicit_admin_retry_gets_fresh_job_budget_under_same_daily_cap(automatic):
    worker, _, _, detector = automatic
    detector.assess_many.side_effect = TimeoutError("provider timeout")
    await worker.tick()
    failed = next(item for item in await worker.queue.list_items() if item.article_id == "a")
    assert failed.status == "blocked"
    assert (await worker.queue.summary(100))["calls_today"] == 1
    await worker.retry(failed.id)
    detector.assess_many.side_effect = lambda pairs, **kwargs: BatchAssessmentResult(
        assessments=[assessment() for _ in pairs], errors=[]
    )
    await worker.tick()
    await worker.tick()  # The other pending anchor is visited before the explicit retry.
    completed = await worker.queue.get_item(failed.id)
    assert completed.status == "done"
    assert completed.job_id != failed.job_id
    assert detector.assess_many.await_count == 2
    assert (await worker.queue.summary(100))["calls_today"] == 2


async def test_changed_neighbour_is_rejected_before_daily_reservation(automatic):
    from app.services.source_conflict_auto import _DeferredCall, _ReservedDetector

    worker, database, _, detector = automatic
    await worker.queue.acquire(worker.owner)
    item = await worker.queue.claim_next(worker.owner, debounce_seconds=0)
    left, right = await worker.registry.snapshot_articles(["a", "b"])
    async with database.session_factory() as db:
        article = await db.get(KBArticle, "b")
        article.answer = "До 25 МБ."
        await db.commit()
    wrapper = _ReservedDetector(detector, worker, item)
    with pytest.raises(_DeferredCall):
        await wrapper.assess(left, right, timeout_s=2, max_output_tokens=300)
    assert wrapper.deferred_reason == "neighbour_changed"
    detector.assess_many.assert_not_awaited()
    assert (await worker.queue.summary(100))["calls_today"] == 0
    await worker.queue.release(worker.owner)


async def test_estimated_pending_calls_are_one_per_article(automatic):
    worker, _, _, _ = automatic
    state = await worker.status()
    assert state.estimated_pending_calls == 2


@pytest.mark.parametrize("status_code", [401, 429, 503])
async def test_provider_error_pauses_other_articles_and_survives_restart(automatic, status_code):
    from pydantic_ai.exceptions import ModelHTTPError

    worker, database, settings, detector = automatic
    detector.assess_many.side_effect = ModelHTTPError(status_code=status_code, model_name="fake", body=None)
    assert await worker.tick()
    assert not await worker.tick()
    restarted = SourceConflictAutoWorker(database.session_factory, settings, HashEmbedder(), object())
    try:
        assert not await restarted.tick()
        assert (await restarted.status()).blocked_reason == "provider_unavailable"
        assert detector.assess_many.await_count == 1
        assert (await restarted.queue.summary(100))["calls_today"] == 1
    finally:
        await restarted.stop()


async def test_neighbour_change_after_model_call_is_blocked_without_second_call(automatic):
    worker, database, settings, detector = automatic
    settings["source_conflicts.auto_max_neighbours"] = 2
    async with database.session_factory() as db:
        db.add(KBArticle(id="c", question="Размер файла?", answer="До 30 МБ.", category="Файлы"))
        await db.commit()
    worker.discovery.find.return_value = ["b", "c"]
    called = 0

    async def change_next_neighbour(pairs, **kwargs):
        nonlocal called
        called += 1
        if called == 1:
            async with database.session_factory() as db:
                article = await db.get(KBArticle, "c")
                article.answer = "До 40 МБ."
                await db.commit()
        return batch_assessments(pairs)

    detector.assess_many.side_effect = change_next_neighbour
    assert await worker.tick()
    waiting = next(item for item in await worker.queue.list_items() if item.article_id == "a")
    previous = await worker.registry.get_job(waiting.job_id)
    assert waiting.last_error == "neighbour_changed"
    assert waiting.status == "blocked"
    assert previous.report.attempted_calls == 1
    assert detector.assess_many.await_count == 1
    assert worker.discovery.find.await_count == 1
    assert (await worker.queue.summary(100))["calls_today"] == 1


async def test_different_anchors_same_pool_have_distinct_jobs_and_shared_pair_cache(automatic):
    worker, database, settings, detector = automatic
    settings["source_conflicts.auto_max_neighbours"] = 2
    async with database.session_factory() as db:
        db.add(KBArticle(id="c", question="Размер файла?", answer="До 30 МБ.", category="Файлы"))
        await db.commit()
    worker.discovery.find.side_effect = lambda anchor, limit: [
        key for key in ("a", "b", "c") if key != anchor
    ]

    assert await worker.tick()
    assert await worker.tick()
    rows = {item.article_id: item for item in await worker.queue.list_items()}
    assert rows["a"].job_id != rows["b"].job_id
    second = await worker.registry.get_job(rows["b"].job_id)
    assert second.scope["anchor_id"] == "b"
    assert second.status == "done"
    assert second.report.cached_pairs == 1
    assert second.report.evaluated_pairs == 1
    assert detector.assess_many.await_count == 2
    assert all(call.kwargs["max_output_tokens"] == 4000 for call in detector.assess_many.await_args_list)


async def test_legacy_unanchored_job_is_rebuilt_without_resetting_budget(automatic):
    from app.schemas.source_conflicts import AuditLimits, AuditReport

    worker, _, _, detector = automatic
    old = await worker.registry.create_job({"article_ids": ["a", "b"]}, AuditLimits(max_calls=2), "legacy")
    assert await worker.registry.claim_job(old.id, "legacy")
    assert await worker.registry.finish_job(old.id, "legacy", "done", AuditReport(attempted_calls=1))
    assert await worker.queue.acquire(worker.owner)
    item = await worker.queue.claim_next(worker.owner, debounce_seconds=0)
    assert await worker.queue.attach_job(item.id, worker.owner, old.id)
    assert await worker.queue.defer(item.id, worker.owner, "worker_stopped", retry_after_seconds=0)
    await worker.queue.release(worker.owner)
    # Queue visits the older remaining b row first, then the deferred a row.
    await worker.tick()
    await worker.tick()
    row = next(item for item in await worker.queue.list_items() if item.article_id == "a")
    new = await worker.registry.get_job(row.job_id)
    assert new.id != old.id
    assert new.scope["anchor_id"] == "a"
    assert new.limits.max_calls == 1
    assert (await worker.registry.get_job(old.id)).status == "stopped"
    assert detector.assess_many.await_count == 1


async def test_invalid_batch_is_not_retried_automatically(automatic):
    from sqlalchemy import delete

    from app.models_conflicts import SourceConflictAutoItem

    worker, database, settings, detector = automatic
    settings["source_conflicts.auto_max_neighbours"] = 3
    settings["source_conflicts.auto_daily_call_limit"] = 4
    async with database.session_factory() as db:
        db.add_all(
            [
                KBArticle(id="c", question="Размер файла?", answer="До 30 МБ.", category="Файлы"),
                KBArticle(id="d", question="Размер файла?", answer="До 40 МБ.", category="Файлы"),
            ]
        )
        await db.commit()
        await db.execute(delete(SourceConflictAutoItem).where(SourceConflictAutoItem.article_id != "a"))
        await db.commit()
    worker.discovery.find.return_value = ["b", "c", "d"]
    checked = []

    async def response(pairs, **kwargs):
        results = []
        errors = []
        for index, (left, right) in enumerate(pairs, start=1):
            checked.append(right.article_id)
            if right.article_id == "c":
                results.append(None)
                errors.append(f"p{index}: Evidence quote is not an exact substring of its source")
            else:
                results.append(
                    assessment(
                        left_quote=left.answer,
                        right_quote=right.answer,
                        left_scope_quote=left.answer,
                        right_scope_quote=right.answer,
                    )
                )
        return BatchAssessmentResult(assessments=results, errors=errors)

    detector.assess_many.side_effect = response
    assert await worker.tick()
    first = (await worker.queue.list_items())[0]
    assert first.status == "blocked"
    first_job = await worker.registry.get_job(first.job_id)
    assert first_job.report.evaluated_pairs == 2
    assert first_job.report.attempted_calls == 1
    assert (await worker.queue.summary(4))["calls_today"] == 1
    assert checked == ["b", "c", "d"]
    assert detector.assess_many.await_count == 1
