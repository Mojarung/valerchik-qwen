"""Source audit remains correct across normal article lifecycle and stale workers."""

from datetime import timedelta

import pytest

from app.db import Database
from app.models import KBArticle, utcnow
from app.schemas.source_conflicts import AuditLimits, AuditReport, PairAssessment
from app.services.source_conflicts import SourceConflictRegistry


@pytest.fixture
async def registry(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'conflicts.db'}")
    await database.create_all()
    async with database.session_factory() as session:
        session.add_all(
            [
                KBArticle(id="a", question="Лимит?", answer="До 10 МБ", category="Файлы"),
                KBArticle(id="b", question="Лимит?", answer="До 20 МБ", category="Файлы"),
            ]
        )
        await session.commit()
    yield SourceConflictRegistry(database.session_factory), database.session_factory
    await database.dispose()


def assessment():
    return PairAssessment(
        same_fact="yes",
        conditions="overlap",
        claims="incompatible",
        material="yes",
        left_quote="До 10 МБ",
        right_quote="До 20 МБ",
    )


async def test_canonical_pair_cache_and_exact_quotes(registry):
    service, _ = registry
    a, b = await service.snapshot_articles(["a", "b"])
    result = await service.record_pair(a, b, assessment(), "v1")
    assert result.outcome == "conflict"
    assert not result.stale
    assert (await service.get_pair(b.id, a.id, "v1")).id == result.id
    assert await service.get_pair(a.id, b.id, "v2") is None
    bad = assessment().model_copy(update={"left_quote": "До 999 МБ"})
    with pytest.raises(ValueError, match="quote"):
        await service.record_pair(a, b, bad, "v2")
    assert len(await service.list_results()) == 1


async def test_updates_archive_republish_and_purge(registry):
    service, factory = registry
    a, b = await service.snapshot_articles(["a", "b"])
    await service.record_pair(a, b, assessment(), "v1")
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.views += 1
        article.helpful += 1
        await db.commit()
    assert (await service.snapshot_articles(["a"]))[0].id == a.id
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.status = "archived"
        await db.commit()
    assert not await service.list_results()
    assert (await service.list_results(include_stale=True))[0].stale
    assert await service.get_pair(a.id, b.id, "v1") is None
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.status = "active"
        await db.commit()
    new_a = (await service.snapshot_articles(["a"]))[0]
    assert new_a.source_version != a.source_version
    assert new_a.content_hash == a.content_hash
    late = await service.record_pair(a, b, assessment(), "v2")
    assert late.stale
    async with factory() as db:
        article = await db.get(KBArticle, "b")
        await db.delete(article)
        await db.commit()
    assert len(await service.list_results(include_stale=True)) == 2
    assert not await service.list_results()


async def test_content_and_metadata_changes_invalidate_but_rollback_does_not(registry):
    service, factory = registry
    a = (await service.snapshot_articles(["a"]))[0]
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.audience = "supplier"
        article.origin = {"file": "manual.pdf", "locator": "page 2"}
        await db.commit()
    changed = (await service.snapshot_articles(["a"]))[0]
    assert changed.content_hash != a.content_hash
    assert changed.audience == "supplier"
    assert changed.origin["file"] == "manual.pdf"
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.answer = "changed"
        await db.flush()
        await db.rollback()
    assert (await service.snapshot_articles(["a"]))[0].id == changed.id


async def test_job_lease_and_progress_survive_resume(registry):
    service, factory = registry
    job = await service.create_job({"article_ids": ["a", "b"]}, AuditLimits(), "v1", "admin")
    assert await service.claim_job(job.id, "worker1")
    assert not await service.claim_job(job.id, "worker2")
    report = AuditReport(evaluated_pairs=1)
    assert not await service.checkpoint_job(job.id, "worker2", report)
    assert await service.checkpoint_job(job.id, "worker1", report)
    from app.models_conflicts import SourceConflictAuditJob

    async with factory() as db:
        row = await db.get(SourceConflictAuditJob, job.id)
        row.lease_until = utcnow() - timedelta(seconds=1)
        await db.commit()
    assert not await service.finish_job(job.id, "worker1", "done", report)
    assert await service.claim_job(job.id, "worker2")
    assert (await service.get_job(job.id)).report.evaluated_pairs == 1
    assert await service.finish_job(job.id, "worker2", "done", report)
    assert not await service.claim_job(job.id, "worker3")


