"""Интеграционный тест build_feedback_report: от отзывов в БД до готового отчёта.

В отличие от test_routing_and_feedback.py (сентимент/кластеризация в изоляции) и
test_feedback_keywords.py (формулировка заключения на синтетических данных), здесь
прогоняется весь конвейер на реальном приложении (client — SQLite + рабочий
HashEmbedder), с сидированными демо-отзывами вперемешку: отчёт не должен падать и
не должен терять данные, добавленные сверх сида.
"""

from sqlalchemy import select

from app.models import AnalyticsReport, ChatSession, Feedback, User
from app.security import hash_password
from app.services.feedback import build_feedback_report

COMPLAINT = "Очень долго ждал оператора, почти час, это ужасно."


async def test_build_feedback_report_scores_specialist_and_persists_report(client):
    rt = client.app.state.rt

    async with rt.db.session_factory() as db:
        operator = User(
            username="test_weak_operator_report",
            password_hash=hash_password("x"),
            role="operator",
            display_name="Тестовый Слабый Оператор",
            line=1,
        )
        db.add(operator)
        await db.flush()
        operator_id = operator.id

        for _ in range(3):
            session = ChatSession(operator_id=operator_id, line=1)
            db.add(session)
            await db.flush()
            db.add(
                Feedback(
                    session_id=session.id,
                    rating=1,
                    comment=COMPLAINT,
                    specialist_kind="operator",
                    operator_id=operator_id,
                )
            )
        await db.commit()

    report = await build_feedback_report(rt)

    # сводка не потеряла ни сидированные, ни только что добавленные отзывы
    assert report["summary"]["count"] >= 3
    assert report["summary"]["negative_count"] >= 3
    assert report["summary"]["operator_count"] >= 3

    specialist = next(s for s in report["specialists"] if s["id"] == operator_id)
    assert specialist["kind"] == "operator"
    assert specialist["name"] == "Тестовый Слабый Оператор"
    assert specialist["count"] == 3
    assert specialist["avg_rating"] == 1.0
    assert specialist["negative_share"] == 1.0
    assert specialist["sample_negative"] == COMPLAINT

    # specialists упорядочены от слабых к сильным по индексу качества; у нашего
    # три отзыва — индекс есть, но с пометкой «мало данных»
    indexes = [s["index"] for s in report["specialists"]]
    assert indexes == sorted(indexes)
    assert specialist["index"] is not None and specialist["low_data"] is True
    assert {spot["dimension"] for spot in specialist["weak_spots"]} == {"csat"}

    # три идентичных жалобы обязаны слиться в один кластер системной проблемы
    matching_clusters = [c for c in report["clusters"] if COMPLAINT in c["examples"]]
    assert matching_clusters, report["clusters"]
    assert matching_clusters[0]["size"] >= 3
    assert matching_clusters[0]["keywords"]

    assert report["conclusion"]
    assert "Тестовый Слабый Оператор" in report["conclusion"]

    async with rt.db.session_factory() as db:
        saved = (
            (
                await db.execute(
                    select(AnalyticsReport)
                    .where(AnalyticsReport.kind == "feedback_summary")
                    .order_by(AnalyticsReport.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
    assert saved is not None
    assert saved.payload["generated_at"] == report["generated_at"]
    assert saved.payload["summary"]["count"] == report["summary"]["count"]
