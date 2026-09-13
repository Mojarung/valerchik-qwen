"""Отзывы в админке: лента оценок и последний сводный отчёт."""

import json
import logging
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AnalyticsReport, Feedback, User
from app.schemas.admin import (
    FeedbackCluster,
    FeedbackItem,
    FeedbackReport,
    FeedbackSummary,
    Methodology,
    NegativeTrend,
    SpecialistScore,
    SupportExportReport,
    SystemProblem,
    TopicQuality,
)

log = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]
#: Отзывов на страницу ленты.
PAGE_SIZE = 50
#: Кто вёл диалог, если оператора не было.
AI_NAME = "ИИ-ассистент"
#: Вид отчёта по реальной выгрузке СТП (tools/support_quality.py).
SUPPORT_EXPORT_KIND = "support_export_quality"
#: Отчёт по выгрузке СТП за 2026 год, посчитанный заранее: на свежем стенде
#: страница показывает системные проблемы по реальным данным сразу, а не
#: после того, как кто-то запустит офлайн-анализ.
BUNDLED_SUPPORT_EXPORT = Path(__file__).resolve().parents[1] / "seed" / "support_export_quality.json"


async def recent(session_factory: SessionFactory, page: int = 1) -> list[FeedbackItem]:
    """Оценки консультаций, свежие сверху, с именем того, кто вёл диалог."""
    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(Feedback)
                    .order_by(desc(Feedback.created_at))
                    .offset((page - 1) * PAGE_SIZE)
                    .limit(PAGE_SIZE)
                )
            )
            .scalars()
            .all()
        )
        names = {u.id: u.display_name for u in (await db.execute(select(User))).scalars()}
    return [
        FeedbackItem(
            id=f.id,
            session_id=f.session_id,
            rating=f.rating,
            comment=f.comment,
            sentiment=f.sentiment,
            specialist_kind=f.specialist_kind,
            operator=names.get(f.operator_id, AI_NAME) if f.operator_id else AI_NAME,
            created_at=f.created_at.isoformat(),
            solved=f.solved,
            clear=f.clear,
        )
        for f in rows
    ]


async def _latest(session_factory: SessionFactory, kind: str) -> AnalyticsReport | None:
    async with session_factory() as db:
        return (
            (
                await db.execute(
                    select(AnalyticsReport)
                    .where(AnalyticsReport.kind == kind)
                    .order_by(desc(AnalyticsReport.created_at))
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )


@cache
def _bundled_support_export() -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = json.loads(BUNDLED_SUPPORT_EXPORT.read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError:
        return None
    return data


async def support_export(session_factory: SessionFactory) -> SupportExportReport | None:
    """Отчёт по выгрузке СТП: свежий из базы, иначе приложенный к поставке."""
    row = await _latest(session_factory, SUPPORT_EXPORT_KIND)
    for payload in (row.payload if row else None, _bundled_support_export()):
        if not payload:
            continue
        try:
            return SupportExportReport.model_validate(payload)
        except ValidationError:
            # Отчёт чужой версии не должен ронять страницу отзывов и не должен
            # оставлять её без реальных данных: тогда показываем приложенный
            log.warning("Отчёт по выгрузке СТП не разобран — пропускаю", exc_info=True)
    return None


def _items[T: BaseModel](model: type[T], payload: dict[str, Any], key: str) -> list[T] | None:
    """Список из сохранённого отчёта; ключа нет — отчёт старой версии, поля нет и в ответе."""
    if key not in payload:
        return None
    return [model.model_validate(item) for item in payload.get(key) or []]


async def latest_report(session_factory: SessionFactory) -> FeedbackReport:
    """Последний сводный отчёт. Берём известные поля: отчёт — JSON из базы, и
    сформированный прежней версией не должен ронять страницу."""
    row = await _latest(session_factory, "feedback_summary")
    exported = await support_export(session_factory)
    extra: dict[str, Any] = {"support_export": exported} if exported else {}
    if row is None:
        return FeedbackReport(exists=False, **extra)
    payload = row.payload or {}
    optional: dict[str, Any] = {
        "topics": _items(TopicQuality, payload, "topics"),
        "problems": _items(SystemProblem, payload, "problems"),
        "trend": NegativeTrend.model_validate(payload["trend"]) if payload.get("trend") else None,
        "methodology": Methodology.model_validate(payload["methodology"])
        if payload.get("methodology")
        else None,
        "recommendations": list(payload["recommendations"]) if "recommendations" in payload else None,
    }
    return FeedbackReport(
        exists=True,
        created_at=row.created_at.isoformat(),
        summary=FeedbackSummary.model_validate(payload["summary"]) if payload.get("summary") else None,
        specialists=[SpecialistScore.model_validate(s) for s in payload.get("specialists") or []],
        clusters=[FeedbackCluster.model_validate(c) for c in payload.get("clusters") or []],
        conclusion=payload.get("conclusion"),
        generated_at=payload.get("generated_at"),
        **{key: value for key, value in optional.items() if value is not None},
        **extra,
    )
