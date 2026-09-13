"""Брокер фоновых задач: taskiq + Redis (full) или InMemoryBroker (lite/тесты).

В lite задачи выполняются в том же процессе (await_inplace) — API остаётся простым,
в full те же задачи разъезжаются по отдельным контейнерам-воркерам.
"""

from taskiq import AsyncBroker, InMemoryBroker

from app.config import get_settings

_settings = get_settings()

if _settings.redis_url:
    from taskiq_redis import ListQueueBroker

    # socket_timeout=None: redis-py 8.1 в режиме maintenance notifications «auto» ставит
    # таймаут чтения 5 с, из-за которого блокирующий BRPOP воркера падает по таймауту.
    broker: AsyncBroker = ListQueueBroker(_settings.redis_url, socket_timeout=None)
else:
    broker = InMemoryBroker(await_inplace=True)
