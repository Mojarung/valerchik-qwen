"""Раздел портала, с которого задан вопрос.

Короткие вопросы («как отменить», «где посмотреть статус») без контекста
неразрешимы: отменить можно и оферту, и котировочную сессию, и заявку. Страница,
на которой человек стоит, обычно и есть недостающая половина вопроса.

Данные приходят со страницы чужого сайта через postMessage — то есть полностью
под контролем встраивающего. Поэтому здесь граница доверия: обрезаем, чистим и
никогда не берём query-строку.
"""

import re
from typing import Any

# Управляющие символы: попадают в логи и в промпт модели, где ломают разметку
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

MAX_PATH = 200
MAX_TITLE = 120


def clean(page: object) -> dict[str, Any]:
    """-> {"path": ..., "title": ...} либо {}, если контекста нет или он мусорный."""
    if not isinstance(page, dict):
        return {}
    path = _clean_value(page.get("path"), MAX_PATH)
    title = _clean_value(page.get("title"), MAX_TITLE)
    # Query-строку отрезаем даже если виджет её прислал: портал кладёт туда
    # номера заявок, ИНН и идентификаторы документов — чужие персональные
    # данные, которым не место ни в нашей базе, ни в логах, ни в промпте.
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path and not title:
        return {}
    return {"path": path, "title": title}


def _clean_value(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return _CONTROL.sub(" ", value).strip()[:limit]


def as_text(page: dict[str, Any]) -> str:
    """Строка для подмешивания в ранжирование.

    Заголовок несёт почти весь смысл: пути на портале латинские, а база знаний
    русская, и пересечься им почти негде. Путь оставляем — он помогает на
    разделах, чьи слова совпадают с терминами («/kotirovochnye-sessii»).
    """
    if not page:
        return ""
    return " ".join(part for part in (page.get("title", ""), page.get("path", "")) if part)
