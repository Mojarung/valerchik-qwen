"""Вопрос к куску текста: формулирует LLM, а без неё — заголовок или первая фраза."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.ingest.chunking import _SENTENCE_END, Section
from app.services.retrieval import tokenize

if TYPE_CHECKING:
    from app.dynamic_settings import DynamicSettings

log = logging.getLogger(__name__)

_QUESTION_PROMPT = (
    "Ты составляешь базу знаний службы поддержки Портала поставщиков. "
    "По фрагменту документа сформулируй ОДИН вопрос пользователя, ответ на который "
    "этот фрагмент даёт. Вопрос — на русском, от лица поставщика, без кавычек и "
    "пояснений, не длиннее 90 символов. Верни только сам вопрос."
)


# Заголовок вместо вопроса — в одну строку списка статей
_TITLE_MAX_CHARS = 88


def fallback_question(section: Section) -> str:
    """Вопрос без LLM: заголовок раздела, иначе первое предложение куска.

    Заголовок берём как есть, без приписки «что нужно знать?»: вопрос статьи
    первым идёт в текст для эмбеддинга и в лексическое сравнение, и одинаковый
    хвост у каждой импортированной статьи — это шум, который сближает их все
    между собой и ни одну не приближает к вопросу человека.
    """
    if section.heading:
        return section.heading.rstrip(".:;")
    first = _SENTENCE_END.split(section.body.strip())[0].strip()
    return (
        (first[: _TITLE_MAX_CHARS - 1] + "…")
        if len(first) > _TITLE_MAX_CHARS
        else (first or "Фрагмент документа")
    )


# Ответ модели короче восьми символов — не вопрос, длиннее ста шестидесяти —
# уже пересказ фрагмента
_QUESTION_MIN_CHARS = 8
_QUESTION_MAX_CHARS = 160


async def generate_question(dyn: DynamicSettings, section: Section, timeout_s: float = 30.0) -> str:
    """Вопрос к куску через LLM; при любой осечке — детерминированный запасной вариант."""
    if not (dyn.get("llm.base_url") and dyn.get("llm.model")):
        return fallback_question(section)
    try:
        import asyncio

        from pydantic_ai import Agent

        # Круговой импорт: agent тянет retrieval, поэтому импорт локальный. Внутри
        # try намеренно — обещание «при любой осечке берём заголовок» обязано
        # накрывать и его. Пока импорт стоял строкой выше перехвата, переименование
        # функции в agent.py роняло разбор методички целиком, и ни один тест этого
        # не видел: путь исполняется только при подключённом LLM.
        from app.services.agent import build_model

        agent: Agent[None, str] = Agent(build_model(dyn), instructions=_QUESTION_PROMPT)
        result = await asyncio.wait_for(agent.run(section.text[:2000]), timeout=timeout_s)
        question = (result.output or "").strip().strip('"«»').split("\n")[0]
        if _QUESTION_MIN_CHARS <= len(question) <= _QUESTION_MAX_CHARS:
            return question
    except Exception as exc:  # noqa: BLE001 — наполнение базы важнее одной формулировки
        # тип обязателен: у таймаута пустое сообщение, и без типа в логе остаётся «()»
        log.warning("LLM не сформулировала вопрос (%s: %s), беру заголовок", type(exc).__name__, exc)
    return fallback_question(section)


def extract_keywords(section: Section, limit: int = 8) -> list[str]:
    """Частотные значимые токены куска — их же использует лексическая часть поиска."""
    counts: dict[str, int] = {}
    for token in tokenize(section.text):
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [token for token, _ in ranked[:limit]]
