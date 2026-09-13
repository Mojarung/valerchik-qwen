"""Conflict evidence reaches the user before either answer path begins streaming."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.chat import source_conflicts
from app.models import KBArticle
from app.models_conflicts import SourceConflict
from app.schemas.source_conflicts import PairAssessment
from app.services.chat import ChatService
from app.services.retrieval import RetrievedDoc
from app.services.source_conflicts import SourceConflictRegistry

QUESTION = "Какой максимальный размер файла?"
LEFT = "Максимальный размер файла — 10 МБ."
RIGHT = "Максимальный размер файла — 20 МБ."


async def seed_conflict(
    client, *, role="all", review="confirmed", archived=False, preferred_index: int | None = None
):
    factory = client.app.state.rt.db.session_factory
    ids = [uuid.uuid4().hex, uuid.uuid4().hex]
    async with factory() as db:
        db.add_all(
            [
                KBArticle(
                    id=ids[0],
                    question=QUESTION,
                    answer=LEFT,
                    category="Файлы",
                    audience=role,
                    origin={"authored": True, "file": "PRIVATE_PROVENANCE"},
                ),
                KBArticle(id=ids[1], question=QUESTION, answer=RIGHT, category="Файлы", audience=role),
            ]
        )
        await db.commit()
    registry = SourceConflictRegistry(factory)
    snapshots = await registry.snapshot_articles(ids)
    assessment = PairAssessment(
        same_fact="yes",
        conditions="overlap",
        claims="incompatible",
        material="yes",
        fact="Максимальный размер файла",
        left_quote=LEFT,
        right_quote=RIGHT,
        explanation="Разные ограничения",
        conditions_text="Одна форма загрузки",
    )
    conflict = await registry.record_pair(*snapshots, assessment, "fixture")
    async with factory() as db:
        row = await db.get(SourceConflict, conflict.id)
        row.review_status = review
        row.preferred_article_id = ids[preferred_index] if preferred_index is not None else None
        if archived:
            article = await db.get(KBArticle, ids[1])
            article.status = "archived"
        await db.commit()
    return ids


def configure_chat(client, monkeypatch, ids, *, agent=False, early=False):
    from app.services.agent import AgentOutcome

    docs = [
        RetrievedDoc(article_id=ids[0], question=QUESTION, answer=LEFT, category="Файлы", line=1, score=0.95)
    ]
    monkeypatch.setattr(ChatService, "_probe_kb", AsyncMock(return_value=docs))
    monkeypatch.setattr(ChatService, "_need_external_tools", AsyncMock(return_value=False))
    if not early:
        monkeypatch.setattr(source_conflicts, "maybe_ambiguous_conflict", AsyncMock(return_value=None))
    if agent:
        monkeypatch.setattr("app.services.verbatim.pick_verbatim", lambda *args: None)
    outcome = AgentOutcome(
        answer=LEFT,
        answered=True,
        confidence=0.95,
        citations=[{"article_id": ids[0], "question": QUESTION, "score": 0.95}],
        escalated=False,
        line=1,
        tool_trace=[],
    )
    agent_mock = AsyncMock(return_value=outcome)
    monkeypatch.setattr("app.services.chat.run_support_agent", agent_mock)
    stream = AsyncMock()
    monkeypatch.setattr(ChatService, "_stream_answer", stream)
    return agent_mock, stream


def ask(client, question=QUESTION, audience="supplier"):
    sid = client.post("/api/chat/sessions").json()["session_id"]
    response = client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": question, "audience": audience}
    )
    assert response.status_code == 200, response.text
    history = client.get(f"/api/chat/sessions/{sid}/messages").json()
    return response.json(), history[-1]


@pytest.mark.parametrize("agent", [False, True])
def test_verbatim_and_agent_conflicts_are_replaced_before_stream(client, monkeypatch, agent):
    ids = client.portal.call(seed_conflict, client)
    agent_mock, stream = configure_chat(client, monkeypatch, ids, agent=agent)
    response, saved = ask(client)
    assert response["status"] == "no_answer"
    assert "10 МБ" in response["answer"] and "20 МБ" in response["answer"]
    assert "оператора" in response["answer"]
    assert {c["article_id"] for c in response["citations"]} == set(ids)
    assert "PRIVATE_PROVENANCE" not in response["answer"]
    assert saved["meta"]["retrieval_outcome"] == "source_conflict"
    assert saved["meta"]["answered"] is False
    assert saved["meta"]["offer_operator"] is True
    assert len(stream.await_args_list) == 1
    assert stream.await_args.args[1] == response["answer"]
    assert agent_mock.await_count == 1


@pytest.mark.parametrize(
    "review,archived,role",
    [
        ("unreviewed", False, "all"),
        ("dismissed", False, "all"),
        ("confirmed", True, "all"),
        ("confirmed", False, "customer"),
    ],
)
def test_dismissed_stale_and_wrong_role_never_block(client, monkeypatch, review, archived, role):
    async def seed():
        return await seed_conflict(client, role=role, review=review, archived=archived)

    ids = client.portal.call(seed)
    configure_chat(client, monkeypatch, ids)
    response, saved = ask(client)
    assert response["status"] == "answered"
    assert response["answer"] == LEFT
    assert saved["meta"].get("retrieval_outcome") != "source_conflict"


def test_clear_fact_question_is_intercepted_before_topic_clarification(client, monkeypatch):
    ids = client.portal.call(seed_conflict, client)
    agent_mock, _ = configure_chat(client, monkeypatch, ids, early=True)
    clarify = AsyncMock(side_effect=AssertionError("Conflict must precede topic clarification"))
    monkeypatch.setattr("app.chat.steps._maybe_clarify", clarify)
    response, _ = ask(client)
    assert response["status"] == "no_answer"
    agent_mock.assert_not_awaited()


def test_unrelated_question_in_same_source_does_not_block(client, monkeypatch):
    ids = client.portal.call(seed_conflict, client)
    configure_chat(client, monkeypatch, ids, agent=True)
    response, _ = ask(client, "Как удалить учётную запись?")
    assert response["status"] == "answered"


def test_confirmed_warning_offers_operator_and_does_not_create_knowledge_gap(client, monkeypatch):
    async def seed():
        return await seed_conflict(client, review="confirmed")

    ids = client.portal.call(seed)
    configure_chat(client, monkeypatch, ids)
    gap = AsyncMock()
    monkeypatch.setattr(ChatService, "_record_gap", gap)
    sid = client.post("/api/chat/sessions").json()["session_id"]
    response = client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": QUESTION, "audience": "supplier"}
    )
    assert response.json()["status"] == "no_answer"
    gap.assert_not_awaited()
    accepted = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Да, позовите оператора"})
    assert accepted.status_code == 200
    assert accepted.json()["escalated"] is True


def test_admin_preferred_source_replaces_conflicting_answer_without_model_call(client, monkeypatch):
    async def seed():
        return await seed_conflict(client, preferred_index=1)

    ids = client.portal.call(seed)
    agent_mock, stream = configure_chat(client, monkeypatch, ids, agent=True, early=True)
    response, saved = ask(client)
    assert response["status"] == "answered"
    assert response["answer"] == RIGHT
    assert len(response["citations"]) == 1
    assert response["citations"][0]["article_id"] == ids[1]
    assert response["citations"][0]["question"] == QUESTION
    assert response["citations"][0]["score"] == 1.0
    assert saved["meta"]["retrieval_outcome"] == "source_conflict_resolved"
    assert saved["meta"]["preferred_article_id"] == ids[1]
    assert saved["meta"]["offer_operator"] is False
    assert stream.await_args.args[1] == RIGHT
    agent_mock.assert_not_awaited()


def test_group_choice_can_use_a_third_article_without_model_call(client, monkeypatch):
    third = "Максимальный размер файла — 30 МБ."

    async def seed():
        from app.schemas.source_conflicts import ConflictReview

        factory = client.app.state.rt.db.session_factory
        ids = [uuid.uuid4().hex for _ in range(3)]
        async with factory() as db:
            db.add_all(
                [
                    KBArticle(id=ids[0], question=QUESTION, answer=LEFT, category="Файлы"),
                    KBArticle(id=ids[1], question=QUESTION, answer=RIGHT, category="Файлы"),
                    KBArticle(id=ids[2], question=QUESTION, answer=third, category="Файлы"),
                ]
            )
            await db.commit()
        registry = SourceConflictRegistry(factory)
        left, middle, selected = await registry.snapshot_articles(ids)
        await registry.record_pair(
            left,
            middle,
            PairAssessment(
                same_fact="yes",
                conditions="overlap",
                claims="incompatible",
                material="yes",
                fact="Максимальный размер файла",
                left_quote=LEFT,
                right_quote=RIGHT,
                explanation="Разные ограничения",
            ),
            "fixture",
        )
        await registry.record_pair(
            middle,
            selected,
            PairAssessment(
                same_fact="yes",
                conditions="overlap",
                claims="incompatible",
                material="yes",
                fact="Лимит загружаемого файла",
                left_quote=RIGHT,
                right_quote=third,
                explanation="Разные ограничения",
            ),
            "fixture",
        )
        group = (await registry.list_groups())[0]
        await registry.review_group(
            group.id,
            ConflictReview(status="confirmed", preferred_article_id=ids[2]),
            "admin",
        )
        return ids

    ids = client.portal.call(seed)
    agent_mock, stream = configure_chat(client, monkeypatch, ids, agent=True, early=True)
    response, saved = ask(client)
    assert response["status"] == "answered"
    assert response["answer"] == third
    assert response["citations"][0]["article_id"] == ids[2]
    assert saved["meta"]["preferred_article_id"] == ids[2]
    assert stream.await_args.args[1] == third
    agent_mock.assert_not_awaited()


def test_uncited_conflicting_retrieval_result_does_not_override_agent(client, monkeypatch):
    from app.services.agent import AgentOutcome

    ids = client.portal.call(seed_conflict, client)
    configure_chat(client, monkeypatch, ids, agent=True)
    unrelated_id = uuid.uuid4().hex

    async def seed_unrelated():
        async with client.app.state.rt.db.session_factory() as db:
            db.add(
                KBArticle(
                    id=unrelated_id,
                    question="Как удалить профиль?",
                    answer="Нажмите Удалить.",
                    category="Профиль",
                )
            )
            await db.commit()

    client.portal.call(seed_unrelated)
    outcome = AgentOutcome(
        answer="Нажмите Удалить.",
        answered=True,
        confidence=0.95,
        citations=[{"article_id": unrelated_id, "question": "Как удалить профиль?", "score": 0.95}],
        escalated=False,
        line=1,
        tool_trace=[],
    )
    monkeypatch.setattr("app.services.chat.run_support_agent", AsyncMock(return_value=outcome))
    response, saved = ask(client)
    assert response["answer"] == "Нажмите Удалить."
    assert saved["meta"].get("retrieval_outcome") != "source_conflict"


@pytest.mark.parametrize("fact", ["Максимальный срок действия оферты", "Maximum offer validity period"])
def test_offer_extension_question_matches_duration_fact_across_russian_forms(fact):
    from datetime import UTC, datetime

    from app.schemas.source_conflicts import ConflictResult
    from tests.test_source_conflict_detector import snapshot

    assessment = PairAssessment(
        same_fact="yes",
        conditions="overlap",
        claims="incompatible",
        material="yes",
        fact=fact,
        left_quote="Максимальный срок действия оферты — 12 месяцев",
        right_quote="Максимальный срок действия оферты — 3 месяца",
        explanation="Разные сроки",
    )
    result = ConflictResult(
        id="pair",
        left=snapshot(answer="Срок оферты — 12 месяцев"),
        right=snapshot("b", "Срок оферты — 3 месяца"),
        assessment=assessment,
        outcome="conflict",
        processing_version="v1",
        job_id=None,
        stale=False,
        created_at=datetime.now(UTC),
    )
    assert source_conflicts.applicable(result, "Как продлить оферту?", "Срок оферты — 12 месяцев", {"a"})
    assert not source_conflicts.applicable(result, "Как оформить МЧД?", "Срок оферты — 12 месяцев", {"a"})


def test_same_number_with_different_units_is_not_conflict_evidence():
    from datetime import UTC, datetime

    from app.schemas.source_conflicts import ConflictResult
    from tests.test_source_conflict_detector import snapshot

    assessment = PairAssessment(
        same_fact="yes",
        conditions="overlap",
        claims="incompatible",
        material="yes",
        fact="Максимальный срок действия оферты",
        left_quote="12 месяцев",
        right_quote="3 месяца",
        explanation="Разные сроки",
    )
    result = ConflictResult(
        id="pair",
        left=snapshot(answer="Срок оферты — 12 месяцев. Продление за 12 дней."),
        right=snapshot("b", "Срок оферты — 3 месяца"),
        assessment=assessment,
        outcome="conflict",
        processing_version="v1",
        job_id=None,
        stale=False,
        created_at=datetime.now(UTC),
    )
    assert not source_conflicts.applicable(
        result,
        "За сколько дней до окончания можно продлить оферту?",
        "Продление доступно за 12 дней до окончания.",
        {"a"},
    )
