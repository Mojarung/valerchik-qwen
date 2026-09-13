"""Transactional support workflow. Database state is authoritative, sockets are hints."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy import text as sql_text

from app.models import AuditLog, ChatSession, Message, SupportCommand, SupportEvent, User, new_id, utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.runtime import AppRuntime

SUPPORT_STATUSES = ("waiting_operator", "operator_assigned", "operator_active", "waiting_user")
OWNED_STATUSES = ("operator_active", "waiting_user")


class AlreadyAccepted(Exception):
    def __init__(self, result: dict[str, Any]):
        self.result = result


def visitor_key(sid: str, request_id: str) -> str:
    return hashlib.sha256(f"{sid}:visitor:{request_id}".encode()).hexdigest()


def state(s: ChatSession) -> dict[str, Any]:
    return {
        "session_id": s.id,
        "status": s.status,
        "line": s.line,
        "operator_id": s.operator_id,
        "version": s.workflow_version or 0,
        "waiting_reason": s.waiting_reason,
        "routing": s.routing or {},
        "assigned_at": s.assigned_at.isoformat() if s.assigned_at else None,
    }


def emit(db: AsyncSession, sid: str, room: str, payload: dict[str, Any]) -> None:
    event_id = new_id()
    db.add(
        SupportEvent(
            id=event_id,
            session_id=sid,
            room=room,
            payload={**payload, "event_id": event_id, "session_id": sid},
        )
    )


async def lock_session(db: AsyncSession, sid: str) -> ChatSession:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        await db.execute(sql_text("SELECT pg_advisory_xact_lock(hashtext('support_workflow'))"))
    # UPDATE serializes on PostgreSQL and SQLite, including existing nullable versions.
    result = await db.execute(
        update(ChatSession)
        .where(ChatSession.id == sid)
        .values(workflow_version=func.coalesce(ChatSession.workflow_version, 0))
    )
    if not result.rowcount:
        raise HTTPException(404, "Обращение не найдено")
    return (
        await db.execute(
            select(ChatSession).where(ChatSession.id == sid).execution_options(populate_existing=True)
        )
    ).scalar_one()


async def staff_access(db: AsyncSession, s: ChatSession, actor: str, *, write: bool = False) -> User:
    user = await db.get(User, actor)
    if not user or not user.is_active or user.role not in ("operator", "admin"):
        raise HTTPException(403, "Нет доступа к поддержке")
    if user.role != "admin" and user.line != s.line:
        raise HTTPException(403, "Обращение другой линии поддержки")
    if write and s.operator_id != actor:
        raise HTTPException(403, "Сначала примите обращение; отвечать может только владелец")
    return user


async def require_access(rt: AppRuntime, sid: str, actor: str, *, write: bool = False) -> None:
    async with rt.db.session_factory() as db:
        s = await db.get(ChatSession, sid)
        if not s:
            raise HTTPException(404, "Обращение не найдено")
        await staff_access(db, s, actor, write=write)


def notice(
    db: AsyncSession,
    s: ChatSession,
    text: str,
    kind: str = "support_status",
    meta: dict[str, Any] | None = None,
) -> None:
    now = utcnow()
    msg = Message(
        id=new_id(),
        session_id=s.id,
        role="system",
        content=text,
        citations=[],
        meta={"kind": kind, **(meta or {})},
        created_at=now,
    )
    db.add(msg)
    emit(
        db,
        s.id,
        f"session:{s.id}",
        {
            "type": "message",
            "message": {
                "id": msg.id,
                "role": msg.role,
                "content": text,
                "citations": [],
                "meta": msg.meta,
                "created_at": now.isoformat(),
            },
        },
    )


def changed(db: AsyncSession, s: ChatSession, actor: str, action: str, reason: str = "") -> None:
    s.workflow_version = (s.workflow_version or 0) + 1
    db.add(
        AuditLog(actor=actor, action=f"support.{action}", entity=s.id, payload={**state(s), "reason": reason})
    )
    public = state(s)
    public.pop("routing", None)  # Classification internals stay in the staff desk.
    emit(db, s.id, f"session:{s.id}", {"type": "support_state", **public})
    emit(db, s.id, "operators", {"type": "ticket_changed"})


async def transition(
    rt: AppRuntime, sid: str, actor: str, action: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = body or {}
    await require_access(rt, sid, actor)
    if body.get("reason"):
        await rt.chat._screen_operator_text(sid, actor, str(body["reason"]))
    async with rt.db.session_factory() as db:
        s = await lock_session(db, sid)
        if body.get("version") is not None and body["version"] != (s.workflow_version or 0):
            raise HTTPException(409, {"message": "Обращение изменилось", "state": state(s)})
        user = await staff_access(db, s, actor)
        reason = str(body.get("reason") or "").strip()
        text = await _transition(rt, db, s, user, action, body)
        changed(db, s, actor, action, reason)
        notice(db, s, text)
        await db.commit()
        result = state(s)
    if action == "take":
        from app.chat.copilot_flow import _last_client_question
        from app.services.background import spawn

        question = _last_client_question(await rt.chat.get_messages(sid))
        if question:
            spawn(rt.chat._push_copilot(sid, question))
    return result


async def _transition(  # noqa: C901, PLR0913
    rt: AppRuntime, db: AsyncSession, s: ChatSession, user: User, action: str, body: dict[str, Any]
) -> str:
    reason = str(body.get("reason") or "").strip()
    if action == "take":
        if s.status not in ("waiting_operator", "operator_assigned") or s.operator_id not in (None, user.id):
            raise HTTPException(409, {"message": "Обращение уже принято или завершено", "state": state(s)})
        s.status, s.operator_id, s.assigned_at = "operator_active", user.id, None
        return f"Оператор {user.display_name or user.username} принял обращение."
    if action == "reassign":
        if user.role != "admin":
            raise HTTPException(403, "Переназначение доступно администратору")
        target = await db.get(User, body.get("operator_id"))
        if not target or not target.is_active or target.role != "operator" or target.line != s.line:
            raise HTTPException(422, "Выберите сотрудника этой линии")
        if s.status not in SUPPORT_STATUSES:
            raise HTTPException(409, "Обращение завершено")
        s.operator_id, s.status, s.assigned_at = target.id, "operator_assigned", utcnow()
        s.waiting_reason = None
        emit(
            db,
            s.id,
            f"operator:{target.id}",
            {
                "type": "assigned",
                "line": s.line,
                "reason": reason,
                "question": s.escalation_reason,
                "waiting_seconds": 0,
            },
        )
        return "Обращение переназначено сотруднику поддержки."
    await staff_access(db, s, user.id, write=True)
    if action == "decline" and s.status == "operator_assigned":
        s.last_operator_id, s.operator_id, s.assigned_at = s.operator_id, None, None
        s.status = "waiting_operator"
        return "Обращение ожидает свободного сотрудника."
    if s.status not in OWNED_STATUSES:
        raise HTTPException(409, "Сначала примите обращение")
    if action == "transfer":
        line = body.get("line")
        if line not in (1, 2, 3):
            raise HTTPException(422, "Укажите направление")
        s.line, s.status, s.waiting_reason = line, "waiting_operator", None
        s.last_operator_id, s.operator_id, s.assigned_at = s.operator_id, None, None
        s.routing = {
            **(s.routing or {}),
            "line": line,
            "source": "operator",
            "reason": reason or "operator_transfer",
        }
        lines: dict[str, Any] = rt.dyn.get("routing.lines") or {}
        label = (lines.get(str(line)) or {}).get("label", "поддержка")
        suffix = f" Комментарий: {reason}" if reason else ""
        return f"Обращение передано в направление «{label}».{suffix}"
    if action == "wait":
        if not reason:
            raise HTTPException(422, "Напишите вопрос пользователю")
        s.status = "waiting_user"
        s.waiting_reason = "information"
        return reason
    if action == "resolve":
        if not reason:
            last_reply = (
                await db.execute(
                    select(Message.content)
                    .where(Message.session_id == s.id, Message.role == "operator")
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if not last_reply:
                raise HTTPException(422, "Сначала отправьте пользователю решение")
        s.status = "waiting_user"
        s.waiting_reason = "solution"
        return reason or "Сотрудник предложил решение. Оно помогло?"
    raise HTTPException(409, "Действие недоступно для этого состояния")


async def user_response(rt: AppRuntime, sid: str, solved: bool, version: int | None = None) -> dict[str, Any]:
    from app.services.dispatch import online_operator_ids

    online = [] if solved else await online_operator_ids(rt.cache)
    async with rt.db.session_factory() as db:
        s = await lock_session(db, sid)
        if version is not None and version != (s.workflow_version or 0):
            raise HTTPException(409, {"message": "Обращение изменилось", "state": state(s)})
        if s.status != "waiting_user" or s.waiting_reason != "solution":
            raise HTTPException(409, "Нет решения, ожидающего подтверждения")
        if solved:
            s.status, s.closed_at = "resolved", utcnow()
            notice(db, s, str(rt.dyn.get("feedback.request_message")), "feedback_request")
            emit(db, s.id, f"session:{s.id}", {"type": "session_closed", "reason": "resolved"})
        else:
            s.status = "operator_active" if s.operator_id in online else "waiting_operator"
            if s.status == "waiting_operator":
                s.operator_id = None
            notice(db, s, "Проблема не решена. Поддержка продолжит работу в этом диалоге.")
        s.waiting_reason = None
        changed(db, s, "visitor", "confirm" if solved else "reopen")
        await db.commit()
        return state(s)


async def send_message(  # noqa: PLR0913
    rt: AppRuntime,
    sid: str,
    actor: str,
    text: str,
    request_id: str | None = None,
    attachment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    await rt.chat._screen_operator_text(sid, actor, text)
    if not text.strip() and not attachment:
        raise HTTPException(422, "Пустое сообщение")
    digest = hashlib.sha256(json.dumps([text, attachment], sort_keys=True).encode()).hexdigest()
    key = hashlib.sha256(f"{sid}:{actor}:{request_id}".encode()).hexdigest() if request_id else None
    async with rt.db.session_factory() as db:
        s = await lock_session(db, sid)
        await staff_access(db, s, actor, write=True)
        if key:
            receipt = await db.get(SupportCommand, key)
            if receipt:
                if receipt.fingerprint != digest:
                    raise HTTPException(409, "Идентификатор уже использован для другого сообщения")
                return receipt.result
        if s.status not in OWNED_STATUSES:
            raise HTTPException(409, "Сначала примите обращение")
        now = utcnow()
        meta: dict[str, Any] = {"operator_id": actor}
        if attachment:
            meta["attachment"] = attachment
        msg = Message(
            id=new_id(),
            session_id=sid,
            role="operator",
            content=text.strip(),
            citations=[],
            meta=meta,
            created_at=now,
        )
        db.add(msg)
        emit(db, sid, f"session:{sid}", rt.chat._msg_event(msg))
        changed(db, s, actor, "message")
        result = {"status": "sent", "message_id": msg.id}
        if key:
            db.add(SupportCommand(id=key, session_id=sid, fingerprint=digest, result=result))
        await db.commit()
        return result


async def flush_outbox(rt: AppRuntime) -> int:
    from app.ws.manager import BUS_CHANNEL

    async with rt.db.session_factory() as db:
        events = (
            (
                await db.execute(
                    select(SupportEvent)
                    .where(SupportEvent.delivered_at.is_(None))
                    .order_by(SupportEvent.created_at, SupportEvent.id)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for event in events:
            await rt.cache.publish(BUS_CHANNEL, json.dumps({"room": event.room, "event": event.payload}))
            event.delivered_at = utcnow()
        await db.commit()
        return len(events)


async def expire_assignments(rt: AppRuntime) -> list[dict[str, Any]]:
    deadline = utcnow() - timedelta(seconds=int(rt.dyn.get("operator.accept_seconds") or 60))
    expired = []
    async with rt.db.session_factory() as db:
        ids = (
            (
                await db.execute(
                    select(ChatSession.id).where(
                        ChatSession.status == "operator_assigned", ChatSession.assigned_at <= deadline
                    )
                )
            )
            .scalars()
            .all()
        )
        for sid in ids:
            s = await lock_session(db, sid)
            if s.status != "operator_assigned" or not s.assigned_at or s.assigned_at > deadline:
                continue
            expired.append(state(s))
            s.last_operator_id, s.operator_id, s.assigned_at = s.operator_id, None, None
            s.status = "waiting_operator"
            s.reclaimed_count = (s.reclaimed_count or 0) + 1
            changed(db, s, "dispatcher", "expired")
        await db.commit()
    return expired
