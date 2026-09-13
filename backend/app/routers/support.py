"""Explicit support commands and durable visitor state."""

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.models import AuditLog, CopilotRun, Message, User
from app.routers.chat import _require_owner
from app.security import StaffUser
from app.services import support_flow

router = APIRouter(tags=["support"])


@router.get("/api/operator/sessions/{session_id}/staff")
async def staff(request: Request, session_id: str, user: StaffUser) -> list[dict[str, Any]]:
    from fastapi import HTTPException

    rt = request.app.state.rt
    async with rt.db.session_factory() as db:
        s = await support_flow.lock_session(db, session_id)
        actor = await support_flow.staff_access(db, s, user.id)
        if actor.role != "admin":
            raise HTTPException(403, "Только для администратора")
        people = (
            (
                await db.execute(
                    select(User).where(User.role == "operator", User.is_active.is_(True), User.line == s.line)
                )
            )
            .scalars()
            .all()
        )
        return [{"id": p.id, "name": p.display_name or p.username, "line": p.line} for p in people]


class DraftFeedback(BaseModel):
    run_id: str = Field(min_length=1, max_length=32)
    action: Literal["inserted", "edited", "discarded", "sent"]
    text: str = Field(default="", max_length=4000)


@router.post("/api/operator/sessions/{session_id}/copilot-feedback")
async def draft_feedback(request: Request, session_id: str, user: StaffUser, body: DraftFeedback) -> dict:
    from fastapi import HTTPException

    rt = request.app.state.rt
    await support_flow.require_access(rt, session_id, user.id, write=True)
    async with rt.db.session_factory() as db:
        run = await db.get(CopilotRun, body.run_id)
        if not run or run.session_id != session_id:
            raise HTTPException(404, "Черновик не найден")
        original = run.payload.get("draft", {}).get("text", "")
        db.add(
            AuditLog(
                actor=user.id,
                action=f"copilot.{body.action}",
                entity=session_id,
                payload={"run_id": run.id, "changed": body.text != original, "text": body.text},
            )
        )
        await db.commit()
    return {"status": "ok"}


class SupportAction(BaseModel):
    version: int | None = Field(default=None, ge=0)
    reason: str = Field(default="", max_length=4000)
    line: int | None = Field(default=None, ge=1, le=3)
    operator_id: str | None = Field(default=None, max_length=32)


class Confirmation(BaseModel):
    solved: bool
    version: int | None = Field(default=None, ge=0)


@router.post("/api/operator/sessions/{session_id}/actions/{action}")
async def action(
    request: Request,
    session_id: str,
    user: StaffUser,
    action: Literal["take", "decline", "transfer", "wait", "resolve", "reassign"],
    body: SupportAction,
) -> dict:
    return await support_flow.transition(request.app.state.rt, session_id, user.id, action, body.model_dump())


@router.post("/api/chat/sessions/{session_id}/solution")
async def confirm(request: Request, session_id: str, body: Confirmation) -> dict:
    _require_owner(request, session_id)
    return await support_flow.user_response(request.app.state.rt, session_id, body.solved, body.version)


@router.get("/api/chat/sessions/{session_id}/support")
async def status(request: Request, session_id: str) -> dict:
    _require_owner(request, session_id)
    rt = request.app.state.rt
    s = await rt.chat._get_session(session_id)
    async with rt.db.session_factory() as db:
        query = select(func.count(Message.id)).where(
            Message.session_id == session_id, Message.role.in_(("operator", "system"))
        )
        if s.read_at:
            query = query.where(Message.created_at > s.read_at)
        unread = (await db.execute(query)).scalar_one()
        latest = (
            await db.execute(select(func.max(Message.created_at)).where(Message.session_id == session_id))
        ).scalar_one()
    data = support_flow.state(s)
    data.pop("routing", None)
    return {**data, "unread": unread, "read_cursor": latest.isoformat() if latest else None}


class ReadReceipt(BaseModel):
    message_id: str = Field(min_length=1, max_length=32)


@router.post("/api/chat/sessions/{session_id}/read")
async def read(request: Request, session_id: str, body: ReadReceipt) -> dict:
    from fastapi import HTTPException

    _require_owner(request, session_id)
    async with request.app.state.rt.db.session_factory() as db:
        msg = await db.get(Message, body.message_id)
        if not msg or msg.session_id != session_id:
            raise HTTPException(422, "Сообщение не принадлежит диалогу")
        s = await support_flow.lock_session(db, session_id)
        if s.read_at is None or s.read_at < msg.created_at:
            s.read_at = msg.created_at
        await db.commit()
    return {"status": "ok"}