async def test_duplicate_manual_jobs_share_a_lease(registry):
    import asyncio

    service, _ = registry
    jobs = await asyncio.gather(
        *[service.create_job({"article_ids": ["a", "b"]}, AuditLimits(), "v1", "admin") for _ in range(2)]
    )
    assert jobs[0].id == jobs[1].id
    assert await service.claim_job(jobs[0].id, "worker")
    assert await service.finish_job(jobs[0].id, "worker", "done", AuditReport())
    subsequent = await service.create_job({"article_ids": ["a", "b"]}, AuditLimits(), "v1")
    assert subsequent.id != jobs[0].id


async def test_import_replacement_stales_evidence_even_when_vectors_fail(registry):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.ingest.pipeline import persist
    from app.ingest.records import ArticleRecord

    service, factory = registry
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.doc_key = "manual"
        await db.commit()
    a, b = await service.snapshot_articles(["a", "b"])
    original = await service.record_pair(a, b, assessment(), "v1")
    kb = SimpleNamespace(
        index_many=AsyncMock(side_effect=RuntimeError("index unavailable")),
        remove_many=AsyncMock(side_effect=RuntimeError("index unavailable")),
    )
    rt = SimpleNamespace(db=SimpleNamespace(session_factory=factory), kb=kb)
    result = await persist(
        rt,
        [ArticleRecord(question="Лимит?", answer="До 30 МБ", line=1)],
        title="manual",
        params={"status": "active"},
    )
    assert result["created"] == result["replaced"] == 1
    assert kb.index_many.await_count == kb.remove_many.await_count == 1
    assert not await service.list_results()
    assert (await service.get_result(original.id)).stale
    assert (await service.snapshot_articles(result["article_ids"]))[0].answer == "До 30 МБ"


async def test_search_phrasings_preserve_version_and_non_answers_are_excluded(registry):
    service, factory = registry
    a = (await service.snapshot_articles(["a"]))[0]
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.origin = {"phrasings": ["Другой поисковый вопрос"]}
        other = await db.get(KBArticle, "b")
        other.origin = {"kind": "operator_case"}
        await db.commit()
    current = await service.snapshot_articles(["a", "b"])
    assert len(current) == 1
    assert current[0].id == a.id


async def test_review_lifecycle_and_audience_filtered_current_lookup(registry):
    from app.schemas.source_conflicts import ConflictReview

    service, factory = registry
    a, b = await service.snapshot_articles(["a", "b"])
    result = await service.record_pair(a, b, assessment(), "v1")
    assert not await service.get_current_conflicts(["a"], "supplier")
    assert not await service.get_current_conflicts(["missing"], "supplier")
    reviewed = await service.review(
        result.id,
        ConflictReview(status="confirmed", preferred_article_id="a", note="Сверены лимиты"),
        "admin",
    )
    assert reviewed.review_status == "confirmed"
    assert reviewed.preferred_article_id == "a"
    assert reviewed.review_note == "Сверены лимиты"
    assert reviewed.reviewed_by == "admin"
    assert reviewed.reviewed_at is not None
    assert (await service.get_current_conflicts(["a"], "supplier"))[0].id == result.id
    await service.review(result.id, ConflictReview(status="dismissed", note="Разные формы"), "admin")
    assert not await service.get_current_conflicts(["a"], "supplier")
    await service.review(result.id, ConflictReview(status="unreviewed", note="Нужна перепроверка"), "admin")
    async with factory() as db:
        article = await db.get(KBArticle, "a")
        article.answer = "До 40 МБ"
        await db.commit()
    with pytest.raises(ValueError, match="current"):
        await service.review(
            result.id,
            ConflictReview(status="confirmed", preferred_article_id="a", note="Old"),
            "admin",
        )
    assert not await service.get_current_conflicts(["a"], "supplier")


