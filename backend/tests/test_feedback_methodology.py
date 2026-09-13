"""Методика на живом приложении: два вопроса после обращения, отчёт и админка.

Демо-данные сидера устроены так, чтобы методика показывала различимую картину:
слабый оператор (op_sergey) объясняет непонятно и не решает вопрос, тема
«Электронная подпись» у бота решается хуже других, негатив растёт к прошлой
неделе. Отчёт обязан это увидеть — иначе методика не работает.
"""

import sqlite3

from sqlalchemy import inspect, select

from app.db import Database
from app.models import AnalyticsReport, Feedback
from app.seed.seeder import HARD_TOPIC, WEAK_OPERATOR
from app.services.feedback import build_feedback_report
from app.services.feedback_admin import BUNDLED_SUPPORT_EXPORT


def _session(client) -> str:
    return client.post("/api/chat/sessions").json()["session_id"]


async def _feedback_of(client, session_id):
    async with client.app.state.rt.db.session_factory() as db:
        return (await db.execute(select(Feedback).where(Feedback.session_id == session_id))).scalars().one()


async def test_feedback_keeps_answers_to_both_questions(client):
    sid = _session(client)
    resp = client.post(
        f"/api/chat/sessions/{sid}/feedback",
        json={"rating": 3, "comment": "Решили, но объяснили сложно", "solved": True, "clear": False},
    )
    assert resp.status_code == 200
    row = await _feedback_of(client, sid)
    assert (row.rating, row.solved, row.clear) == (3, True, False)


async def test_old_client_without_answers_still_works(client):
    sid = _session(client)
    assert (
        client.post(f"/api/chat/sessions/{sid}/feedback", json={"rating": 5, "comment": ""}).status_code
        == 200
    )
    row = await _feedback_of(client, sid)
    assert row.solved is None and row.clear is None


def test_feedback_answers_must_be_yes_or_no(client):
    sid = _session(client)
    resp = client.post(f"/api/chat/sessions/{sid}/feedback", json={"rating": 4, "solved": "наверное"})
    assert resp.status_code == 422


async def test_report_names_the_weak_operator_by_dimension(client):
    report = await build_feedback_report(client.app.state.rt)
    specialists = {s["id"]: s for s in report["specialists"]}
    weak = specialists[WEAK_OPERATOR]
    dimensions = {spot["dimension"] for spot in weak["weak_spots"]}
    # Не «плохой оператор», а что именно не так
    assert {"clear", "solved"} <= dimensions
    assert "speed" in dimensions and "abandoned" in dimensions
    assert weak["abandoned"] == 2
    others = [s for s in report["specialists"] if s["kind"] == "operator" and s["id"] != WEAK_OPERATOR]
    assert others and all(weak["index"] < s["index"] for s in others)
    assert report["specialists"][0]["id"] == WEAK_OPERATOR
    assert "Сергей Ефимов — " in report["conclusion"] and "объясняет непонятно" in report["conclusion"]


async def test_report_scores_ai_as_a_specialist(client):
    report = await build_feedback_report(client.app.state.rt)
    ai = next(s for s in report["specialists"] if s["kind"] == "ai")
    assert ai["auto_resolved_rate"] is not None and ai["auto_resolved_rate"] > 0.5
    assert ai["refusal_rate"] is not None and ai["refusal_rate"] > 0
    assert ai["thumbs_up"] > 0 and ai["thumbs_down"] > 0
    assert ai["index"] is not None and 0 <= ai["index"] <= 100
    assert set(ai["components"]) == {"solved", "clear", "csat", "speed"}


async def test_report_finds_systemic_problems(client):
    report = await build_feedback_report(client.app.state.rt)
    assert report["summary"]["solved_rate"] is not None and report["summary"]["clear_rate"] is not None
    hard = next(t for t in report["topics"] if t["topic"] == HARD_TOPIC)
    assert hard["unsolved_share"] > report["topics"][-1]["unsolved_share"] or hard["flagged"]
    assert report["trend"]["growing"] is True
    kinds = {p["kind"] for p in report["problems"]}
    assert {"specialist", "trend"} <= kinds
    assert report["recommendations"]
    assert "Что сделать:" in report["conclusion"]
    assert report["methodology"]["weights"] == {"solved": 40.0, "clear": 30.0, "csat": 20.0, "speed": 10.0}


async def test_report_shows_result_and_clarity_apart(client):
    report = await build_feedback_report(client.app.state.rt)
    weak = next(s for s in report["specialists"] if s["id"] == WEAK_OPERATOR)
    # Слабый оператор объясняет ещё хуже, чем решает: это видно двумя числами
    assert weak["clarity_index"] < weak["result_index"]
    ai = next(s for s in report["specialists"] if s["kind"] == "ai")
    # демо-👎 с причиной «Непонятно» доходят до понятности ИИ
    assert 0 < ai["thumbs_unclear"] <= ai["thumbs_down"]
    assert "Результат работы:" in report["conclusion"] and "Качество донесения:" in report["conclusion"]
    assert set(report["methodology"]["baseline"]) == {"solved", "clear", "csat"}


async def test_every_advice_says_whom_it_is_for(client):
    report = await build_feedback_report(client.app.state.rt)
    specialist = next(p for p in report["problems"] if p["kind"] == "specialist")
    assert specialist["target"] == "Сергей Ефимов"
    assert all(p["target"] for p in report["problems"] if p["kind"] != "trend")
    advice = report["conclusion"].split("Что сделать:\n", 1)[1].splitlines()
    assert advice[0].startswith("— Сергей Ефимов: ")
    # одному адресату — одна строка
    addressees = [line.split(":", 1)[0] for line in advice]
    assert len(addressees) == len(set(addressees))


