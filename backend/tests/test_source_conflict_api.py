"""Staff review and nonblocking paid audit requests through the real app."""

import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models import AuditLog, KBArticle, new_id
from app.schemas.source_conflicts import PairAssessment
from app.services.source_conflicts import SourceConflictRegistry

ROOT = "/api/source-conflicts"


@pytest.fixture
async def sources(client):
    factory = client.app.state.rt.db.session_factory
    ids = [new_id(), new_id()]
    async with factory() as db:
        db.add_all(
            [
                KBArticle(id=ids[0], question="Размер файла?", answer="До 10 МБ.", category="Тест API"),
                KBArticle(id=ids[1], question="Размер файла?", answer="До 20 МБ.", category="Тест API"),
            ]
        )
        await db.commit()
    service = SourceConflictRegistry(factory)
    a, b = await service.snapshot_articles(ids)
    result = await service.record_pair(
        a,
        b,
        PairAssessment(
            same_fact="yes",
            conditions="overlap",
            claims="incompatible",
            material="yes",
            left_quote="10 МБ",
            right_quote="20 МБ",
            fact="Лимит",
            explanation="Разные лимиты",
        ),
        "api-fixture",
    )
    return ids, result.id


async def test_staff_reads_admin_reviews_and_stale_review_is_rejected(
    client,
    admin_headers,
    operator_headers,
    sources,
):
    ids, result_id = sources
    assert client.get(f"{ROOT}/results").status_code == 401
    response = client.get(f"{ROOT}/results", params={"article_id": ids[0]}, headers=operator_headers)
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [result_id]
    review_url = f"{ROOT}/results/{result_id}/review"
    body = {
        "status": "confirmed",
        "preferred_article_id": ids[0],
        "note": "Сверено по источникам",
    }
    assert client.patch(review_url, json=body, headers=operator_headers).status_code == 403
    updated = client.patch(review_url, json=body, headers=admin_headers)
    assert updated.status_code == 200
    assert updated.json()["reviewed_by"] == "admin"
    assert updated.json()["review_status"] == "confirmed"
    assert updated.json()["preferred_article_id"] == ids[0]
    assert len(client.get(f"{ROOT}/results", params={"reviewed": True}, headers=operator_headers).json()) == 1
    assert client.get(f"{ROOT}/results", params={"reviewed": False}, headers=operator_headers).json() == []
    async with client.app.state.rt.db.session_factory() as db:
        log = await db.scalar(select(AuditLog).where(AuditLog.entity == result_id))
        assert log.action == "source_conflict.review"
        assert log.payload["note"] == body["note"]
        article = await db.get(KBArticle, ids[0])
        article.status = "archived"
        await db.commit()
    assert client.patch(review_url, json=body, headers=admin_headers).status_code == 409
    current = client.get(f"{ROOT}/results/{result_id}", headers=operator_headers)
    assert current.json()["stale"] is True
    assert client.get(f"{ROOT}/results", params={"article_id": ids[0]}, headers=operator_headers).json() == []


async def test_admin_reviews_a_conflict_group(client, admin_headers, operator_headers, sources):
    ids, result_id = sources
    assert client.get(f"{ROOT}/groups").status_code == 401
    response = client.get(f"{ROOT}/groups", headers=operator_headers)
    assert response.status_code == 200
    group = response.json()[0]
    assert group["result_ids"] == [result_id]
    assert {item["source"]["article_id"] for item in group["sources"]} == set(ids)
    url = f"{ROOT}/groups/{group['id']}/review"
    body = {"status": "confirmed", "preferred_article_id": ids[1], "note": ""}
    assert client.patch(url, json=body, headers=operator_headers).status_code == 403
    reviewed = client.patch(url, json=body, headers=admin_headers)
    assert reviewed.status_code == 200
    assert reviewed.json()["review_status"] == "confirmed"
    assert reviewed.json()["preferred_article_id"] == ids[1]
    assert client.get(f"{ROOT}/groups", params={"reviewed": False}, headers=operator_headers).json() == []
    assert len(client.get(f"{ROOT}/groups", params={"reviewed": True}, headers=operator_headers).json()) == 1
    async with client.app.state.rt.db.session_factory() as db:
        log = await db.scalar(select(AuditLog).where(AuditLog.entity == group["id"]))
        assert log.action == "source_conflict.group_review"
        assert log.payload["result_ids"] == [result_id]


async def test_start_requires_admin_and_valid_limits(client, admin_headers, operator_headers, sources):
    ids, _ = sources
    body = {"article_ids": ids}
    assert client.post(f"{ROOT}/jobs", json=body).status_code == 401
    assert client.post(f"{ROOT}/jobs", json=body, headers=operator_headers).status_code == 403
    assert (
        client.post(f"{ROOT}/jobs", json={"article_ids": [ids[0]]}, headers=admin_headers).status_code == 422
    )
    response = client.post(f"{ROOT}/jobs", json=body, headers=admin_headers)
    assert response.status_code == 503
    assert "LLM" in response.json()["detail"]


