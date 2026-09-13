"""Виды записей базы знаний: чем запись служит поиску.

Одна таблица статей хранит три разных сущности, и путать их дорого:

- ANSWER — ответ человеку. Им отвечает бот, его цитирует агент.
- OPERATOR_CASE — случай для оператора: так в прошлом решались обращения, где
  ответом был не текст, а действие специалиста («заказчику направлено
  приглашение», «на момент проверки УПД в статусе…»). Отдать такой текст новому
  человеку — соврать ему; зато совпадение с ним — сильный сигнал, что вопрос
  решает оператор, и на какой линии.
- REFERENCE — справочник без ответа (темы и подтемы обращений): полезен для
  рубрик и маршрутизации, но ответом быть не может вовсе — в нём нет инструкции.

Вид живёт в происхождении статьи (KBArticle.origin["kind"]): это JSON, и
существующим статьям без поля ничего не грозит — они ответы, как и были.
"""

from __future__ import annotations

from typing import Any

ANSWER = "answer"
OPERATOR_CASE = "operator_case"
REFERENCE = "reference"
ALL = frozenset({ANSWER, OPERATOR_CASE, REFERENCE})
LABELS = {
    ANSWER: "Ответ",
    OPERATOR_CASE: "Случай для оператора",
    REFERENCE: "Справочник",
}


def of(origin: dict[str, Any] | None) -> str:
    """Вид записи по её происхождению; неизвестное и пустое — ответ."""
    kind = str((origin or {}).get("kind") or ANSWER)
    return kind if kind in ALL else ANSWER


def answers_user(kind: str) -> bool:
    return kind == ANSWER
