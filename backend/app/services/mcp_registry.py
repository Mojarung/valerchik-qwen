"""Реестр MCP-серверов вне HTTP-слоя: список, заведение, правка, удаление."""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditLog, McpServer
from app.schemas import McpServerIn
from app.schemas.admin import McpServerOut, McpStatus, McpTool

SessionFactory = async_sessionmaker[AsyncSession]


def _out(server: McpServer) -> McpServerOut:
    # Значения заголовков наружу не отдаём никому, включая админа: там боевые
    # ключи внешних систем, а список нужен, чтобы видеть, какие заголовки
    # заданы, — не чтобы их прочитать. Тот же принцип уже применён к llm.api_key.
    # Раньше их целиком получал и оператор: чтение было открыто StaffUser,
    # тогда как запись правильно закрыта AdminUser.
    return McpServerOut(
        id=server.id,
        name=server.name,
        url=server.url,
        description=server.description,
        enabled=server.enabled,
        headers={key: "***" for key in (server.headers or {})},
        timeout_s=server.timeout_s,
        last_status=cast(McpStatus, server.last_status),
        last_error=server.last_error,
        tools=[McpTool.model_validate(tool) for tool in server.tools or []],
        checked_at=server.checked_at.isoformat() if server.checked_at else None,
    )


async def servers(session_factory: SessionFactory) -> list[McpServerOut]:
    async with session_factory() as db:
        rows = (await db.execute(select(McpServer).order_by(McpServer.name))).scalars().all()
    return [_out(row) for row in rows]


async def server(session_factory: SessionFactory, server_id: str) -> McpServerOut | None:
    async with session_factory() as db:
        row = await db.get(McpServer, server_id)
    return _out(row) if row is not None else None


async def create(session_factory: SessionFactory, body: McpServerIn, actor: str) -> str | None:
    """Завести сервер; возвращает id, None — имя уже занято."""
    async with session_factory() as db:
        exists = (await db.execute(select(McpServer).where(McpServer.name == body.name))).scalar_one_or_none()
        if exists is not None:
            return None
        row = McpServer(**body.model_dump())
        db.add(row)
        db.add(AuditLog(actor=actor, action="mcp.create", entity=body.name, payload={"url": body.url}))
        await db.commit()
        return row.id


async def update(
    session_factory: SessionFactory, server_id: str, changes: dict[str, Any], actor: str
) -> bool:
    """Переписать поля сервера; False — такого сервера нет."""
    async with session_factory() as db:
        row = await db.get(McpServer, server_id)
        if row is None:
            return False
        for field, value in changes.items():
            setattr(row, field, value)
        db.add(AuditLog(actor=actor, action="mcp.update", entity=row.name, payload=changes))
        await db.commit()
    return True


async def delete(session_factory: SessionFactory, server_id: str, actor: str) -> bool:
    """Убрать сервер из реестра; False — такого сервера нет."""
    async with session_factory() as db:
        row = await db.get(McpServer, server_id)
        if row is None:
            return False
        db.add(AuditLog(actor=actor, action="mcp.delete", entity=row.name, payload={}))
        await db.delete(row)
        await db.commit()
    return True