async def test_audit_is_queued_and_second_process_task_is_bounded(
    client,
    admin_headers,
    sources,
    monkeypatch,
):
    import asyncio

    from app.services import source_conflict_runner

    ids, _ = sources
    release = threading.Event()

    async def assess(left, right, **kwargs):
        await asyncio.to_thread(release.wait, 5)
        return PairAssessment(
            same_fact="yes",
            conditions="overlap",
            claims="incompatible",
            material="yes",
            left_quote=left.answer,
            right_quote=right.answer,
            fact="Лимит",
            explanation="Лимиты",
        )

    detector = SimpleNamespace(version="api-model-fixture", assess=assess, aclose=AsyncMock())
    monkeypatch.setattr(source_conflict_runner, "create_conflict_detector", lambda dyn: detector)
    body = {"article_ids": ids, "limits": {"max_calls": 1, "timeout_seconds": 5}}
    try:
        started = client.post(f"{ROOT}/jobs", json=body, headers=admin_headers)
        assert started.status_code == 202
        job_id = started.json()["id"]
        shown = client.get(f"{ROOT}/jobs/{job_id}", headers=admin_headers)
        assert shown.json()["status"] in {"pending", "running"}
        assert client.post(f"{ROOT}/jobs", json=body, headers=admin_headers).status_code == 409
    finally:
        release.set()
    for _ in range(100):
        shown = client.get(f"{ROOT}/jobs/{job_id}", headers=admin_headers)
        if shown.json()["status"] == "done":
            break
        await asyncio.sleep(0.01)
    assert shown.json()["status"] == "done"
    assert shown.json()["report"]["attempted_calls"] == 1
    listed = client.get(f"{ROOT}/jobs", headers=admin_headers).json()
    assert any(job["id"] == job_id for job in listed)
    detector.aclose.assert_awaited_once()


async def test_failed_audit_is_explicitly_resumed_with_saved_budget(
    client,
    admin_headers,
    sources,
    monkeypatch,
):
    import asyncio

    import httpx

    from app.services import source_conflict_runner

    ids, _ = sources
    calls = []

    async def assess(left, right, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("credentials-must-not-be-returned")
        return PairAssessment(
            same_fact="yes",
            conditions="overlap",
            claims="incompatible",
            material="yes",
            left_quote=left.answer,
            right_quote=right.answer,
            fact="Лимит",
            explanation="Лимит",
        )

    def detector_factory(dyn):
        return SimpleNamespace(version="resume-api-fixture", assess=assess, aclose=AsyncMock())

    monkeypatch.setattr(source_conflict_runner, "create_conflict_detector", detector_factory)
    body = {"article_ids": ids, "limits": {"max_calls": 2, "timeout_seconds": 5}}
    started = client.post(f"{ROOT}/jobs", json=body, headers=admin_headers)
    assert started.status_code == 202
    job_id = started.json()["id"]
    for _ in range(100):
        shown = client.get(f"{ROOT}/jobs/{job_id}", headers=admin_headers)
        if shown.json()["status"] == "retryable":
            break
        await asyncio.sleep(0.01)
    assert shown.json()["status"] == "retryable"
    assert "credentials" not in shown.text
    assert shown.json()["report"]["attempted_calls"] == 1
    resumed = client.post(f"{ROOT}/jobs/{job_id}/resume", headers=admin_headers)
    assert resumed.status_code == 202
    for _ in range(100):
        shown = client.get(f"{ROOT}/jobs/{job_id}", headers=admin_headers)
        if shown.json()["status"] == "done":
            break
        await asyncio.sleep(0.01)
    assert shown.json()["status"] == "done"
    assert shown.json()["report"]["attempted_calls"] == 2
    assert len(calls) == 2
    assert client.post(f"{ROOT}/jobs/{job_id}/resume", headers=admin_headers).status_code == 409


async def test_resume_rejects_another_process_live_lease(client, admin_headers, sources, monkeypatch):
    from app.schemas.source_conflicts import AuditLimits
    from app.services import source_conflict_runner
    from app.services.source_conflict_audit import SourceConflictAuditor
    from app.services.source_conflict_candidates import SourceConflictCandidateFinder

    ids, _ = sources
    rt = client.app.state.rt
    detector = SimpleNamespace(version="external-worker-model", assess=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(source_conflict_runner, "create_conflict_detector", lambda dyn: detector)
    auditor = SourceConflictAuditor(
        rt.source_conflict_runner.registry, SourceConflictCandidateFinder(rt.embedder), detector
    )
    limits = AuditLimits()
    job = await rt.source_conflict_runner.registry.create_job(
        {"article_ids": sorted(ids)}, limits, auditor.processing_version(limits), "admin"
    )
    assert await rt.source_conflict_runner.registry.claim_job(job.id, "other-process")
    response = client.post(f"{ROOT}/jobs/{job.id}/resume", headers=admin_headers)
    assert response.status_code == 409
    detector.assess.assert_not_awaited()


def test_automation_status_exposes_configuration_and_queue_to_staff(client, admin_headers):
    response = client.get("/api/source-conflicts/automation", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is False
    assert data["blocked_reason"] == "model_not_configured"
    assert data["calls_used_today"] == 0
    assert data["estimated_backfill_calls"] >= 0
    assert "entries" in data and "counts" in data


def test_automatic_backfill_enqueues_without_paid_work_and_is_idempotent(client, admin_headers):
    first = client.post(
        "/api/source-conflicts/automation/backfill", headers=admin_headers, json={"all": True}
    )
    second = client.post(
        "/api/source-conflicts/automation/backfill", headers=admin_headers, json={"all": True}
    )
    assert first.status_code == second.status_code == 202
    assert second.json()["enqueued"] == 0
    state = client.get("/api/source-conflicts/automation", headers=admin_headers).json()
    assert state["calls_used_today"] == 0


def test_automatic_controls_require_admin(client, operator_headers):
    assert client.get("/api/source-conflicts/automation", headers=operator_headers).status_code == 200
    assert (
        client.post(
            "/api/source-conflicts/automation/backfill", headers=operator_headers, json={"all": True}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/source-conflicts/automation/missing/retry", headers=operator_headers).status_code
        == 403
    )
