"""Контракт от пользователя: короткая выжимка вместо самостоятельного чтения документа.

Разбор файла и вызов модели живут в `app/services/contract_summary.py` — здесь
только запись результата в переписку, тем же способом, что и вложение-скриншот
в `operator_flow.py`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.chat.errors import ChatError

if TYPE_CHECKING:
    from app.services.chat import ChatService

log = logging.getLogger(__name__)


async def ensure_session_open_for_document(svc: ChatService, session_id: str) -> None:
    """Можно ли загрузить документ в этот диалог.

    В отличие от вложения-скриншота (только пока диалог ведёт оператор),
    контракт на выжимку — обычный сценарий разговора с ботом: загрузить его
    можно в любой момент активного диалога, а не только когда подключился
    человек. Отдельным методом, чтобы роутер спрашивал это ДО разбора файла —
    иначе минуты разбора тратились бы и на диалог, куда писать уже нельзя.
    """
    session = await svc._get_session(session_id)
    if session.status in ("resolved", "closed_violation"):
        raise ChatError(409, "Диалог завершён — создайте новое обращение")


async def contract_summary(svc: ChatService, session_id: str, filename: str, summary: str) -> dict[str, Any]:
    """Документ разобран: реплика пользователя о вложении и ответ с выжимкой.

    Файл в постоянное хранилище не кладём — в отличие от скриншота оператору,
    здесь важен не сам файл, а его содержимое, которое уже пересказано; хранить
    исходный контракт эта задача не требует.
    """
    user_msg = await svc._save_message(
        session_id,
        "user",
        "",
        meta={"attachment": {"name": filename, "kind": "document"}},
    )
    await svc.rt.ws.publish(f"session:{session_id}", svc._msg_event(user_msg))

    bot_msg = await svc._save_message(
        session_id,
        "assistant",
        summary,
        meta={"kind": "contract_summary", "source_file": filename},
    )
    await svc.rt.ws.publish(f"session:{session_id}", svc._msg_event(bot_msg))
    return {"status": "ok", "message_id": bot_msg.id, "summary": summary}
