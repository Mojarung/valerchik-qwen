"""Внешние MCP-серверы: их инструменты подмешиваются в ReAct-агента.

Реестр серверов лежит в БД и правится из админки — включить, выключить, добавить
новый источник можно на лету, как и всё остальное в сервисе. Инструменты каждого
включённого сервера агент видит наравне со своим `search_kb`.

Главное правило здесь — внешний сервер не имеет права уронить поддержку. Любая
ошибка подключения гасится: сервер молча выпадает из набора инструментов, а
пользователь получает обычный ответ по базе знаний. Поэтому же на подключение
стоит собственный таймаут: недоступный MCP не должен съедать бюджет ответа.
"""

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models import McpServer

if TYPE_CHECKING:
    from pydantic_ai.mcp import MCPToolset
    from pydantic_ai.toolsets import AbstractToolset

    from app.db import SessionFactory

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 12.0


@dataclass
class McpBundle:
    """Готовые к передаче агенту тулсеты и отчёт о том, кто не подключился."""

    toolsets: list[AbstractToolset[Any]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (имя сервера, ошибка)


def _build_toolset(server: McpServer) -> MCPToolset[Any]:
    """MCPToolset для строки реестра. Импорт внутри: группа mcp опциональна."""
    from pydantic_ai.mcp import MCPToolset

    if server.headers:
        from fastmcp.client import Client
        from fastmcp.client.transports import StreamableHttpTransport

        client = Client(StreamableHttpTransport(server.url, headers=dict(server.headers)))
        return MCPToolset(client, id=server.name)
    return MCPToolset(server.url, id=server.name)


async def list_enabled(session_factory: SessionFactory) -> list[McpServer]:
    async with session_factory() as db:
        return list(
            (await db.execute(select(McpServer).where(McpServer.enabled.is_(True)).order_by(McpServer.name)))
            .scalars()
            .all()
        )


async def probe(server: McpServer) -> dict[str, Any]:
    """Проверка подключения: -> {status, tools, error}. Ничего не бросает наружу."""
    try:
        toolset = _build_toolset(server)
    except ImportError as exc:
        return {"status": "error", "tools": [], "error": f"MCP недоступен: {exc}"}
    except Exception as exc:  # noqa: BLE001 — сюда попадает и кривой URL
        return {"status": "error", "tools": [], "error": str(exc)}

    async def _connect() -> list[dict[str, Any]]:
        async with toolset:
            # именно list_tools(): get_tools() требует RunContext агента, которого
            # при проверке из админки нет — там мы просто смотрим, что сервер отдаёт
            return [
                {"name": tool.name, "description": (tool.description or "")[:300]}
                for tool in await toolset.list_tools()
            ]

    try:
        tools = await asyncio.wait_for(_connect(), timeout=server.timeout_s or CONNECT_TIMEOUT_S)
    except TimeoutError:
        return {"status": "error", "tools": [], "error": "таймаут подключения"}
    except Exception as exc:  # noqa: BLE001 — причин отказа у внешнего сервера много
        return {"status": "error", "tools": [], "error": f"{type(exc).__name__}: {exc}"[:400]}
    return {"status": "ok", "tools": tools, "error": ""}


async def refresh_status(session_factory: SessionFactory, server_id: str) -> dict[str, Any]:
    """Проверяет сервер и запоминает результат — админка показывает его в списке."""
    async with session_factory() as db:
        server = await db.get(McpServer, server_id)
        if server is None:
            return {"status": "error", "tools": [], "error": "сервер не найден"}
        result = await probe(server)
        server.last_status = result["status"]
        server.last_error = result["error"]
        server.tools = result["tools"]
        server.checked_at = datetime.now(UTC)
        await db.commit()
    return result


def wants_external(text: str, keywords: list[str] | None) -> bool:
    """Пользователь явно просит внешний источник — подключаемся не спрашивая базу знаний."""
    lowered = text.lower()
    return any(k.strip().lower() in lowered for k in (keywords or []) if k.strip())


def looks_like_product_request(text: str, pattern: str | None) -> bool:
    """Просят найти или купить вещь, а не объясняют правило.

    Отдельный признак нужен потому, что по молчанию базы знаний товарный запрос
    больше не опознаётся: на импортированной базе поиск отвечает хоть чем-нибудь
    на любой вопрос, и порог уверенности не пробивается никогда.

    Битое выражение из админки не должно ронять ответ пользователю: не
    скомпилировалось — считаем, что признака нет, и работаем по остальным.
    """
    if not pattern:
        return False
    try:
        return re.search(pattern, text.lower()) is not None
    except re.error:
        log.warning("mcp.product_intent не компилируется как регулярное выражение: %s", pattern)
        return False


@contextlib.asynccontextmanager
async def mcp_toolsets(session_factory: SessionFactory, active: bool = True) -> AsyncIterator[McpBundle]:
    """Тулсеты всех включённых серверов на время одного ответа агента.

    Каждый сервер подключается отдельно и под своим таймаутом: упавший или
    зависший выпадает из набора, остальные работают.

    `active=False` — пустой набор без единого сетевого запроса: подключение стоит
    секунды, и на вопросах, которые закрывает база знаний, платить за него незачем.
    """
    bundle = McpBundle()
    if not active:
        yield bundle
        return
    try:
        servers = await list_enabled(session_factory)
    except Exception as exc:  # noqa: BLE001 — БЗ важнее реестра интеграций
        log.warning("Не удалось прочитать реестр MCP: %s", exc)
        yield bundle
        return

    async with contextlib.AsyncExitStack() as stack:
        for server in servers:
            try:
                toolset = _build_toolset(server)
                await asyncio.wait_for(
                    stack.enter_async_context(toolset),
                    timeout=server.timeout_s or CONNECT_TIMEOUT_S,
                )
                bundle.toolsets.append(toolset)
            except Exception as exc:  # noqa: BLE001 — внешний сервер не роняет поддержку
                reason = (
                    "таймаут подключения" if isinstance(exc, TimeoutError) else f"{type(exc).__name__}: {exc}"
                )
                log.warning("MCP «%s» не подключён: %s", server.name, reason)
                bundle.failed.append((server.name, reason[:200]))
        yield bundle
