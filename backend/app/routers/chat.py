"""Пользовательский чат: REST + WebSocket (live-режим)."""

import logging
from pathlib import Path

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)

from app.adapters.storage import safe_filename
from app.chat import answer_recovery, feedback_flow
from app.chat.errors import ChatError
from app.middleware import CHAT_MESSAGES, check_rate_limit, rate_limit_exceeded
from app.models import new_id
from app.schemas import (
    AudienceRequest,
    ChatMessageRequest,
    FeedbackRequest,
    SessionCreateRequest,
)
from app.schemas.answer_feedback import AnswerRatingRequest, AnswerRatingSaved, AnswerRecoveryRequest
from app.schemas.chat import (
    AttachmentSent,
    AudienceState,
    ChatMessageOut,
    ChatReply,
    ContractSummarySent,
    FeedbackSaved,
    SessionCreated,
    SessionHistoryRequest,
    SessionState,
    SessionSummary,
    SourceArticle,
)
from app.security import CHAT_WS_PROTOCOL, is_session_owner, owner_from_subprotocols, session_owner_token
from app.services import answer_feedback, articles
from app.services.contract_summary import summarize_contract
from app.services.suggestions import autocomplete
from app.uploads import StoredFile, read_limited

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _require_owner(request: Request, session_id: str) -> None:
    """Писать в диалог может только тот, кто его начал.

    Токен фронт хранит рядом с id в своей куке и присылает заголовком. Чтение
    требует того же доказательства: id уезжает в выгрузку диалогов и в
    /dbadmin, а по нему видна вся переписка с оператором вместе со ссылками на
    вложения. Сокет предъявляет тот же токен подпротоколом: браузерный WebSocket
    не умеет своих заголовков. Query-параметр не принимается нигде — он оседает
    в логах прокси.
    """
    token = request.headers.get("x-session-owner")
    if not is_session_owner(session_id, token):
        raise HTTPException(403, "Этот диалог принадлежит другому посетителю")


@router.post("/sessions", response_model=SessionCreated)
async def create_session(request: Request, body: SessionCreateRequest | None = None) -> SessionCreated:
    await check_rate_limit(request, scope="session")
    created = await request.app.state.rt.chat.create_session(
        audience=body.audience if body else None,
    )
    return SessionCreated(**created, owner=session_owner_token(created["session_id"]))


@router.get("/sessions/{session_id}", response_model=SessionState)
async def resume_session(request: Request, session_id: str) -> SessionState:
    """Возврат к прежнему диалогу после перезагрузки или закрытой вкладки."""
    _require_owner(request, session_id)
    return SessionState.model_validate(await request.app.state.rt.chat.resume_session(session_id))


@router.post("/sessions/history", response_model=list[SessionSummary])
async def session_history(request: Request, body: SessionHistoryRequest) -> list[SessionSummary]:
    """Раздел «Мои обращения»: карточки прошлых диалогов, накопленных браузером.

    Регистрации у чата нет — фронт присылает все пары (id, токен владельца),
    что накопил localStorage. Пара с неверным или чужим токеном тихо выпадает
    из ответа (см. ChatService.list_session_summaries), а не роняет запрос
    403-м: иначе одна подделанная запись прятала бы все настоящие обращения.
    """
    # Свой счётчик, а не общий с созданием диалога: список перечитывается на
    # каждое переключение обращения и по таймеру, и с общей квотой пара десятков
    # обновлений запрещала завести новое обращение, а сам список замирал на 429.
    await check_rate_limit(request, scope="history")
    pairs = [(item.session_id, item.owner) for item in body.sessions]
    summaries = await request.app.state.rt.chat.list_session_summaries(pairs)
    return [SessionSummary.model_validate(s) for s in summaries]


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
async def get_messages(request: Request, session_id: str) -> list[ChatMessageOut]:
    _require_owner(request, session_id)
    rows = await request.app.state.rt.chat.get_messages(session_id)
    return [ChatMessageOut.model_validate(row) for row in rows]


@router.post("/sessions/{session_id}/messages", response_model=ChatReply)
async def post_message(request: Request, session_id: str, body: ChatMessageRequest) -> ChatReply:
    await check_rate_limit(request, scope="chat")
    _require_owner(request, session_id)
    result = await request.app.state.rt.chat.handle_user_message(
        session_id,
        body.text,
        body.page.model_dump() if body.page else None,
        audience=body.audience,
        request_id=body.request_id,
    )
    CHAT_MESSAGES.labels(result.get("status", "unknown")).inc()
    return ChatReply.model_validate(result)


