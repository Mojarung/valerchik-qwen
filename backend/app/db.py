"""Асинхронная работа с БД: PostgreSQL (full) или SQLite (lite/тесты)."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, DateTime, Text, TypeDecorator, event, inspect, text
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import ConnectionPoolEntry

log = logging.getLogger(__name__)


# Фабрика асинхронных сессий — в таком виде её передают по сервисам
SessionFactory = async_sessionmaker[AsyncSession]


class Base(DeclarativeBase):
    pass


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Время всегда в UTC и со смещением — и при записи, и при чтении.

    SQLite хранит datetime строкой без пояса и отдаёт его наивным. Такое значение
    уходило в API как «2026-09-10T22:05:00», и браузер читал его как местное
    время: у оператора в Москве очередь отставала на три часа. Тип столбца чинит
    это в одном месте для всех таблиц, а не пометкой в каждом обработчике.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        return _as_utc(value)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        return _as_utc(value)


class Database:
    def __init__(self, url: str) -> None:
        kwargs: dict[str, Any] = {"echo": False}
        if url.startswith("postgresql"):
            kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True)
        self.engine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):
            _tune_sqlite(self.engine)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def create_all(self) -> None:
        from app import models, models_conflicts  # noqa: F401 — регистрация таблиц

        async with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                # API и воркеры стартуют вместе: checkfirst у create_all сам по себе
                # не защищает от одновременного создания новой таблицы.
                await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('valerchik_schema'))"))
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_add_missing_columns)
            await conn.run_sync(_widen_source_columns)

    async def ping(self) -> None:
        """Связь с базой: исключение — база недоступна."""
        async with self.session_factory() as s:
            await s.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as s:
            yield s


def _tune_sqlite(engine: AsyncEngine) -> None:
    """WAL и ожидание блокировки для lite-режима.

    В журнале по умолчанию (delete) читатель блокирует писателя целиком: пока
    идёт долгий SELECT — например выгрузка диалогов, — сообщение в чат не
    записывается, а через пять секунд драйвер отдаёт «database is locked».
    Замерено: та же запись ждёт 1.75 с при обычном журнале и 0.01 с при WAL.

    Листенер вешаем на sync_engine: у асинхронного движка своих событий нет.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


def _add_missing_columns(conn: Connection) -> None:
    """Досинхронизация схемы: добавляет колонки, появившиеся в моделях.

    `create_all` создаёт только отсутствующие таблицы и не трогает существующие,
    поэтому на уже развёрнутом стенде новая колонка ломает приложение на первом
    же запросе, хотя с нуля всё поднимается. Полноценные миграции проекту такого
    размера избыточны, а этой проверки хватает: она идемпотентна, отрабатывает за
    миллисекунды и одинаково работает в PostgreSQL и SQLite.

    Колонки добавляются nullable: значение для существующих строк взять неоткуда,
    а код и так рассчитан на пустоту (`article.images or []`).
    """
    inspector = inspect(conn)
    quote = conn.dialect.identifier_preparer.quote
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl_type = column.type.compile(conn.dialect)
            conn.exec_driver_sql(
                f"ALTER TABLE {quote(table.name)} ADD COLUMN {quote(column.name)} {ddl_type}"
            )
            log.info("Схема: в таблицу %s добавлена колонка %s", table.name, column.name)


def _widen_source_columns(conn: Connection) -> None:
    """Снимает старые ограничения длины с полей, заполненных внешними данными.

    SQLite длину VARCHAR не ограничивает. В PostgreSQL старые стенды уже имеют
    ``kb_articles.category VARCHAR(128)``, поэтому одной правки модели мало:
    ``create_all`` не меняет тип существующей колонки.
    """
    if conn.dialect.name != "postgresql":
        return
    columns = {column["name"]: column for column in inspect(conn).get_columns("kb_articles")}
    category = columns.get("category")
    if category is None or isinstance(category["type"], Text):
        return
    conn.exec_driver_sql('ALTER TABLE "kb_articles" ALTER COLUMN "category" TYPE TEXT')
    log.info("Схема: kb_articles.category расширена до TEXT")
