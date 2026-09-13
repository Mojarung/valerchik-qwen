"""Кэш/шина событий: Redis (full) или fakeredis (lite/тесты). Интерфейс одинаковый."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis


def create_cache(redis_url: str) -> Redis:
    """Возвращает асинхронный redis-клиент (настоящий или in-memory)."""
    if redis_url:
        import redis.asyncio as aioredis

        return aioredis.from_url(redis_url, decode_responses=True)
    from fakeredis import aioredis as fake_aioredis

    return fake_aioredis.FakeRedis(decode_responses=True)