async def test_review_rejects_article_outside_comparison(registry):
    from app.schemas.source_conflicts import ConflictReview

    service, _ = registry
    a, b = await service.snapshot_articles(["a", "b"])
    result = await service.record_pair(a, b, assessment(), "v1")
    with pytest.raises(ValueError, match="belong"):
        await service.review(
            result.id,
            ConflictReview(status="confirmed", preferred_article_id="another"),
            "admin",
        )


async def test_same_question_conflicts_form_one_group_with_three_choices(registry):
    from app.schemas.source_conflicts import ConflictReview

    service, factory = registry
    async with factory() as db:
        db.add(KBArticle(id="c", question="Лимит?", answer="До 30 МБ", category="Файлы"))
        await db.commit()
    a, b, c = await service.snapshot_articles(["a", "b", "c"])
    first = await service.record_pair(
        a,
        b,
        assessment().model_copy(update={"fact": "Максимальный размер файла"}),
        "v1",
    )
    second = await service.record_pair(
        b,
        c,
        assessment().model_copy(
            update={
                "left_quote": "До 20 МБ",
                "right_quote": "До 30 МБ",
                "fact": "Лимит загружаемого файла",
            }
        ),
        "v1",
    )

    groups = await service.list_groups(reviewed=False)
    assert len(groups) == 1
    group = groups[0]
    assert set(group.result_ids) == {first.id, second.id}
    assert {item.source.article_id for item in group.sources} == {"a", "b", "c"}

    reviewed = await service.review_group(
        group.id,
        ConflictReview(status="confirmed", preferred_article_id="c"),
        "admin",
    )
    assert reviewed.review_status == "confirmed"
    assert reviewed.preferred_article_id == "c"
    results = await service.get_current_conflicts(["a"], "supplier")
    assert len(results) == 1
    assert results[0].preferred_source is not None
    assert results[0].preferred_source.article_id == "c"
    assert results[0].preferred_source.answer == "До 30 МБ"

    async with factory() as db:
        selected = await db.get(KBArticle, "c")
        selected.answer = "До 40 МБ"
        await db.commit()
    assert not await service.list_groups()
    assert not await service.get_current_conflicts(["a"], "supplier")


async def test_different_questions_are_not_merged_into_one_group(registry):
    service, factory = registry
    async with factory() as db:
        db.add(KBArticle(id="c", question="Сколько хранится файл?", answer="30 дней", category="Файлы"))
        await db.commit()
    a, b, c = await service.snapshot_articles(["a", "b", "c"])
    await service.record_pair(a, b, assessment(), "v1")
    await service.record_pair(
        b,
        c,
        assessment().model_copy(update={"left_quote": "До 20 МБ", "right_quote": "30 дней"}),
        "v1",
    )
    assert len(await service.list_groups()) == 2


async def test_current_lookup_rejects_opposite_roles(registry):
    service, factory = registry
    async with factory() as db:
        for article_id in ("a", "b"):
            article = await db.get(KBArticle, article_id)
            article.audience = "supplier"
        await db.commit()
    a, b = await service.snapshot_articles(["a", "b"])
    result = await service.record_pair(a, b, assessment(), "v1")
    from app.schemas.source_conflicts import ConflictReview

    await service.review(
        result.id,
        ConflictReview(status="confirmed", preferred_article_id="a"),
        "admin",
    )
    assert len(await service.get_current_conflicts(["a"], "supplier")) == 1
    assert not await service.get_current_conflicts(["a"], "customer")
    assert not await service.get_current_conflicts(["a"], None)
