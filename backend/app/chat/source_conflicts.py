"""Use current, relevant conflict evidence before any answer text reaches the user."""

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import SQLAlchemyError

from app.schemas.source_conflicts import ConflictResult, SourceSnapshot
from app.services import wordforms
from app.services.source_conflicts import SourceConflictRegistry
from app.services.verbatim import is_same_question

if TYPE_CHECKING:
    from app.chat.turn import Turn
    from app.services.chat import ChatService

log = logging.getLogger(__name__)
_LOOKUP_TIMEOUT_S = 1.0
_MIN_FACT_TERMS = 2
_QUOTE_OVERLAP = 0.5
_NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")
_MEASUREMENTS = re.compile(r"(\d+(?:[.,]\d+)?)\s*([а-яёa-z%]+)", re.IGNORECASE)
_SPACE = re.compile(r"\s+")
_WORDS = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
_MIN_WORD_CHARS = 3


def _terms(text: str) -> set[str]:
    # Extending a validity period asks about that period even without the word "срок".
    return {
        "срок" if word.startswith("продл") else wordforms.stem(word)
        for word in _WORDS.findall(text.casefold())
        if len(word) >= _MIN_WORD_CHARS and word not in wordforms.STOPWORDS
    }


def _measurements(text: str) -> set[tuple[str, str]]:
    return {(number.replace(",", "."), wordforms.stem(unit)) for number, unit in _MEASUREMENTS.findall(text)}


def _quote_used(quote: str, answer: str) -> bool:
    if not quote.strip():
        return False
    if _SPACE.sub(" ", quote.casefold()).strip() in _SPACE.sub(" ", answer.casefold()):
        return True
    quote_numbers = set(_NUMBERS.findall(quote))
    if quote_numbers:
        if not quote_numbers <= set(_NUMBERS.findall(answer)):
            return False
        measurements = _measurements(quote)
        if measurements:
            # The same numeral in another claim (12 days vs 12 months) is not evidence.
            return measurements <= _measurements(answer)
    words = _terms(quote)
    return len(words) >= _MIN_FACT_TERMS and len(words & _terms(answer)) / len(words) >= _QUOTE_OVERLAP


def applicable(result: ConflictResult, question: str, answer: str, cited_ids: set[str]) -> bool:
    # Labels can be translated by the model; the common subject of the original
    # evidence quotes is also grounded scope, unlike the entire retrieved document.
    fact = _terms(result.assessment.fact) | (
        _terms(result.assessment.left_quote) & _terms(result.assessment.right_quote)
    )
    shared = fact & _terms(question)
    if len(shared) < _MIN_FACT_TERMS:
        return False
    return any(
        source.article_id in cited_ids and _quote_used(quote, answer)
        for source, quote in (
            (result.left, result.assessment.left_quote),
            (result.right, result.assessment.right_quote),
        )
    )


async def maybe_conflict_reply(
    svc: ChatService, turn: Turn, answer: str, citations: list[dict[str, Any]]
) -> dict[str, Any] | None:
    cited_ids = {str(citation["article_id"]) for citation in citations if citation.get("article_id")}
    if not cited_ids:
        return None
    try:
        async with asyncio.timeout(_LOOKUP_TIMEOUT_S):
            results = await SourceConflictRegistry(svc.rt.db.session_factory).get_current_conflicts(
                sorted(cited_ids), turn.role, limit=20
            )
    except TimeoutError, SQLAlchemyError:
        # Existing answering remains available; metadata distinguishes an unavailable check from no conflicts.
        turn.flags["source_conflict_check"] = "unavailable"
        log.warning("Source conflict lookup unavailable")
        return None
    result = next((item for item in results if applicable(item, turn.text, answer, cited_ids)), None)
    if result is None:
        return None
    return await _reply(svc, turn, result)


async def maybe_ambiguous_conflict(svc: ChatService, turn: Turn) -> dict[str, Any] | None:
    """An exact question with conflicting sources should not become duplicate topic choices."""
    if not turn.probe:
        return None
    top = turn.probe[0]
    if not is_same_question(turn.text, top.question):
        return None
    return await maybe_conflict_reply(
        svc, turn, top.answer, [{"article_id": top.article_id, "question": top.question, "score": top.score}]
    )


async def _reply(svc: ChatService, turn: Turn, result: ConflictResult) -> dict[str, Any]:
    preferred = result.preferred_source or next(
        (
            source
            for source in (result.left, result.right)
            if source.article_id == result.preferred_article_id
        ),
        None,
    )
    if preferred is not None:
        return await _preferred_reply(svc, turn, result, preferred)

    answer = (
        "В источниках найдены разные ответы на этот вопрос; требуется проверка.\n\n"
        f"В одном источнике: «{result.assessment.left_quote}»\n\n"
        f"В другом источнике: «{result.assessment.right_quote}»\n\n"
        "По этим данным я не могу уверенно выбрать действующее правило. Позвать оператора для уточнения?"
    )
    citations = [
        {"article_id": source.article_id, "question": source.question, "score": 0.0}
        for source in (result.left, result.right)
    ]
    latency_ms = int((time.perf_counter() - turn.started) * 1000)
    meta = {
        **turn.flags,
        "question": turn.text,
        "reply_to": turn.message_id,
        "audience": turn.role,
        "page_context": turn.page_text,
        "answered": False,
        "confidence": 0.0,
        "escalated": False,
        "offer_operator": True,
        "retrieval_outcome": "source_conflict",
        "source_conflict_ids": [result.id],
        "tool_trace": ["source_conflict_registry"],
        "latency_ms": latency_ms,
    }
    await svc._stream_answer(turn.session_id, answer)
    msg = await svc._save_message(turn.session_id, "assistant", answer, citations=citations, meta=meta)
    await svc.rt.ws.publish(f"session:{turn.session_id}", svc._msg_event(msg))
    await svc._track_query(turn.text)
    # Evidence exists: this is neither an answered query nor a knowledge-base gap.
    return {
        "status": "no_answer",
        "answer": answer,
        "citations": citations,
        "confidence": 0.0,
        "escalated": False,
        "latency_ms": latency_ms,
    }


async def _preferred_reply(
    svc: ChatService, turn: Turn, result: ConflictResult, preferred: SourceSnapshot
) -> dict[str, Any]:
    """Use the source selected by an admin without another model request."""
    answer = preferred.answer
    citations = [{"article_id": preferred.article_id, "question": preferred.question, "score": 1.0}]
    latency_ms = int((time.perf_counter() - turn.started) * 1000)
    meta = {
        **turn.flags,
        "question": turn.text,
        "reply_to": turn.message_id,
        "audience": turn.role,
        "page_context": turn.page_text,
        "answered": True,
        "confidence": 1.0,
        "escalated": False,
        "offer_operator": False,
        "retrieval_outcome": "source_conflict_resolved",
        "source_conflict_ids": [result.id],
        "preferred_article_id": preferred.article_id,
        "tool_trace": ["source_conflict_registry"],
        "latency_ms": latency_ms,
    }
    await svc._stream_answer(turn.session_id, answer)
    msg = await svc._save_message(turn.session_id, "assistant", answer, citations=citations, meta=meta)
    await svc.rt.ws.publish(f"session:{turn.session_id}", svc._msg_event(msg))
    await svc._track_query(turn.text)
    return {
        "status": "answered",
        "answer": answer,
        "citations": citations,
        "confidence": 1.0,
        "escalated": False,
        "latency_ms": latency_ms,
    }
