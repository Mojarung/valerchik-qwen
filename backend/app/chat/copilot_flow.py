"""Подсказки оператору: карточки статей и черновик ответа.

Работает, когда диалог уже у человека: агент молчит, а оператору к каждому
сообщению клиента прилетают найденные статьи и готовый черновик.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from app.chat.errors import ChatError
from app.services import answer_guard, copilot, kbkind
from app.services.agent import wants_operator

if TYPE_CHECKING:
    from app.services.chat import ChatService

log = logging.getLogger(__name__)


async def _push_copilot(svc: ChatService, session_id: str, text: str, reply_to: str = "") -> None:
    """Подсказки оператору: статьи мгновенно, черновик ответа — следом.

    Разделено намеренно. Поиск по базе знаний занимает десятки миллисекунд, а
    генерация черновика — секунды. Ждать их вместе значит держать оператора
    перед пустой панелью и, что хуже, задерживать ответ самому клиенту: этот
    метод вызывается внутри обработки его сообщения, хотя к клиенту черновик
    отношения не имеет. Поэтому карточки уходят сразу, черновик догоняет
    отдельным событием, а отправка сообщения не ждёт LLM вовсе.
    """
    session = await svc._get_session(session_id)
    try:
        docs = await svc.rt.retriever.search(text, top_k=6, audience=session.audience or "")
        docs = [d for d in docs if d.kind != "operator_case"]
    except Exception:
        log.exception("Подсказки недоступны")
        docs = []
    auto = bool(svc.rt.dyn.get("copilot.enabled") and svc.rt.dyn.get("copilot.auto_draft"))
    await svc.rt.ws.publish(
        f"copilot:{session_id}",
        {
            "type": "copilot",
            "suggestions": copilot.sources_of(docs),
            # Похожие обращения — отдельным списком, а не вперемешку со статьями:
            # это не текст для клиента, а подсказка, что проверять и на какой
            # линии такое решали. Вставить отписку по чужой заявке как ответ
            # было бы той же ошибкой, от которой бот уже избавлен.
            "cases": await _case_cards(svc, text),
            "reply_to": reply_to,
            # Панель показывает ожидание ровно тогда, когда черновик действительно
            # готовится. Без этого флага выключенная автогенерация оставляла бы
            # оператора смотреть на вечный спиннер вместо кнопки.
            "draft_pending": auto,
        },
    )
    if auto:
        copilot.spawn(svc._push_draft(session_id, text, reply_to))


async def _push_draft(svc: ChatService, session_id: str, question: str, reply_to: str) -> None:
    """Фоновая генерация черновика с рассылкой в панель оператора.

    Событие уходит при любом исходе, в том числе с пустым черновиком: панель
    ждёт именно его, чтобы снять ожидание, и молчание в ответ на ошибку
    выглядело бы для оператора зависанием.
    """
    from app.services.support_copilot import payload

    data = {"draft": "", "generated": False, "status": "error", "error": "Повторите генерацию"}
    try:
        draft = await svc._make_draft(session_id, question, reply_to=reply_to)
        data = payload(draft)
    except Exception:
        log.exception("Черновик оператору не сгенерирован, сессия %s", session_id)
    await svc.rt.ws.publish(
        f"copilot:{session_id}",
        {
            "type": "copilot_draft",
            **data,
            "reply_to": reply_to,
        },
    )


async def _make_draft(
    svc: ChatService, session_id: str, question: str, *, reply_to: str = ""
) -> copilot.CopilotDraft:
    from app.services.support_copilot import generate

    return await generate(svc.rt, session_id, question, reply_to)


_QUESTION_PARTS = re.compile(r"[,;:\n]|(?<=[!?])\s+")
_MIN_QUESTION_WORDS = 2


def _substantive_question(text: str) -> str:
    """Keep the question next to an operator request, but drop a bare request."""
    text = text.strip()
    if not wants_operator(text):
        return text
    # A visitor commonly sends both intents in one message: "call an operator,
    # how do I register?". Treating the whole message as a bare hand-off made
    # the copilot claim there was no client question even though it was visible
    # in the conversation.
    parts = [part.strip(" .!?-") for part in _QUESTION_PARTS.split(text)]
    return next(
        (
            part
            for part in parts
            if part and len(part.split()) >= _MIN_QUESTION_WORDS and not wants_operator(part)
        ),
        "",
    )


def _last_client_question(messages: list[dict[str, Any]]) -> str:
    """Последний вопрос клиента по существу — то, на что сейчас отвечает оператор.

    Просьбу позвать человека пропускаем: искать по ней в базе знаний нечего, а
    перед эскалацией она почти всегда последняя, и подсказки с черновиком
    строились бы по «хочу оператора», а не по вопросу клиента.
    """
    for message in reversed(messages):
        if message["role"] != "user":
            continue
        if question := _substantive_question(message["content"]):
            return question
    return ""


async def operator_draft(svc: ChatService, session_id: str) -> dict[str, Any]:
    """Черновик по запросу оператора: кнопка «Ещё вариант» и режим без автогенерации.

    Отвечаем синхронно, а не событием в комнату: оператор нажал и ждёт, и
    показать ему спиннер на месте кнопки честнее, чем оставить панель молчать
    до прихода сообщения по сокету.
    """
    if not svc.rt.dyn.get("copilot.enabled"):
        raise ChatError(409, "Копайлот отключён в настройках")
    session = await svc._get_session(session_id)
    if session.status not in ("waiting_operator", "operator_assigned", "operator_active", "waiting_user"):
        raise ChatError(409, "Диалог не в работе у оператора")
    question = _last_client_question(await svc.get_messages(session_id))
    if not question:
        raise ChatError(422, "В диалоге ещё нет вопроса клиента")
    draft = await svc._make_draft(session_id, question)
    from app.services.support_copilot import payload

    return payload(draft)


async def operator_suggestions(svc: ChatService, session_id: str) -> list[dict[str, Any]]:
    """Карточки статей к последнему вопросу клиента — для обращения, которое оператор открыл.

    Живые подсказки приходят событием на каждое новое сообщение клиента. Обращение,
    открытое из очереди, новых сообщений может не получить вовсе, и колонка подсказок
    стояла пустой ровно тогда, когда оператор впервые читает вопрос.
    """
    session = await svc._get_session(session_id)
    history = await svc.get_messages(session_id)
    question = _last_client_question(history)
    if not question:
        return []
    context = "\n".join(m["content"] for m in history[-12:] if m["role"] in ("user", "operator"))
    return copilot.sources_of(
        await svc.rt.retriever.search(question, top_k=6, context=context, audience=session.audience or "")
    )


# Сколько похожих обращений показывать оператору: больше трёх — уже не
# подсказка, а поиск по архиву, для которого у него есть своё поле
CASE_HINTS = 3


async def _case_cards(svc: ChatService, text: str) -> list[dict[str, Any]]:
    """Похожие обращения, которые решали специалисты, — «как решали похожее».

    Отдельно от статей и с пометкой вида: это отчёт о действиях по чужой заявке,
    клиенту его не вставишь. Зато видно, что проверять и на какой линии такое
    закрывали. Ниже порога ответа совпадение — шум, как и у статей. Сбой поиска
    не должен лишать оператора обычных подсказок: тогда просто пустой список.
    """
    if not text.strip():
        return []
    try:
        cases = await svc.rt.retriever.search_cases(text, top_k=CASE_HINTS)
    except Exception:
        log.exception("Похожие обращения для оператора не найдены")
        return []
    floor = float(svc.rt.dyn.get("agent.confidence_threshold"))
    return [
        {
            "article_id": case.article_id,
            "question": case.question,
            "answer": answer_guard.sanitize(case.answer),
            "score": case.score,
            "category": case.category,
            "line": case.line,
            "kind": kbkind.OPERATOR_CASE,
        }
        for case in cases
        if case.score >= floor
    ]


async def operator_case_suggestions(svc: ChatService, session_id: str) -> list[dict[str, Any]]:
    """Похожие обращения к последнему вопросу клиента — пара к operator_suggestions.

    Отдельный метод, а не лишние карточки в operator_suggestions: ответ той ручки
    строго описан схемой статьи, и карточка другого вида с полями line и kind в
    ней не прошла бы проверку.
    """
    await svc._get_session(session_id)
    question = _last_client_question(await svc.get_messages(session_id))
    return await _case_cards(svc, question)
