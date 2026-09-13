"""Ошибка диалога.

Отдельным модулем, а не в services/chat.py: её поднимают все зоны
ответственности, а сам services/chat.py их импортирует — держи исключение там,
получился бы круговой импорт.
"""

from __future__ import annotations


class ChatError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
