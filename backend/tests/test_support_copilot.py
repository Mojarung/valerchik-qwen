"""Deterministic copilot safety and concurrency checks, independent of cloud scoring."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic_ai import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from redis.exceptions import ConnectionError as RedisConnectionError

from app.models import CopilotRun
from app.services import copilot, support_copilot
from app.services.retrieval import RetrievedDoc


class Sources:
    async def search(self, question, **kwargs):
        return [RetrievedDoc("manual-1", "Инструкция", "Проверьте браузер.", "Помощь", 1)]


def model(monkeypatch, content):
    monkeypatch.setattr(copilot, "llm_configured", lambda _: True)
    monkeypatch.setattr(
        copilot,
        "build_model",
        lambda _: FunctionModel(
            lambda messages, info: ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, content)])
        ),
    )


@pytest.mark.parametrize(
    "content,error",
    [
        ({"draft": "Выполните действие", "mode": "grounded", "source_ids": ["invented"]}, "неизвестный"),
        ({"draft": "Выполните действие", "mode": "grounded"}, "не подтверждён"),
    ],
)
async def test_unknown_or_absent_evidence_never_becomes_draft(client, monkeypatch, content, error):
    model(monkeypatch, content)
    draft = await copilot.draft_reply(client.app.state.rt.dyn, Sources(), [], "Как исправить?")
    assert draft.status == "error" and not draft.text
    assert error in draft.error
    assert draft.sources


async def test_short_citations_resolve_to_known_documents(client, monkeypatch):
    model(monkeypatch, {"draft": "Проверьте браузер.", "source_ids": ["S1"], "mode": "grounded"})
    draft = await copilot.draft_reply(client.app.state.rt.dyn, Sources(), [], "Как исправить?")
    assert draft.source_ids == ["manual-1"] and draft.generated


async def test_false_completed_action_is_not_insertable(client, monkeypatch):
    model(
        monkeypatch,
        {"draft": "Мы исправили ошибку, заявка отправлена.", "source_ids": ["S1"], "mode": "grounded"},
    )
    draft = await copilot.draft_reply(client.app.state.rt.dyn, Sources(), [], "Что с заявкой?")
    assert not draft.text and draft.warnings


async def test_search_failure_is_explicit(client):
    source = Sources()
    source.search = AsyncMock(side_effect=ConnectionError("search down"))
    draft = await copilot.draft_reply(client.app.state.rt.dyn, source, [], "Что проверить?")
    assert draft.status == "error" and not draft.text and "Поиск" in draft.error


async def test_timeout_preserves_sources(client, monkeypatch):
    model(monkeypatch, {})

    async def slow(*args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(copilot.Agent, "run", slow)
    client.app.state.rt.dyn.override("copilot.timeout_s", 0.01)
    try:
        draft = await copilot.draft_reply(client.app.state.rt.dyn, Sources(), [], "Что проверить?")
        assert draft.status == "error" and not draft.text and draft.sources
    finally:
        client.app.state.rt.dyn.override("copilot.timeout_s", 30)


async def test_singleflight_is_one_generation_with_persisted_diagnostics(client, monkeypatch):
    rt = client.app.state.rt
    sid = client.post("/api/chat/sessions").json()["session_id"]
    calls = 0

    async def generate(*args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return copilot.CopilotDraft(text="Уточните браузер", generated=True)

    monkeypatch.setattr(copilot, "draft_reply", generate)
    results = await asyncio.gather(*(support_copilot.generate(rt, sid, "Как исправить?") for _ in range(3)))
    assert calls == 1
    assert len({r.run_id for r in results}) == 1
    async with rt.db.session_factory() as db:
        run = await db.get(CopilotRun, results[0].run_id)
        assert {"prompt_version", "kb_version", "latency_ms", "model"} <= run.payload.keys()


async def test_late_generation_is_marked_stale(client, monkeypatch):
    rt = client.app.state.rt
    sid = client.post("/api/chat/sessions").json()["session_id"]

    async def generate(*args, **kwargs):
        await rt.chat._save_message(sid, "user", "Новые сведения")
        return copilot.CopilotDraft(text="Старая подсказка", generated=True)

    monkeypatch.setattr(copilot, "draft_reply", generate)
    result = await support_copilot.generate(rt, sid, "Как исправить?")
    assert result.status == "stale" and result.error


async def test_redis_failure_finishes_instead_of_spinning(client, monkeypatch):
    rt = client.app.state.rt
    sid = client.post("/api/chat/sessions").json()["session_id"]
    monkeypatch.setattr(rt.cache, "set", AsyncMock(side_effect=RedisConnectionError("offline")))
    result = await support_copilot.generate(rt, sid, "Как исправить?")
    assert result.status == "error" and result.error
