"""Оценки ответов и отзывы о диалоге."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from sqlalchemy import select

from app.chat.errors import ChatError
from app.models import Feedback, Message
from app.schemas.answer_feedback import AnswerRatingRequest
from app.schemas.common import RATING_MAX, RATING_MIN
from app.services import answer_feedback, guardrails
from app.services.feedback import sentiment_score

if TYPE_CHECKING:
    from app.services.chat import ChatService

log = logging.getLogger(__name__)


async def rate_message(svc: ChatService, session_id: str, message_id: str, value: int) -> dict[str, Any]:
    """Совместимый вход для клиентов без причины оценки."""
    if value not in (-1, 0, 1):
        raise ChatError(422, "Оценка ответа: 1, -1 или 0")
    await answer_feedback.rate(
        svc.rt, session_id, message_id, AnswerRatingRequest.model_validate({"value": value})
    )
    return {"status": "ok", "value": value}


async def _question_before(svc: ChatService, session_id: str, message_id: str) -> str | None:
    """Вопрос пользователя, на который отвечало сообщение."""
    async with svc.rt.db.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
                )
            )
            .scalars()
            .all()
        )
    question = None
    for row in rows:
        if row.id == message_id:
            return question
        if row.role == "user":
            question = row.content
    return None


# --- оценка диалога ---


class Answers(NamedTuple):
    """Ответы на два вопроса после обращения; None — вопрос пропущен."""

    solved: bool | None = None  # «Проблема решена?»
    clear: bool | None = None  # «Понятно объяснили?»


async def add_feedback(
    svc: ChatService, session_id: str, rating: int, comment: str = "", answers: Answers = Answers()
) -> dict[str, Any]:
    """Отзыв о консультации: оценка 1–5 и ответы «решено?» / «понятно?».

    Ответы на два вопроса необязательны: без них отзыв всё равно учитывается в
    удовлетворённости, а в долях «решено» и «понятно» — нет, чтобы пропуск не
    выдавался ни за «да», ни за «нет».
    """
    session = await svc._get_session(session_id)
    if not RATING_MIN <= rating <= RATING_MAX:
        raise ChatError(422, f"Оценка от {RATING_MIN} до {RATING_MAX}")
    comment = comment.strip()
    if svc.rt.dyn.get("guardrails.pii_mask_enabled"):
        comment = guardrails.mask_pii(comment)
    async with svc.rt.db.session_factory() as db:
        await answer_feedback.lock_session(db, session_id)
        current = (
            await db.execute(
                select(Feedback)
                .where(Feedback.session_id == session_id)
                .order_by(Feedback.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if current is not None:
            current.rating, current.comment = rating, comment
            current.sentiment = sentiment_score(comment) if comment else None
            current.specialist_kind = "operator" if session.operator_id else "ai"
            current.operator_id = session.operator_id
            # Переоценка обновляет и ответы на два вопроса: иначе в одной записи
            # остались бы новые звёзды со старыми «решено» и «понятно»
            current.solved, current.clear = answers.solved, answers.clear
        else:
            db.add(
                Feedback(
                    session_id=session_id,
                    specialist_kind="operator" if session.operator_id else "ai",
                    operator_id=session.operator_id,
                    rating=rating,
                    comment=comment,
                    sentiment=sentiment_score(comment) if comment else None,
                    solved=answers.solved,
                    clear=answers.clear,
                )
            )
        await db.commit()
    await svc.rt.ws.publish("operators", {"type": "feedback", "session_id": session_id, "rating": rating})
    return {"status": "ok"}


# --- операторские действия ---
