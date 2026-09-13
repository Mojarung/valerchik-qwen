"""Transactional automatic work, restart-safe ownership and provider call quota."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import update

from app.db import Database
from app.models import KBArticle, utcnow
from app.models_conflicts import SourceConflictAutoControl
from app.services.source_conflict_auto_queue import SourceConflictAutoQueue


@pytest.fixture
async def queue(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'auto.db'}")
    await database.create_all()
    service = SourceConflictAutoQueue(database.session_factory)
    yield service, database.session_factory
    await database.dispose()


async def add(factory, article_id="a", **fields):
    async with factory() as db:
        article = KBArticle(id=article_id, question="Лимит?", answer="10 МБ", category="Файлы", **fields)
        db.add(article)
        await db.commit()
        return article.source_version


async def test_source_changes_coalesce_and_rollback_or_counters_do_not_enqueue(queue):
    service, factory = queue
    initial = await add(factory)
    assert len(await service.list_items()) == 1
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.views += 1
        await db.commit()
    assert len(await service.list_items()) == 1
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.answer = "20 МБ"
        await db.flush()
        await db.rollback()
    assert len(await service.list_items()) == 1
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.answer = "30 МБ"
        await db.commit()
    rows = await service.list_items()
    assert len(rows) == 2
    assert next(row for row in rows if row.source_version == initial).status == "superseded"
    assert sum(row.status == "pending" for row in rows) == 1


async def test_drafts_do_not_queue_until_published_and_purge_invalidates(queue):
    service, factory = queue
    await add(factory, status="draft")
    await add(factory, "reference", origin={"kind": "reference"})
    assert await service.list_items() == []
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.status = "active"
        await db.commit()
    assert len(await service.list_items()) == 1
    async with factory() as db:
        await db.delete(await db.get(KBArticle, "a"))
        await db.commit()
    assert (await service.list_items())[0].status == "superseded"


async def test_global_lease_budget_and_restart_recovery(queue):
    service, factory = queue
    await add(factory)
    assert await service.acquire("first")
    assert not await service.acquire("second")
    item = await service.claim_next("first", debounce_seconds=0)
    assert item is not None and item.attempts == 1
    assert await service.attach_job(item.id, "first", "saved-job")
    results = await asyncio.gather(*[service.reserve_call("first", 2) for _ in range(4)])
    assert sum(results) == 2
    assert not await service.reserve_call("second", 100)
    async with factory() as db:
        await db.execute(
            update(SourceConflictAutoControl).values(lease_until=utcnow() - timedelta(seconds=1))
        )
        await db.commit()
    assert not await service.finish(item.id, "first", "done")
    assert await service.acquire("second")
    recovered = await service.claim_next("second", debounce_seconds=0)
    assert recovered.id == item.id and recovered.job_id == "saved-job"
    assert not await service.reserve_call("second", 2)
    assert await service.finish(recovered.id, "second", "done")
    assert await service.release("second")


async def test_retry_delay_defer_and_latest_revision_guard(queue):
    service, factory = queue
    await add(factory)
    assert await service.acquire("worker")
    item = await service.claim_next("worker", debounce_seconds=0)
    assert await service.defer(item.id, "worker", "daily_budget", 0)
    deferred = await service.get_item(item.id)
    assert deferred.attempts == 0 and deferred.status == "retryable"
    item = await service.claim_next("worker", debounce_seconds=0)
    assert await service.finish(item.id, "worker", "retryable", "ProviderError", retry_after_seconds=60)
    assert await service.claim_next("worker", debounce_seconds=0) is None
    refreshed = await service.retry(item.id)
    assert refreshed.attempts == 0 and refreshed.status == "pending"
    item = await service.claim_next("worker", debounce_seconds=0)
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.answer = "40 МБ"
        await db.commit()
    assert not await service.finish(item.id, "worker", "done")
    with pytest.raises(ValueError):
        await service.retry(item.id)


async def test_backfill_estimates_and_enqueues_entire_corpus_idempotently(queue):
    service, factory = queue
    # Simulate old corpus predating the feature without invoking ORM lifecycle.
    async with factory() as db:
        await db.execute(
            KBArticle.__table__.insert(),
            [
                {"id": f"old-{i}", "question": "Лимит?", "answer": "10 МБ", "category": "Файлы"}
                for i in range(520)
            ],
        )
        await db.commit()
    estimate = await service.estimate()
    assert estimate["eligible_articles"] == estimate["unchecked_articles"] == 520
    assert estimate["estimated_max_calls"] == 520
    enqueued = await service.enqueue_unchecked()
    assert enqueued["queued"] == 520
    assert (await service.enqueue_unchecked())["queued"] == 0
    assert (await service.estimate())["unchecked_articles"] == 0
    assert (await service.summary(100))["counts"]["pending"] == 520


async def test_new_articles_take_priority_over_historical_backfill(queue):
    service, factory = queue
    async with factory() as db:
        await db.execute(
            KBArticle.__table__.insert(),
            [
                {"id": "old", "question": "Лимит?", "answer": "10 МБ", "category": "Файлы"},
            ],
        )
        await db.commit()
    assert (await service.enqueue_unchecked())["queued"] == 1
    await add(factory, "fresh")
    assert await service.acquire("worker")
    item = await service.claim_next("worker", debounce_seconds=0)
    assert item.article_id == "fresh"


async def test_daily_budget_resets_only_on_new_utc_day(queue):
    service, factory = queue
    assert await service.acquire("worker")
    assert await service.reserve_call("worker", 1)
    assert not await service.reserve_call("worker", 1)
    async with factory() as db:
        await db.execute(update(SourceConflictAutoControl).values(budget_day="2020-01-01"))
        await db.commit()
    assert (await service.summary(1))["calls_today"] == 0
    assert await service.reserve_call("worker", 1)
    assert not await service.reserve_call("worker", 1)
    assert (await service.summary(1))["calls_today"] == 1


def test_auto_setting_limits_are_bounded():
    from app.dynamic_settings import SETTINGS, SettingsValueError, validate_updates

    assert SETTINGS["source_conflicts.auto_enabled"].default is True
    assert validate_updates({"source_conflicts.auto_daily_call_limit": 50000})
    with pytest.raises(SettingsValueError):
        validate_updates({"source_conflicts.auto_daily_call_limit": 50001})
    with pytest.raises(SettingsValueError):
        validate_updates({"source_conflicts.auto_max_attempts": 0})


@pytest.mark.parametrize("legacy_version", [None, "old-version"])
async def test_backfill_stale_page_cannot_supersede_concurrent_edit(queue, monkeypatch, legacy_version):
    from app.services import source_conflict_auto_queue as queue_module

    service, factory = queue
    async with factory() as db:
        await db.execute(
            KBArticle.__table__.insert(),
            {
                "id": "old",
                "question": "Лимит?",
                "answer": "10 МБ",
                "category": "Файлы",
                "source_version": legacy_version,
            },
        )
        await db.commit()
    original_page = queue_module._page
    fresh_version = None

    async def racing_page(db, cursor):
        nonlocal fresh_version
        articles = await original_page(db, cursor)
        if articles and fresh_version is None:
            async with factory() as editor:
                article = await editor.get(KBArticle, "old")
                article.answer = "20 МБ"
                await editor.commit()
                fresh_version = article.source_version
        return articles

    monkeypatch.setattr(queue_module, "_page", racing_page)
    await service.enqueue_unchecked()
    async with factory() as db:
        assert (await db.get(KBArticle, "old")).source_version == fresh_version
    assert await service.acquire("worker")
    item = await service.claim_next("worker", debounce_seconds=0)
    assert item is not None and item.source_version == fresh_version


@pytest.mark.parametrize("live_job", [False, True])
async def test_explicit_retry_retires_failed_job_but_preserves_live_job_and_daily_quota(queue, live_job):
    from app.schemas.source_conflicts import AuditLimits, AuditReport
    from app.services.source_conflict_jobs import SourceConflictJobs

    service, factory = queue
    await add(factory)
    jobs = SourceConflictJobs(factory)
    limits = AuditLimits()
    scope = {"article_ids": ["a", "b"]}
    job = await jobs.create_job(scope, limits, "test")
    assert await jobs.claim_job(job.id, "audit")
    if not live_job:
        assert await jobs.finish_job(job.id, "audit", "retryable", AuditReport(attempted_calls=5))
    assert await service.acquire("worker")
    item = await service.claim_next("worker", debounce_seconds=0)
    assert await service.attach_job(item.id, "worker", job.id)
    assert await service.reserve_call("worker", 1)
    assert await service.finish(item.id, "worker", "blocked")
    if live_job:
        with pytest.raises(ValueError, match="running"):
            await service.retry(item.id)
        assert (await service.get_item(item.id)).job_id == job.id
        assert (await service.get_item(item.id)).status == "blocked"
    else:
        retried = await service.retry(item.id)
        assert retried.status == "pending" and retried.job_id is None
        assert (await jobs.get_job(job.id)).status == "stopped"
        assert (await jobs.create_job(scope, limits, "test")).id != job.id
    assert (await service.summary(1))["calls_today"] == 1
    assert not await service.reserve_call("worker", 1)


async def test_provider_cooldown_survives_release_and_restart_without_resetting_quota(queue):
    service, factory = queue
    assert await service.acquire("worker")
    assert await service.reserve_call("worker", 10)
    assert not await service.pause_provider("stranger", 60)
    assert await service.pause_provider("worker", 60)
    first_until = (await service.summary(10))["paused_until"]
    assert await service.pause_provider("worker", 1)
    assert (await service.summary(10))["paused_until"] == first_until
    assert not await service.reserve_call("worker", 10)
    assert await service.release("worker")
    restarted = SourceConflictAutoQueue(factory)
    assert not await restarted.acquire("new-worker")
    assert (await restarted.summary(10))["provider_paused"]
    async with factory() as db:
        await db.execute(
            update(SourceConflictAutoControl).values(paused_until=utcnow() - timedelta(seconds=1))
        )
        await db.commit()
    assert await restarted.acquire("new-worker")
    assert await restarted.reserve_call("new-worker", 10)
    assert (await restarted.summary(10))["calls_today"] == 2
