"""Статистика для админ-панели: сводка, разрез по линиям, динамика по дням."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.models import ChatSession, Feedback, KBArticle, KBGap, Message
from app.services.specialist_metrics import POSITIVE_MIN_RATING, share, tally

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.runtime import AppRuntime


def _yes_share(values: Iterable[bool | None]) -> float | None:
    answers = tally(values)
    return share(answers.yes, answers.total)


async def overview_stats(rt: AppRuntime) -> dict[str, Any]:
    async with rt.db.session_factory() as db:
        status_rows = (
            await db.execute(
                select(ChatSession.status, func.count(ChatSession.id)).group_by(ChatSession.status)
            )
        ).all()
        by_status = {status: count for status, count in status_rows}

        line_rows = (
            await db.execute(
                select(ChatSession.line, func.count(ChatSession.id))
                .where(ChatSession.escalated_at.is_not(None))
                .group_by(ChatSession.line)
            )
        ).all()
        escalated_by_line = {str(line or 0): count for line, count in line_rows}

        feedback_count = (await db.execute(select(func.count(Feedback.id)))).scalar() or 0
        avg_rating = (await db.execute(select(func.avg(Feedback.rating)))).scalar()
        # Два вопроса после обращения и удовлетворённость — по той же методике,
        # что и отчёт по отзывам: доля «да» среди ответивших, пропуск не считается
        answers = (await db.execute(select(Feedback.rating, Feedback.solved, Feedback.clear))).all()

        kb_active = (
            await db.execute(select(func.count(KBArticle.id)).where(KBArticle.status == "active"))
        ).scalar() or 0
        gaps_open = (
            await db.execute(select(func.count(KBGap.id)).where(KBGap.status == "open"))
        ).scalar() or 0

        # метрики качества автоответов — по метаданным последних сообщений ассистента
        recent = (
            (
                await db.execute(
                    select(Message)
                    .where(Message.role == "assistant")
                    .order_by(Message.created_at.desc())
                    .limit(2000)
                )
            )
            .scalars()
            .all()
        )
        answered = [m for m in recent if m.meta.get("answered") is True]
        no_answer = [m for m in recent if m.meta.get("answered") is False]
        latencies = sorted(m.meta["latency_ms"] for m in recent if m.meta.get("latency_ms"))
        confidences = [m.meta["confidence"] for m in answered if m.meta.get("confidence")]

        since = datetime.now(UTC) - timedelta(days=14)
        session_rows = (
            (await db.execute(select(ChatSession).where(ChatSession.created_at >= since))).scalars().all()
        )
        feedback_rows = (
            (await db.execute(select(Feedback).where(Feedback.created_at >= since))).scalars().all()
        )

    def day(dt: datetime) -> str:
        return dt.strftime("%d.%m")

    days = [(datetime.now(UTC) - timedelta(days=i)) for i in range(13, -1, -1)]
    series = []
    for d in days:
        key = day(d)
        day_sessions = [s for s in session_rows if day(s.created_at) == key]
        day_feedback = [f for f in feedback_rows if day(f.created_at) == key]
        series.append(
            {
                "day": key,
                "sessions": len(day_sessions),
                "escalated": sum(1 for s in day_sessions if s.escalated_at),
                "violations": sum(1 for s in day_sessions if s.status == "closed_violation"),
                "avg_rating": round(sum(f.rating for f in day_feedback) / len(day_feedback), 2)
                if day_feedback
                else None,
            }
        )

    total_answers = len(answered) + len(no_answer)
    return {
        "sessions": {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "escalated_by_line": escalated_by_line,
        },
        "quality": {
            "auto_answer_rate": round(len(answered) / total_answers, 3) if total_answers else None,
            "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
            "median_latency_ms": latencies[len(latencies) // 2] if latencies else None,
            "avg_rating": round(float(avg_rating), 2) if avg_rating else None,
            "feedback_count": feedback_count,
            "csat": share(sum(1 for rating, _, _ in answers if rating >= POSITIVE_MIN_RATING), len(answers)),
            "solved_rate": _yes_share(solved for _, solved, _ in answers),
            "clear_rate": _yes_share(clear for _, _, clear in answers),
        },
        "kb": {"active_articles": kb_active, "open_gaps": gaps_open},
        "series": series,
    }