@router.post("/sessions/{session_id}/audience", response_model=AudienceState)
async def set_audience(request: Request, session_id: str, body: AudienceRequest) -> AudienceState:
    """Переключатель «поставщик / заказчик» посреди диалога: роль запоминается
    у диалога, в ответ — подсказки под новую роль."""
    _require_owner(request, session_id)
    return AudienceState.model_validate(
        await request.app.state.rt.chat.set_audience(session_id, body.audience)
    )


# Скриншот с экрана — это сотни килобайт; десяти мегабайт хватает с запасом,
# а больше принимать от неавторизованного посетителя незачем.
MAX_USER_IMAGE_BYTES = 10 * 1024 * 1024
# Только изображения: описать ошибку картинкой — законный сценарий, принимать
# от анонима документы и архивы — нет.
USER_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@router.post("/sessions/{session_id}/attachment", response_model=AttachmentSent)
async def post_attachment(request: Request, session_id: str, file: UploadFile = File(...)) -> AttachmentSent:
    """Скриншот от пользователя в диалог с оператором."""
    await check_rate_limit(request, scope="chat")
    _require_owner(request, session_id)
    raw = await read_limited(
        file, MAX_USER_IMAGE_BYTES, f"Картинка больше {MAX_USER_IMAGE_BYTES // 1024 // 1024} МБ"
    )
    if not raw:
        raise HTTPException(422, "Пустой файл")

    name = safe_filename(file.filename or "screenshot.png", "screenshot.png")
    content_type = USER_IMAGE_TYPES.get(Path(name).suffix.lower())
    if content_type is None:
        raise HTTPException(415, "Прикрепить можно только изображение: png, jpg, gif или webp")

    rt = request.app.state.rt
    # Право на запись проверяем ДО того, как что-то ляжет на диск: иначе файл
    # сохранялся даже для несуществующего диалога, и отказ приходил постфактум —
    # то есть загрузка была открыта кому угодно.
    await rt.chat.ensure_session_open_for_upload(session_id)

    key = f"chat/{session_id}/{new_id()}/{name}"
    try:
        url = await rt.media.put(key, raw, content_type)
    except Exception as exc:
        raise HTTPException(503, "Хранилище недоступно, картинка не отправлена") from exc

    sent = await rt.chat.user_attachment(
        session_id, StoredFile(key=key, url=url, name=name, size=len(raw), content_type=content_type)
    )
    return AttachmentSent.model_validate(sent)


# Контракты по закупке — это десятки страниц со сканами приложений, а не
# скриншот ошибки: лимит соответственно выше, чем у картинки.
MAX_USER_DOCUMENT_BYTES = 20 * 1024 * 1024
# Только то, чем обычно присылают контракт. Остальные форматы документов сам
# разбор (markitdown) тоже понимает, но сценарий здесь один — выжимка по
# контракту закупки, а не общая загрузка файлов в диалог.
USER_DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}


