"""Реальные формулировки вопроса статьи: как его на самом деле задавали люди.

У статьи один вопрос, а у ответа из выгрузки поддержки — десятки обращений, на
которые оператор его давал: «не могу подписать УПД», «УПД не уходит заказчику»,
«ошибка при подписании документа о приёмке». Следующий заявитель напишет
пятым способом, и одного вопроса статьи поиску мало, чтобы его узнать.

Формулировки живут в происхождении статьи (KBArticle.origin["phrasings"]): это
JSON, схема базы от них не меняется, а редактор статьи их не трогает — он
переписывает вопрос, ответ и ключевые слова, но не происхождение. Поиск
использует их дважды: каждая — отдельная точка векторного индекса со ссылкой на
статью, и все вместе — слова статьи в лексическом индексе.
"""

from __future__ import annotations

from typing import Any

ORIGIN_KEY = "phrasings"
# Больше не бывает и при ручной правке JSON: каждая формулировка — отдельный
# вектор, и статья на сотню формулировок заняла бы выдачу одна
LIMIT = 12


def of(origin: dict[str, Any] | None) -> list[str]:
    """Формулировки статьи из её происхождения; пусто — у статьи их нет."""
    raw = (origin or {}).get(ORIGIN_KEY) or []
    if not isinstance(raw, list):
        return []
    texts = [" ".join(str(text).split()) for text in raw if isinstance(text, str)]
    return list(dict.fromkeys(text for text in texts if text))[:LIMIT]


def extra(phrasings: list[str], present: list[str]) -> list[str]:
    """Формулировки, которых среди present ещё нет (вопрос, ключевые слова):
    повтор той же строки в лексическом индексе только удвоил бы её вес."""
    seen = {text.casefold() for text in present}
    return [text for text in phrasings if text.casefold() not in seen]
