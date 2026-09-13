"""Автодополнение и чипсы: вопросы из базы знаний + популярные запросы пользователей."""

from typing import TYPE_CHECKING

from app.chat.knowledge import popular_queries

if TYPE_CHECKING:
    from app.runtime import AppRuntime

_MAX_SUGGESTIONS = 8
# По одной букве совпадает полбазы — подсказываем со второй
_MIN_QUERY_CHARS = 2


def _norm(s: str) -> str:
    return s.lower().replace("ё", "е").strip()


async def autocomplete(rt: AppRuntime, query: str) -> list[str]:
    q = _norm(query)
    if len(q) < _MIN_QUERY_CHARS:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def push(text: str) -> None:
        key = _norm(text)
        if key not in seen and len(out) < _MAX_SUGGESTIONS:
            seen.add(key)
            out.append(text)

    questions = rt.retriever.all_questions()
    for source in (
        (t for t in questions if _norm(t).startswith(q)),
        (t for t in questions if q in _norm(t)),
    ):
        for t in source:
            push(t)

    if len(out) < _MAX_SUGGESTIONS:
        # Только то, что спрашивали многие. Одиночная реплика в подсказках —
        # это чужое сообщение, показанное постороннему; а один настойчивый
        # аноним иначе навязывал бы всем посетителям свою строку («ВНИМАНИЕ,
        # подтвердите оплату…») под видом популярного вопроса.
        for text in await popular_queries(rt.cache, 31):
            if q in _norm(text):
                push(text)
    return out
