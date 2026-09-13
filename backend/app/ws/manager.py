"""Менеджер WebSocket-комнат с мостом через Redis pub/sub.

Инстансов API может быть несколько (docker compose --scale api=N): событие публикуется
в Redis, каждый инстанс доставляет его своим локальным подключениям. В lite-режиме
fakeredis делает то же самое внутри одного процесса.
"""

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = logging.getLogger(__name__)

BUS_CHANNEL = "ws:bus"
OPERATORS_ROOM = "operators"


class WSManager:
    def __init__(self, cache: Redis) -> None:
        self._cache = cache
        self._rooms: dict[str, set[WebSocket]] = {}
        self._listener_task: asyncio.Task[None] | None = None

    async def join(self, room: str, ws: WebSocket) -> None:
        self._rooms.setdefault(room, set()).add(ws)

    def has_room(self, room: str) -> bool:
        """Есть ли живые локальные подключения. Достаточно для «закрылась ли
        последняя вкладка оператора»: соединения одного человека держит тот же
        инстанс, а отметка присутствия всё равно протухает по таймауту."""
        return bool(self._rooms.get(room))

    def leave(self, room: str, ws: WebSocket) -> None:
        conns = self._rooms.get(room)
        if conns:
            conns.discard(ws)
            if not conns:
                self._rooms.pop(room, None)

    async def _deliver_local(self, room: str, event: dict[str, Any]) -> None:
        for ws in list(self._rooms.get(room, ())):
            try:
                authorize = getattr(ws.state, "support_authorize", None)
                if authorize is not None and not await authorize(room, event):
                    continue
                await ws.send_json(event)
            except Exception:  # noqa: BLE001 — мёртвое соединение просто выкидываем
                self.leave(room, ws)

    async def publish(self, room: str, event: dict[str, Any]) -> None:
        """Опубликовать событие во все инстансы (и локально доставить сразу)."""
        payload = json.dumps({"room": room, "event": event}, ensure_ascii=False, default=str)
        try:
            await self._cache.publish(BUS_CHANNEL, payload)
        except Exception:
            log.exception("Redis pub/sub недоступен, доставка только локальная")
            await self._deliver_local(room, event)

    async def start(self) -> None:
        pubsub = self._cache.pubsub()
        await pubsub.subscribe(BUS_CHANNEL)

        async def _listen() -> None:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    await self._deliver_local(data["room"], data["event"])
                except Exception:
                    log.exception("Плохое событие в ws:bus")

        self._listener_task = asyncio.create_task(_listen())

    async def stop(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
