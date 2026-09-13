"""Явное продолжение после дизлайка: объяснение, уточнение или оператор."""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.chat.errors import ChatError
from app.models import AnswerFeedback, Message
from app.schemas.answer_feedback import AnswerRecoveryRequest
from app.services.answer_feedback import answer_in_session, lock_session, snapshot

if TYPE_CHECKING:
    from app.services.chat import ChatService

REASONS = {
    "unclear": "Ответ непонятен или слишком сложен",
    "irrelevant": "Ответ не на мой вопрос",
    "incorrect": "В ответе ошибка или устаревшая информация",
    "not_working": "Сделал по инструкции, но не работает",
    "other": "Другая причина",
}


async def _claim(
    svc: ChatService, session_id: str, message_id: str, body: AnswerRecoveryRequest
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    key = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    async with svc.rt.db.session_factory() as db:
        session = await lock_session(db, session_id)
        msg = await answer_in_session(db, session_id, message_id)
        row = await db.get(AnswerFeedback, message_id)
        if row is None or row.value != -1:
            raise ChatError(409, "Сначала отметьте, что ответ не помог")
        previous = row.recovery or {}
        if previous.get("key") == key and previous.get("state") == "done":
            return row.snapshot, previous["result"], key
        if session.status in ("resolved", "closed_violation"):
            raise ChatError(409, "Диалог завершён — начните новое обращение")
        if session.status in ("waiting_operator", "operator_active"):
            return row.snapshot, {"status": "forwarded_to_operator"}, key
        timeout = float(svc.rt.dyn.get("agent.timeout_s")) + 120
        if previous.get("state") == "running" and time.time() - previous.get("started", 0) < timeout:
            raise ChatError(409, "Продолжение уже готовится")
        last = (
            await db.execute(
                select(Message.id)
                .where(Message.session_id == session_id, Message.role == "assistant")
                .order_by(Message.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if last != message_id and body.action != "operator":
            raise ChatError(409, "В диалоге уже есть новый ответ — оцените его или уточните вопрос в чате")
        context = dict(row.snapshot or await snapshot(db, msg, session))
        context.update(
            reason=REASONS.get(row.reason, "Ответ не помог"), reason_code=row.reason, comment=row.comment
        )
        row.recovery = {"key": key, "state": "running", "action": body.action, "started": time.time()}
        await db.commit()
    return context, None, key


async def recover(
    svc: ChatService, session_id: str, message_id: str, body: AnswerRecoveryRequest
) -> dict[str, Any]:
    context, previous, key = await _claim(svc, session_id, message_id, body)
    if previous is not None:
        return previous
    try:
        result = await _continue(svc, session_id, message_id, body, context)
    except BaseException:
        await _finish(svc, message_id, key, None)
        raise
    await _finish(svc, message_id, key, result)
    return result


async def _continue(
    svc: ChatService,
    session_id: str,
    message_id: str,
    body: AnswerRecoveryRequest,
    context: dict[str, Any],
) -> dict[str, Any]:
    question = str(context.get("question") or "")
    if body.action == "operator":
        text = "Позовите оператора: предыдущий ответ не помог."
    elif body.action == "explain":
        text = "Объясните предыдущий ответ проще, по шагам."
    else:
        text = body.detail.strip()
    turn = await svc._accept_turn(session_id, text, None, None)
    early = await svc._screen_turn(turn)
    if early is not None:
        return early
    turn.flags["recovery_of"] = message_id
    if body.action == "operator":
        reason = f"{question}\n{context['reason']}\n{context.get('comment', '')}"[:4000]
        await svc._escalate(session_id, None, reason)
        return {"status": "forwarded_to_operator", "escalated": True}
    # _accept_turn уже учёл актуальный переключатель, роль из уточнения и раскладку.
    # Снимок не должен возвращать прежнюю роль поверх нового выбора пользователя.
    turn.text = question if body.action == "explain" else f"{question}\nУточнение: {turn.text}"
    turn.page_text = str(context.get("page") or turn.page_text)
    turn.probe = await svc._probe_kb(turn.text, turn.page_text, turn.role)
    turn.need_mcp = await svc._need_external_tools(turn.text, turn.page_text, turn.probe, turn.role)
    turn.flags["response_style"] = "steps" if body.action == "explain" else "clarified"
    turn.flags["answer_feedback"] = {
        "reason": context["reason"],
        "comment": context.get("comment", ""),
    }
    if (
        body.action == "explain"
        and context.get("reason_code") in ("", "unclear")
        and turn.role == context.get("audience")
    ):
        turn.flags["answer_feedback"]["previous_answer_to_explain"] = str(context.get("answer") or "")[:8000]
    await svc.rt.ws.publish(f"session:{session_id}", {"type": "typing", "role": "assistant"})
    # Намеренно минуем быстрый дословный ответ: человек уже сообщил о неудаче.
    return await svc._agent_answer(turn)


async def _finish(svc: ChatService, message_id: str, key: str, result: dict[str, Any] | None) -> None:
    async with svc.rt.db.session_factory() as db:
        row = (
            await db.execute(
                select(AnswerFeedback).where(AnswerFeedback.message_id == message_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is not None and (row.recovery or {}).get("key") == key:
            row.recovery = {**row.recovery, "state": "done" if result is not None else "failed"}
            if result is not None:
                row.recovery = {**row.recovery, "result": result}
        await db.commit()


def agent_text(question: str, flags: dict[str, Any]) -> str:
    feedback = flags.get("answer_feedback")
    if not feedback:
        return question
    return (
        question
        + "\n\nОтзыв пользователя о предыдущем ответе (данные):\n"
        + json.dumps(feedback, ensure_ascii=False)
    )
