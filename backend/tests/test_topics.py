"""Темы обращений: кластеризация, устойчивый рост, маскирование ПДн."""

from datetime import UTC, datetime, timedelta

from app.models import ChatSession, Message
from app.services import topics


def test_cluster_groups_the_same_and_separates_the_different():
    vectors = [
        [1.0, 0.0],
        [0.99, 0.14],  # одна тема
        [0.0, 1.0],
        [0.14, 0.99],  # другая
    ]
    groups = topics.cluster([topics._normalize(v) for v in vectors], 0.9)
    assert sorted(sorted(g) for g in groups) == [[0, 1], [2, 3]]


def test_cluster_threshold_controls_granularity():
    """Порог — единственная ручка: ниже темы слипаются, выше дробятся."""
    vectors = [topics._normalize(v) for v in ([1.0, 0.0], [0.7, 0.7])]
    assert len(topics.cluster(vectors, 0.5)) == 1
    assert len(topics.cluster(vectors, 0.9)) == 2


def test_growth_does_not_let_tiny_topics_top_the_chart():
    """Скачок с одного обращения на три не должен выглядеть рекордом."""
    assert topics._growth(2, 0) < 4.0, "рост из нуля обязан быть конечным"
    assert topics._growth(3, 1) < topics._growth(30, 10)


async def _ask(rt, texts: list[str], *, days_ago: int = 0) -> None:
    moment = datetime.now(UTC) - timedelta(days=days_ago, hours=1)
    async with rt.db.session_factory() as db:
        session = ChatSession(status="active", channel="web", created_at=moment)
        db.add(session)
        await db.flush()
        for text in texts:
            db.add(Message(session_id=session.id, role="user", content=text, created_at=moment))
        await db.commit()


async def test_report_groups_reformulations_of_one_problem(client):
    rt = client.app.state.rt
    await _ask(rt, ["Как подписать контракт электронной подписью"] * 4)
    await _ask(rt, ["Как выгрузить УПД из личного кабинета"] * 3)

    report = await topics.build_report(rt, days=7)

    labels = {t["label"]: t["count"] for t in report["topics"]}
    assert any("подписать контракт" in label for label in labels)
    assert any("выгрузить УПД" in label for label in labels)
    assert max(labels.values()) >= 3


async def test_single_mention_is_not_a_topic(client):
    """Одиночная реплика в отчёте — это чужое сообщение, показанное персоналу."""
    rt = client.app.state.rt
    await _ask(rt, ["Совершенно уникальный вопрос про ржаной хлеб и телескопы"])

    report = await topics.build_report(rt, days=7)

    assert not any("телескоп" in t["label"] for t in report["topics"])


async def test_personal_data_is_masked_in_examples(client):
    rt = client.app.state.rt
    await _ask(rt, ["Мой телефон +7 916 123-45-67, не приходит СМС на портале"] * 3)

    report = await topics.build_report(rt, days=7)

    printed = " ".join(t["label"] + " ".join(t["examples"]) for t in report["topics"])
    assert "916" not in printed, "телефон обязан быть замаскирован"


async def test_growth_compares_with_the_previous_window(client):
    """Тема из прошлого периода не считается новой, а её рост виден числом."""
    rt = client.app.state.rt
    await _ask(rt, ["Не открывается котировочная сессия после обновления"] * 2, days_ago=9)
    await _ask(rt, ["Не открывается котировочная сессия после обновления"] * 5)

    report = await topics.build_report(rt, days=7)
    topic = next(t for t in report["topics"] if "котировочная сессия" in t["label"])

    assert topic["count"] == 5
    assert topic["previous"] == 2, "прошлый период должен раскладываться по тем же темам"
    assert topic["delta"] == 3


def test_endpoint_requires_staff_and_returns_shape(client, operator_headers):
    assert client.get("/api/admin/topics").status_code in (401, 403)

    body = client.get("/api/admin/topics?days=7", headers=operator_headers).json()
    assert set(body) == {"report", "stale", "computing"}


def test_refresh_is_admin_only(client, operator_headers):
    assert client.post("/api/admin/topics/refresh").status_code in (401, 403)
    assert client.post("/api/admin/topics/refresh", headers=operator_headers).status_code in (401, 403)


def test_kill_switch(client, admin_headers, operator_headers):
    client.put("/api/admin/settings", headers=admin_headers, json={"updates": {"topics.enabled": False}})
    try:
        assert client.get("/api/admin/topics", headers=operator_headers).status_code == 409
    finally:
        client.put("/api/admin/settings", headers=admin_headers, json={"updates": {"topics.enabled": True}})