async def test_index_weights_come_from_settings(client):
    rt = client.app.state.rt
    await rt.dyn.set_many({"feedback.index_weights": {"solved": 0, "clear": 0, "csat": 100, "speed": 0}})
    try:
        report = await build_feedback_report(rt)
    finally:
        await rt.dyn.set_many(
            {"feedback.index_weights": {"solved": 40, "clear": 30, "csat": 20, "speed": 10}}
        )
    for s in report["specialists"]:
        assert s["index"] == round(100 * s["components"]["csat"])


def test_admin_report_exposes_methodology(client, admin_headers):
    assert client.post("/api/admin/feedback/report", headers=admin_headers).status_code == 200
    report = client.get("/api/admin/feedback/report", headers=admin_headers).json()
    first = report["specialists"][0]
    assert first["index"] is not None and first["weak_spots"]
    assert report["topics"] and report["problems"] and report["methodology"]["min_reviews"] == 5
    assert "growing" in report["trend"]


def test_admin_report_includes_support_export(client, admin_headers):
    report = client.get("/api/admin/feedback/report", headers=admin_headers).json()
    assert BUNDLED_SUPPORT_EXPORT.exists(), "к поставке приложен отчёт по реальной выгрузке СТП"
    export = report["support_export"]
    assert export["total"] > 20000
    assert export["topics"] and export["problems"] and export["conclusion"]


async def test_fresh_support_export_report_wins_over_bundled(client, admin_headers):
    async with client.app.state.rt.db.session_factory() as db:
        db.add(
            AnalyticsReport(
                kind="support_export_quality",
                payload={"generated_at": "2026-09-12T00:00:00+00:00", "total": 7, "conclusion": "свежий"},
            )
        )
        await db.commit()
    report = client.get("/api/admin/feedback/report", headers=admin_headers).json()
    assert report["support_export"]["total"] == 7
    async with client.app.state.rt.db.session_factory() as db:
        for row in (
            await db.execute(select(AnalyticsReport).where(AnalyticsReport.kind == "support_export_quality"))
        ).scalars():
            await db.delete(row)
        await db.commit()


async def test_unreadable_fresh_support_export_falls_back_to_bundled(client, admin_headers):
    async with client.app.state.rt.db.session_factory() as db:
        # Отчёт чужой версии: без числа обращений он не разбирается
        db.add(
            AnalyticsReport(
                kind="support_export_quality", payload={"generated_at": "2026-09-12T00:00:00+00:00"}
            )
        )
        await db.commit()
    try:
        report = client.get("/api/admin/feedback/report", headers=admin_headers).json()
    finally:
        async with client.app.state.rt.db.session_factory() as db:
            for row in (
                await db.execute(
                    select(AnalyticsReport).where(AnalyticsReport.kind == "support_export_quality")
                )
            ).scalars():
                await db.delete(row)
            await db.commit()
    # страница не остаётся без реальных данных
    assert report["support_export"]["total"] > 20000
    assert "Что сделать: написать статьи" in report["support_export"]["conclusion"]


async def test_report_of_previous_version_still_renders(client, admin_headers):
    async with client.app.state.rt.db.session_factory() as db:
        db.add(
            AnalyticsReport(
                kind="feedback_summary",
                payload={
                    "summary": {"count": 1, "avg_rating": 5.0, "positive_share": 1.0},
                    "specialists": [
                        {
                            "id": "ai",
                            "name": "ИИ-ассистент",
                            "kind": "ai",
                            "count": 1,
                            "avg_rating": 5.0,
                            "negative_share": 0.0,
                        }
                    ],
                    "clusters": [],
                    "conclusion": "старый отчёт",
                },
            )
        )
        await db.commit()
    resp = client.get("/api/admin/feedback/report", headers=admin_headers)
    assert resp.status_code == 200
    report = resp.json()
    assert report["conclusion"] == "старый отчёт"
    # Полей, которых старый отчёт не знал, в ответе нет вовсе — ни в корне, ни у специалиста
    assert "topics" not in report and "index" not in report["specialists"][0]


def test_feedback_list_shows_answers(client, admin_headers):
    rows = client.get("/api/admin/feedback", headers=admin_headers).json()
    assert any(r["solved"] is not None for r in rows)
    assert all("clear" in r for r in rows)


def test_dashboard_quality_has_both_questions(client, admin_headers):
    quality = client.get("/api/admin/stats", headers=admin_headers).json()["quality"]
    assert 0 < quality["solved_rate"] < 1
    assert 0 < quality["clear_rate"] < 1
    assert 0 < quality["csat"] < 1


async def test_new_columns_are_added_to_an_existing_table(tmp_path):
    """Стенд, развёрнутый до этих вопросов: таблица отзывов без двух колонок."""
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE feedback (id VARCHAR(32) PRIMARY KEY, session_id VARCHAR(32), "
            "specialist_kind VARCHAR(16), operator_id VARCHAR(32), rating INTEGER, comment TEXT, "
            "sentiment FLOAT, created_at DATETIME)"
        )
        conn.execute("INSERT INTO feedback (id, session_id, rating, comment) VALUES ('f1', 's1', 4, '')")
    db = Database(f"sqlite+aiosqlite:///{path}")
    try:
        await db.create_all()
        await db.create_all()  # второй запуск ничего не ломает
        async with db.engine.connect() as conn:
            columns = await conn.run_sync(
                lambda c: {col["name"] for col in inspect(c).get_columns("feedback")}
            )
        async with db.session_factory() as session:
            row = await session.get(Feedback, "f1")
    finally:
        await db.dispose()
    assert {"solved", "clear"} <= columns
    assert row is not None and row.solved is None and row.clear is None