@router.post("/sessions/{session_id}/document", response_model=ContractSummarySent)
async def post_document(
    request: Request, session_id: str, file: UploadFile = File(...)
) -> ContractSummarySent:
    """Контракт от пользователя -> короткая выжимка в диалоге вместо самостоятельного чтения.

    Отдельная ручка от /attachment: там — скриншот для оператора, здесь —
    документ, который разбирает и пересказывает модель. Смешивать сценарии
    значило бы разрешить постороннему заливать в диалог с оператором PDF под
    видом картинки или наоборот.
    """
    await check_rate_limit(request, scope="chat")
    _require_owner(request, session_id)
    raw = await read_limited(
        file, MAX_USER_DOCUMENT_BYTES, f"Файл больше {MAX_USER_DOCUMENT_BYTES // 1024 // 1024} МБ"
    )
    if not raw:
        raise HTTPException(422, "Пустой файл")

    name = safe_filename(file.filename or "document.pdf", "document.pdf")
    if Path(name).suffix.lower() not in USER_DOCUMENT_TYPES:
        raise HTTPException(415, "Прикрепить можно только документ: pdf, docx или doc")

    rt = request.app.state.rt
    # Право на запись проверяем ДО разбора файла: иначе минуты разбора
    # тратились бы и на диалог, куда писать уже нельзя (см. /attachment).
    await rt.chat.ensure_session_open_for_document(session_id)

    try:
        summary = await summarize_contract(rt, name, raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        log.exception("Не удалось разобрать документ %s", name)
        raise HTTPException(502, "Не удалось разобрать документ, попробуйте другой файл") from exc

    sent = await rt.chat.contract_summary(session_id, name, summary)
    return ContractSummarySent.model_validate(sent)


@router.post("/sessions/{session_id}/messages/{message_id}/rating", response_model=AnswerRatingSaved)
async def rate_message(
    request: Request, session_id: str, message_id: str, body: AnswerRatingRequest
) -> AnswerRatingSaved:
    _require_owner(request, session_id)
    return await answer_feedback.rate(request.app.state.rt, session_id, message_id, body)


@router.post("/sessions/{session_id}/messages/{message_id}/recovery", response_model=ChatReply)
async def recover_answer(
    request: Request, session_id: str, message_id: str, body: AnswerRecoveryRequest
) -> ChatReply:
    _require_owner(request, session_id)
    await check_rate_limit(request, scope="chat")
    result = await answer_recovery.recover(request.app.state.rt.chat, session_id, message_id, body)
    return ChatReply.model_validate(result)


@router.post("/sessions/{session_id}/feedback", response_model=FeedbackSaved)
async def post_feedback(request: Request, session_id: str, body: FeedbackRequest) -> FeedbackSaved:
    _require_owner(request, session_id)
    # Напрямую в feedback_flow, а не через фасад ChatService: ответы на вопросы
    # «решено?» и «понятно?» фасад пока не пробрасывает.
    saved = await feedback_flow.add_feedback(
        request.app.state.rt.chat,
        session_id,
        body.rating,
        body.comment,
        feedback_flow.Answers(solved=body.solved, clear=body.clear),
    )
    return FeedbackSaved.model_validate(saved)


@router.get("/suggest", response_model=list[str])
async def suggest(request: Request, q: str = "") -> list[str]:
    return await autocomplete(request.app.state.rt, q)


@router.get("/articles/{article_id}", response_model=SourceArticle)
async def get_source_article(request: Request, article_id: str) -> SourceArticle:
    """Статья-источник для просмотра из цитаты в чате (только активные)."""
    article = await articles.public_article(request.app.state.rt.db.session_factory, article_id)
    if article is None:
        raise HTTPException(404, "Статья не найдена или снята с публикации")
    return article


ws_router = APIRouter()


@ws_router.websocket("/ws/chat/{session_id}")
async def chat_ws(ws: WebSocket, session_id: str) -> None:
    rt = ws.app.state.rt
    # Сокет двунаправленный: без проверки владельца тот, кто узнал id, читал
    # диалог в реальном времени (включая реплики оператора и ссылки на
    # вложения) и писал сообщения от имени заявителя. Токен идёт подпротоколом
    # `owner.<токен>`: браузерный WebSocket не умеет своих заголовков, а адрес с
    # токеном оседал бы в логах прокси и в истории браузера.
    offered = ws.scope.get("subprotocols") or []
    if CHAT_WS_PROTOCOL not in offered or not is_session_owner(session_id, owner_from_subprotocols(offered)):
        await ws.close(code=4403)
        return
    await ws.accept(subprotocol=CHAT_WS_PROTOCOL)
    room = f"session:{session_id}"
    await rt.ws.join(room, ws)
    try:
        while True:
            data = await ws.receive_json()
            kind = data.get("type")
            if kind == "message":
                text = str(data.get("text", ""))[:4000]
                # Лимит общий с REST-путём: без него достаточно было перейти на
                # сокет, чтобы ограничение перестало работать, — а сообщение
                # стоит одинаково, включая guardrails и вызов LLM.
                denied = await rate_limit_exceeded(ws, scope="chat")
                if denied:
                    await ws.send_json({"type": "error", "detail": denied})
                    continue
                try:
                    result = await rt.chat.handle_user_message(
                        session_id,
                        text,
                        data.get("page"),
                        audience=data.get("audience"),
                        request_id=data.get("request_id"),
                    )
                    CHAT_MESSAGES.labels(result.get("status", "unknown")).inc()
                except (ChatError, HTTPException) as exc:
                    await ws.send_json({"type": "error", "detail": exc.detail})
            elif kind == "typing":
                await rt.ws.publish(
                    f"copilot:{session_id}", {"type": "user_typing", "text": data.get("text", "")[:200]}
                )
    except WebSocketDisconnect:
        pass
    finally:
        rt.ws.leave(room, ws)
