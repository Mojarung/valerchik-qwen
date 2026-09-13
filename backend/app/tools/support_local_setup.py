"""Sanitize the isolated clone configuration before the first application startup."""

import asyncio
import os

from sqlalchemy import update

from app.db import Database
from app.models import ChatSession, McpServer, SettingEntry, User
from app.security import hash_password


async def main():
    url = os.environ["DATABASE_URL"]
    if "supportflow-postgres" not in url:
        raise RuntimeError("This command only accepts the isolated supportflow-postgres database")
    db = Database(url)
    await db.create_all()
    async with db.session_factory() as session:
        values = {
            "llm.base_url": "",
            "llm.api_key": "",
            "llm.model": "",
            "source_conflicts.auto_enabled": False,
            "copilot.auto_draft": True,
            "operator.autoassign_enabled": True,
            "rate_limit.enabled": False,
        }
        for key, value in values.items():
            row = await session.get(SettingEntry, key)
            if row:
                row.value = {"v": value}
        await session.execute(update(McpServer).values(enabled=False))
        # Clone history remains readable, but imported active cases must not be auto-dispatched.
        await session.execute(
            update(ChatSession)
            .where(
                ChatSession.status.in_(
                    ("waiting_operator", "operator_active", "operator_assigned", "waiting_user")
                )
            )
            .values(status="resolved")
        )
        for name, role, line in [
            ("support_admin", "admin", None),
            ("support_one", "operator", 1),
            ("support_two", "operator", 1),
            ("support_tech", "operator", 2),
            ("support_contracts", "operator", 3),
        ]:
            from sqlalchemy import select

            user = (await session.execute(select(User).where(User.username == name))).scalar_one_or_none()
            if user is None:
                user = User(username=name, password_hash="", role=role, line=line, display_name=name)
                session.add(user)
            user.password_hash = hash_password(
                os.environ["ADMIN_PASSWORD" if role == "admin" else "OPERATOR_PASSWORD"]
            )
        await session.commit()
    await db.dispose()
    print("Isolated clone configured; cloud LLM and external MCP disabled.")


if __name__ == "__main__":
    asyncio.run(main())
