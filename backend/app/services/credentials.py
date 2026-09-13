"""Проверка логина и пароля сотрудника: одна на вход в интерфейс и в /dbadmin."""

import asyncio
import secrets
from functools import cache

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User
from app.security import hash_password, needs_rehash, verify_password


@cache
def _decoy_hash() -> str:
    """Хеш, с которым сверяется пароль несуществующего логина.

    Без него отказ по чужому логину приходил мгновенно, а по настоящему — после
    PBKDF2: время ответа выдавало, какие сотрудники есть.
    """
    return hash_password(secrets.token_urlsafe(16))


async def authenticate(
    session_factory: async_sessionmaker[AsyncSession], username: str, password: str
) -> User | None:
    """Активный сотрудник с этим логином и паролем или None.

    PBKDF2 считается в потоке: 600 000 итераций — доли секунды, и всё это время
    event loop не обслуживал бы ни чаты, ни сокеты. Соединение с БД на это время
    не держится. Хеш на прежнем числе итераций перехешируется здесь же: другого
    момента, когда пароль известен, не будет.
    """
    async with session_factory() as db:
        user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    stored = user.password_hash if user is not None else await asyncio.to_thread(_decoy_hash)
    valid = await asyncio.to_thread(verify_password, password, stored)
    if user is None or not user.is_active or not valid:
        return None
    if needs_rehash(user.password_hash):
        upgraded = await asyncio.to_thread(hash_password, password)
        async with session_factory() as db:
            await db.execute(update(User).where(User.id == user.id).values(password_hash=upgraded))
            await db.commit()
    return user


async def staff_profile(session_factory: async_sessionmaker[AsyncSession], user_id: str) -> User | None:
    """Учётка сотрудника для шапки интерфейса: имя и линия; None — учётка удалена.

    Из базы, а не из токена: имя и линию админ меняет на лету, а токен живёт
    дольше этих изменений."""
    async with session_factory() as db:
        return await db.get(User, user_id)
