"""Фоновые задачи внутри процесса API.

Не замена очереди задач: сюда идёт то, что должно пережить возврат ответа
клиенту, но не переживать перезапуск процесса — черновик оператору, пересчёт
отчёта. Всё, что обязано выполниться гарантированно, живёт в taskiq.
"""

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Coroutine

# asyncio держит на задачу только слабую ссылку: без своего множества сборщик
# мусора вправе убить её на середине — тем вероятнее, чем дольше она идёт.
_running: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Фоновая задача, переживающая сборщик мусора."""
    task = asyncio.create_task(coro)
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task


def busy_with(name: str) -> bool:
    """Крутится ли уже задача с таким именем — защита от параллельных запусков."""
    return any(task.get_name() == name and not task.done() for task in _running)
