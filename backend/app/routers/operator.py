"""Рабочее место оператора: очередь обращений, чат, копайлот, шаблоны, поиск по БЗ."""

import asyncio
import contextlib
import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)

from app.adapters.storage import safe_filename
from app.config import get_settings
from app.middleware import check_rate_limit
from app.models import new_id
from app.schemas import OperatorMessageRequest, QuickReplyIn, ToKBRequest
from app.schemas.base import Created
from app.schemas.chat import AttachmentSent, ChatMessageOut
from app.schemas.operator import Ack, CopilotDraft, DraftArticle, KbHit, QueueItem, QuickReplyOut
from app.security import AUTH_COOKIE, StaffUser, decode_token, origin_allowed
from app.services import articles, copilot, operator_desk, support_flow
from app.services.dispatch import (
    PRESENCE_BEAT_SECONDS,
    mark_offline,
    mark_online,
)
from app.uploads import StoredFile, read_limited
from app.ws.manager import OPERATORS_ROOM

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = logging.getLogger(__name__)


async def _session_access(request: Request, user: StaffUser) -> None:
    sid = request.path_params.get("session_id")
    if sid:
        await support_flow.require_access(request.app.state.rt, sid, user.id)


router = APIRouter(prefix="/api/operator", tags=["operator"], dependencies=[Depends(_session_access)])


@router.get("/sessions", response_model=list[QueueItem])
async def list_sessions(
    request: Request, user: StaffUser, status: str = "queue", line: int | None = None
) -> list[QueueItem]:
    return await operator_desk.queue(request.app.state.rt.db.session_factory, status, line, actor=user.id)


@router.post("/sessions/{session_id}/take", response_model=Ack)
async def take_session(request: Request, session_id: str, user: StaffUser) -> Ack:
    rt = request.app.state.rt
    await support_flow.transition(rt, session_id, user.id, "take")
    return Ack(status="taken")


@router.post("/sessions/{session_id}/message", response_model=Ack)
async def operator_message(
    request: Request, session_id: str, user: StaffUser, body: OperatorMessageRequest
) -> Ack:
    await support_flow.send_message(request.app.state.rt, session_id, user.id, body.text, body.request_id)
    return Ack(status="sent")


@router.post("/sessions/{session_id}/draft", response_model=CopilotDraft)
async def operator_draft(request: Request, session_id: str, user: StaffUser) -> CopilotDraft:
    """Черновик ответа по запросу оператора.

    Лимит частоты здесь не про защиту от абсурда, а про деньги: каждое нажатие —
    полноценный вызов модели, а кнопка «Ещё вариант» провоцирует жать её подряд.
    """
    await check_rate_limit(request, scope="copilot")
    return CopilotDraft.model_validate(await request.app.state.rt.chat.operator_draft(session_id))


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
async def session_history(request: Request, session_id: str, user: StaffUser) -> list[ChatMessageOut]:
    """Переписка для сотрудника.

    Гостевой эндпоинт читает диалог только с токеном владельца, а у оператора его
    нет и быть не должно, — поэтому у сотрудника свой вход, закрытый ролью.
    """
    rows = await request.app.state.rt.chat.staff_history(session_id)
    return [ChatMessageOut.model_validate(row) for row in rows]


@router.get("/sessions/{session_id}/suggestions", response_model=list[KbHit])
async def session_suggestions(request: Request, session_id: str, user: StaffUser) -> list[KbHit]:
    """Подсказки из базы знаний к последнему вопросу клиента — при открытии обращения."""
    hits = await request.app.state.rt.chat.operator_suggestions(session_id)
    return [KbHit.model_validate(hit) for hit in hits]


# Пятнадцати мегабайт хватает на скриншот и на инструкцию в PDF; больше — это
# уже файлообменник, а его роль лучше отдать почте.
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
# Явный список: всё, что оператор может понадобиться показать. Разрешать по
# принципу «всё, кроме опасного» нельзя — за нас это решает браузер, который
# охотно исполнит .html или .svg, отданный с нашего же домена.
ALLOWED_ATTACHMENTS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
}


