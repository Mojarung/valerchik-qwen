"""Актуальные голоса и очередь разбора, без автоматической порчи базы знаний."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update

from app.chat.errors import ChatError
from app.models import AnswerFeedback, AuditLog, ChatSession, KBArticle, Message
from app.schemas.answer_feedback import (
    AnswerRatingRequest,
    AnswerRatingSaved,
    AnswerReviewItem,
    AnswerReviewPage,
    AnswerReviewResolution,
    AnswerReviewResolved,
    AnswerReviewUpdate,
)
from app.services import guardrails

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.runtime import AppRuntime

log = logging.getLogger(__name__)
OPEN_STATUSES = ("open", "in_progress")


async def lock_session(db: AsyncSession, session_id: str) -> ChatSession:
    """Общий замок для изменения оценки; работает также в SQLite без FOR UPDATE."""
    await db.execute(update(ChatSession).where(ChatSession.id == session_id).values(topic=ChatSession.topic))
    session = await db.get(ChatSession, session_id)
    if session is None:
        raise ChatError(404, "Диалог не найден")
    return session


async def answer_in_session(db: AsyncSession, session_id: str, message_id: str) -> Message:
    msg = await db.get(Message, message_id)
    if msg is None or msg.session_id != session_id:
        raise ChatError(404, "Сообщение не найдено")
    if msg.role != "assistant" or (msg.meta or {}).get("kind"):
        raise ChatError(422, "Оценивать можно только ответы ассистента")
    return msg


async def snapshot(db: AsyncSession, msg: Message, session: ChatSession) -> dict[str, Any]:
    previous = (
        await db.execute(
            select(Message)
            .where(
                Message.session_id == msg.session_id,
                Message.role == "user",
                Message.created_at <= msg.created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    meta = msg.meta or {}
    question = meta.get("question") or (previous.content if previous else "")
    ids = [c["article_id"] for c in (msg.citations or []) if c.get("article_id")]
    sources = (
        (await db.execute(select(KBArticle).where(KBArticle.id.in_(ids)))).scalars().all() if ids else []
    )
    return {
        "question": question,
        "answer": msg.content,
        "citations": msg.citations or [],
        "audience": meta.get("audience") or session.audience or "all",
        "page": meta.get("page_context") or ((previous.meta or {}).get("page") if previous else None),
        "answer_meta": {k: v for k, v in meta.items() if not k.startswith("rating")},
        "sources": [
            {"id": a.id, "question": a.question, "answer": a.answer, "updated_at": a.updated_at.isoformat()}
            for a in sources
        ],
        "sources_captured_at": "rating",
    }


async def rate(
    rt: AppRuntime, session_id: str, message_id: str, body: AnswerRatingRequest
) -> AnswerRatingSaved:
    reason = body.reason if body.value == -1 else ""
    comment = body.comment.strip() if body.value == -1 else ""
    if rt.dyn.get("guardrails.pii_mask_enabled"):
        comment = guardrails.mask_pii(comment)
    async with rt.db.session_factory() as db:
        session = await lock_session(db, session_id)
        msg = await answer_in_session(db, session_id, message_id)
        row = await db.get(AnswerFeedback, message_id)
        if row is None:
            row = AnswerFeedback(
                message_id=message_id, session_id=session_id, snapshot=await snapshot(db, msg, session)
            )
            db.add(row)
        changed = (row.value, row.reason, row.comment) != (body.value, reason, comment)
        if changed:
            row.value, row.reason, row.comment = body.value, reason, comment
            row.status = "open" if body.value == -1 else "withdrawn"
        msg.meta = {
            **(msg.meta or {}),
            "rating": body.value,
            "rating_reason": reason,
            "rating_comment": comment,
        }
        await db.commit()
    # Запись уже состоялась: сбой уведомления не должен предлагать повторить голос.
    event = {
        "type": "message_rating",
        "session_id": session_id,
        "message_id": message_id,
        "value": body.value,
        "reason": reason,
        "comment": comment,
    }
    try:
        await rt.ws.publish("operators", event)
        await rt.ws.publish(f"session:{session_id}", event)
    except Exception:
        log.exception("Оценка сохранена, уведомление не доставлено: %s", message_id)
    return AnswerRatingSaved(value=body.value, reason=reason, comment=comment)


def item(row: AnswerFeedback) -> AnswerReviewItem:
    return AnswerReviewItem(
        message_id=row.message_id,
        session_id=row.session_id,
        value=row.value,
        reason=row.reason,
        comment=row.comment,
        snapshot=row.snapshot,
        status=row.status,
        resolution_note=row.resolution_note,
        article_id=row.article_id,
        recovery=row.recovery,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


async def listing(rt: AppRuntime, status: str, page: int) -> AnswerReviewPage:
    filters = [AnswerFeedback.value == -1]
    if status == "pending":
        filters.append(AnswerFeedback.status.in_(OPEN_STATUSES))
    elif status != "all":
        filters.append(AnswerFeedback.status == status)
    async with rt.db.session_factory() as db:
        counts = (
            await db.execute(select(AnswerFeedback.value, func.count()).group_by(AnswerFeedback.value))
        ).all()
        totals = {value: count for value, count in counts}
        total = (
            await db.execute(select(func.count()).select_from(AnswerFeedback).where(*filters))
        ).scalar_one()
        opened = (
            await db.execute(
                select(func.count())
                .select_from(AnswerFeedback)
                .where(AnswerFeedback.value == -1, AnswerFeedback.status.in_(OPEN_STATUSES))
            )
        ).scalar_one()
        rows = (
            (
                await db.execute(
                    select(AnswerFeedback)
                    .where(*filters)
                    .order_by(AnswerFeedback.updated_at.desc())
                    .offset((page - 1) * 30)
                    .limit(30)
                )
            )
            .scalars()
            .all()
        )
    return AnswerReviewPage(
        items=[item(row) for row in rows],
        total=total,
        positive=totals.get(1, 0),
        negative=totals.get(-1, 0),
        open_count=opened,
    )


async def review_row(db: AsyncSession, message_id: str) -> AnswerFeedback:
    row = (
        await db.execute(
            select(AnswerFeedback).where(AnswerFeedback.message_id == message_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise ChatError(404, "Замечание не найдено")
    if row.value != -1:
        raise ChatError(409, "Пользователь изменил оценку — обновите список")
    return row


async def set_status(
    rt: AppRuntime, message_id: str, body: AnswerReviewUpdate, actor: str
) -> AnswerReviewItem:
    allowed = {
        "open": {"in_progress", "dismissed"},
        "in_progress": {"open", "dismissed"},
        "fixed": {"verified", "open"},
        "verified": {"open"},
        "dismissed": {"open"},
    }
    async with rt.db.session_factory() as db:
        row = await review_row(db, message_id)
        if body.status != row.status and body.status not in allowed.get(row.status, set()):
            raise ChatError(409, "Недопустимый переход статуса")
        if body.status in ("dismissed", "verified") and not body.note.strip():
            raise ChatError(422, "Опишите причину отклонения или результат проверки")
        row.status, row.resolution_note = body.status, body.note.strip()
        db.add(
            AuditLog(
                actor=actor,
                action="answer_feedback.review",
                entity=message_id,
                payload={"status": body.status, "note": body.note.strip()},
            )
        )
        await db.commit()
        return item(row)


async def resolve(
    rt: AppRuntime, message_id: str, body: AnswerReviewResolution, actor: str
) -> AnswerReviewResolved:
    fingerprint = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    async with rt.db.session_factory() as db:
        row = await review_row(db, message_id)
        if row.resolution_fingerprint == fingerprint and row.article_id:
            article_id = row.article_id
            if row.status in ("fixed", "verified"):
                return AnswerReviewResolved(
                    article_id=article_id, status="verified" if row.status == "verified" else "fixed"
                )
            if row.status not in OPEN_STATUSES:
                raise ChatError(409, "Сначала верните замечание в работу")
            row.status = "in_progress"
            await db.commit()
        else:
            if row.status not in OPEN_STATUSES:
                raise ChatError(409, "Сначала верните замечание в работу")
            article_id = await _save_resolution(db, row, body, actor, fingerprint)
        version = row.updated_at
    await rt.kb.index_article(article_id)
    async with rt.db.session_factory() as db:
        result = await db.execute(
            update(AnswerFeedback)
            .where(
                AnswerFeedback.message_id == message_id,
                AnswerFeedback.value == -1,
                AnswerFeedback.status == "in_progress",
                AnswerFeedback.updated_at == version,
                AnswerFeedback.resolution_fingerprint == fingerprint,
            )
            .values(status="fixed")
            .returning(AnswerFeedback.message_id)
        )
        changed = result.scalar_one_or_none()
        await db.commit()
        if changed is None:
            current = await db.get(AnswerFeedback, message_id)
            if (
                current is None
                or current.value != -1
                or current.resolution_fingerprint != fingerprint
                or current.status not in ("fixed", "verified")
            ):
                raise ChatError(409, "Статья сохранена, но замечание изменилось. Обновите список")
            return AnswerReviewResolved(
                article_id=article_id, status="verified" if current.status == "verified" else "fixed"
            )
    return AnswerReviewResolved(article_id=article_id)


async def _save_resolution(
    db: AsyncSession,
    row: AnswerFeedback,
    body: AnswerReviewResolution,
    actor: str,
    fingerprint: str,
) -> str:
    fields = body.article.model_dump()
    if fields["status"] != "active":
        raise ChatError(422, "Для исправления опубликуйте активную статью")
    article = await _resolution_article(db, body)
    for key, value in fields.items():
        setattr(article, key, value)
    await db.flush()
    row.article_id = article.id
    row.resolution_note = body.note.strip()
    row.resolution_fingerprint = fingerprint
    # Индексация идёт отдельно: повтор запроса после сбоя продолжит её без новой статьи.
    row.status = "in_progress"
    db.add(
        AuditLog(
            actor=actor,
            action="answer_feedback.article",
            entity=row.message_id,
            payload={"article_id": article.id, "note": row.resolution_note},
        )
    )
    await db.commit()
    return article.id


async def _resolution_article(db: AsyncSession, body: AnswerReviewResolution) -> KBArticle:
    if body.article_id:
        article = (
            await db.execute(select(KBArticle).where(KBArticle.id == body.article_id).with_for_update())
        ).scalar_one_or_none()
        if article is None:
            raise ChatError(404, "Статья не найдена")
        if not body.expected_updated_at or article.updated_at.isoformat() != body.expected_updated_at:
            raise ChatError(409, "Статья уже изменилась. Откройте её заново перед сохранением")
        return article
    article = KBArticle(source="feedback")
    db.add(article)
    return article