@router.post("/sessions/{session_id}/attachment", response_model=AttachmentSent)
async def operator_attachment(  # noqa: PLR0913
    request: Request,
    session_id: str,
    user: StaffUser,
    file: UploadFile = File(...),
    caption: str = Form(""),
    request_id: str | None = Form(default=None, min_length=1, max_length=128),
) -> AttachmentSent:
    """Скриншот или документ от оператора в диалог.

    Тип берём по расширению, а не из заголовка запроса: content-type присылает
    клиент, и доверять ему при последующей отдаче файла нельзя.
    """
    raw = await read_limited(
        file, MAX_ATTACHMENT_BYTES, f"Файл больше {MAX_ATTACHMENT_BYTES // 1024 // 1024} МБ"
    )
    if not raw:
        raise HTTPException(422, "Пустой файл")

    # safe_filename, а не Path().name: на Linux обратный слеш не разделитель, и
    # имя вида «..\..\kb\doc\0000.png» уезжало бы в чужой раздел хранилища.
    name = safe_filename(file.filename or "file")
    content_type = ALLOWED_ATTACHMENTS.get(Path(name).suffix.lower())
    if content_type is None:
        raise HTTPException(
            415,
            "Такой формат отправить нельзя. Можно: картинки, PDF, документы Office, txt, csv, zip",
        )

    rt = request.app.state.rt
    # Право на запись — до того, как файл ляжет на диск
    await support_flow.require_access(rt, session_id, user.id, write=True)
    await rt.chat.ensure_session_open_for_upload(session_id)
    file_id = hashlib.sha256((request_id or new_id()).encode() + raw).hexdigest()
    key = f"chat/{session_id}/{file_id}/{name}"
    try:
        url = await rt.media.put(key, raw, content_type)
    except Exception as exc:
        raise HTTPException(503, "Хранилище недоступно, файл не отправлен") from exc

    sent = await rt.chat.operator_attachment(
        session_id,
        user.id,
        StoredFile(key=key, url=url, name=name, size=len(raw), content_type=content_type),
        caption=caption,
        request_id=request_id,
    )
    return AttachmentSent.model_validate(sent)


@router.post("/sessions/{session_id}/close", response_model=Ack)
async def close_session(request: Request, session_id: str, user: StaffUser) -> Ack:
    await support_flow.transition(
        request.app.state.rt,
        session_id,
        user.id,
        "resolve",
        {"reason": "Предложенное решение помогло? Выберите «Помогло» или «Не решено»."},
    )
    return Ack(status="closed")


@router.get("/kb-search", response_model=list[KbHit])
async def kb_search(request: Request, user: StaffUser, q: str) -> list[KbHit]:
    docs = await request.app.state.rt.retriever.search(q, top_k=6)
    return [KbHit.model_validate(hit) for hit in copilot.sources_of(docs)]


@router.get("/quick-replies", response_model=list[QuickReplyOut])
async def quick_replies(request: Request, user: StaffUser) -> list[QuickReplyOut]:
    return await operator_desk.quick_replies(request.app.state.rt.db.session_factory)


@router.post("/quick-replies", response_model=Created)
async def create_quick_reply(request: Request, user: StaffUser, body: QuickReplyIn) -> Created:
    return Created(id=await operator_desk.create_quick_reply(request.app.state.rt.db.session_factory, body))


@router.post("/quick-replies/{reply_id}/use", response_model=Ack)
async def use_quick_reply(request: Request, reply_id: str, user: StaffUser) -> Ack:
    await operator_desk.count_quick_reply_use(request.app.state.rt.db.session_factory, reply_id)
    return Ack(status="ok")


@router.post("/sessions/{session_id}/to-kb", response_model=DraftArticle)
async def dialog_to_kb(request: Request, session_id: str, user: StaffUser, body: ToKBRequest) -> DraftArticle:
    """«Динамическое пополнение»: удачный ответ оператора становится черновиком статьи БЗ."""
    article_id = await articles.create_operator_draft(request.app.state.rt.db.session_factory, body)
    return DraftArticle(
        article_id=article_id,
        status="draft",
        note="Статья появится в поиске после публикации администратором",
    )


ws_router = APIRouter()


async def _keep_online(cache: Redis, operator_id: str) -> None:
    """Пока вкладка открыта, оператор считается на смене."""
    while True:
        await asyncio.sleep(PRESENCE_BEAT_SECONDS)
        try:
            await mark_online(cache, operator_id)
        except Exception:
            log.exception("Не удалось обновить отметку присутствия оператора")


async def _operator_payload(ws: WebSocket) -> dict[str, Any] | None:
    """Токен оператора из куки сокета; None — соединение уже закрыто с кодом отказа."""
    # Кука ходит на сокет сама, поэтому чужая вкладка могла бы открыть соединение
    # от имени оператора. Origin у WebSocket браузер подделать не даёт — на нём и
    # держим проверку; CSRF-заголовок для рукопожатия выставить нельзя.
    settings = get_settings()
    if not origin_allowed(
        ws.headers.get("origin"), ws.headers.get("host", ""), settings.allowed_origins_list
    ):
        await ws.close(code=4403)
        return None
    token = ws.cookies.get(AUTH_COOKIE, "")
    try:
        payload = decode_token(token)
        if payload.get("role") not in ("operator", "admin"):
            raise ValueError("роль без доступа")
    except Exception:  # noqa: BLE001
        await ws.close(code=4401)
        return None
    return payload


@ws_router.websocket("/ws/operator")
async def operator_ws(ws: WebSocket) -> None:  # noqa: C901, PLR0915
    rt = ws.app.state.rt
    payload = await _operator_payload(ws)
    if payload is None:
        return
    await ws.accept()
    await rt.ws.join(OPERATORS_ROOM, ws)
    # Личная комната: сюда приходит уведомление о назначенном обращении. Плюс
    # отметка «на смене» — диспетчер раздаёт только тем, у кого открыта операторская.
    operator_id = str(payload["sub"])

    async def authorize_delivery(room: str, event: dict[str, Any]) -> bool:
        sid = room.split(":", 1)[1] if room.startswith(("session:", "copilot:")) else event.get("session_id")
        if not sid:
            return True
        try:
            await support_flow.require_access(rt, str(sid), operator_id)
        except HTTPException:
            return False
        return True

    ws.state.support_authorize = authorize_delivery
    personal_room = f"operator:{operator_id}"
    await rt.ws.join(personal_room, ws)
    await mark_online(rt.cache, operator_id)
    heartbeat = asyncio.create_task(_keep_online(rt.cache, operator_id))
    joined: set[str] = set()
    try:
        while True:
            data = await ws.receive_json()
            kind = data.get("type")
            room = str(data.get("room", ""))
            allowed = room.startswith("session:") or room.startswith("copilot:")
            if kind == "subscribe" and allowed:
                try:
                    await support_flow.require_access(rt, room.split(":", 1)[1], operator_id)
                except HTTPException:
                    await ws.send_json({"type": "error", "message": "Нет доступа к обращению"})
                    continue
                await rt.ws.join(room, ws)
                joined.add(room)
            elif kind == "unsubscribe" and room in joined:
                rt.ws.leave(room, ws)
                joined.discard(room)
            elif kind == "typing" and room.startswith("session:") and room in joined:
                try:
                    await support_flow.require_access(rt, room.split(":", 1)[1], operator_id, write=True)
                except HTTPException:
                    continue
                # В комнату диалога, а не гостю напрямую: гость слушает её же.
                # Дедупликация и «погасить через пару секунд» — забота фронта
                # обеих сторон, сюда долетает уже throttled сигнал.
                await rt.ws.publish(room, {"type": "operator_typing"})
    except WebSocketDisconnect:
        pass
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        rt.ws.leave(OPERATORS_ROOM, ws)
        rt.ws.leave(personal_room, ws)
        for room in joined:
            rt.ws.leave(room, ws)
        # Вкладок может быть несколько: снимаем со смены, только когда закрылась
        # последняя — иначе закрытая вторая вкладка выкидывала бы человека.
        if not rt.ws.has_room(personal_room):
            await mark_offline(rt.cache, operator_id)
