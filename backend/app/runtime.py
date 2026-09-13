"""Сборка рантайма приложения: адаптеры по режиму (lite/full), слушатели pub/sub,
жизненный цикл. Один объект AppRuntime живёт в app.state.rt."""

import asyncio
import contextlib
import logging
from datetime import timedelta

from sqlalchemy import select

from app.adapters.cache import create_cache
from app.adapters.docparse import build_docparse_client
from app.adapters.embeddings import create_embedder
from app.adapters.storage import build_media_storage
from app.adapters.toxicity import build_toxicity_client
from app.adapters.vectorstore import create_vector_store
from app.config import Settings
from app.db import Database
from app.dynamic_settings import DynamicSettings
from app.rag.search import KB_CHANNEL, RagRetriever
from app.services import guardrails, guides_store
from app.services.chat import ChatService
from app.services.kb import KBService
from app.services.routing import LineClassifier
from app.services.source_conflict_auto import SourceConflictAutoWorker
from app.services.source_conflict_runner import SourceConflictRunner
from app.ws.manager import WSManager

log = logging.getLogger(__name__)

# Через сколько простоя импорт считается брошенным. С запасом: разметка
# трёхсотстраничного PDF идёт больше двадцати минут без единого обновления прогресса.
STALE_INGEST_MINUTES = 90

# Как часто диспетчер смотрит на очередь. Порог ожидания задаётся в админке и
# измеряется десятками секунд, так что чаще смысла нет.
DISPATCH_TICK_SECONDS = 15


class AppRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.effective_database_url)
        self.cache = create_cache(settings.redis_url)
        self.embedder = create_embedder(settings.tei_url, settings.tei_rerank_url)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = create_vector_store(settings.qdrant_url, settings.data_dir)
        self.dyn = DynamicSettings(
            self.db.session_factory,
            self.cache,
            env_overrides={
                "llm.base_url": settings.llm_base_url,
                "llm.api_key": settings.llm_api_key,
                "llm.model": settings.llm_model,
                # поднят профиль rerank (задан TEI_RERANK_URL) — включаем дожим выдачи по умолчанию
                "retrieval.use_reranker": bool(settings.tei_rerank_url),
            },
        )
        self.retriever = RagRetriever(self.embedder, self.store, self.dyn)
        # Настройки нужны классификатору на каждом предсказании: таблица «тема →
        # линия», адресаты по регламенту и пороги правятся в админке на лету.
        self.classifier = LineClassifier(self.embedder, settings.data_dir, self.cache, dyn=self.dyn)
        # Картинки из методичек: S3, если задан endpoint, иначе том данных
        self.media = build_media_storage(settings)
        # Разбор PDF с иллюстрациями; None — импорт идёт через markitdown
        self.docparse = build_docparse_client(settings)
        # Нейросеть против оскорблений; None — guardrails на правилах и словаре
        self.toxicity = build_toxicity_client(settings)
        self.ws = WSManager(self.cache)
        self.chat = ChatService(self)
        self.kb = KBService(self)
        self.source_conflict_runner = SourceConflictRunner(self.db.session_factory, self.dyn, self.embedder)
        self.source_conflict_auto = SourceConflictAutoWorker(
            self.db.session_factory, self.dyn, self.embedder, self.store
        )
        self._kb_listener: asyncio.Task[None] | None = None
        self._guides_listener: asyncio.Task[None] | None = None
        self._dispatcher: asyncio.Task[None] | None = None
        self._outbox: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.db.create_all()
        await self.dyn.load()
        await self.dyn.start_listener()
        await self.ws.start()
        await self._start_kb_listener()
        # Сценарии по порталу: заводские из файла, правки админов из БД
        await guides_store.refresh(self.db.session_factory)
        await self._start_guides_listener()
        # Словарь грубостей и морфология грузятся около секунды — пусть это
        # случится на старте, а не на первой реплике посетителя.
        try:
            await asyncio.to_thread(guardrails.warm_lexicon)
        except Exception:
            log.exception("Словарь грубых слов не загрузился, проверка по нему отключится до перезапуска")

        if self.settings.seed_on_startup:
            from app.seed.seeder import seed_if_empty

            await seed_if_empty(self)

        try:
            await self.store.ensure_ready(self.embedder.dim)
        except Exception:
            log.exception("Векторное хранилище недоступно на старте, поиск деградирует до BM25")
        count = await self.retriever.refresh_from_db(self.db.session_factory)
        log.info("Лексический индекс загружен: %d статей", count)

        await self._fail_orphaned_ingests()
        self._dispatcher = asyncio.create_task(self._dispatch_loop())
        self._outbox = asyncio.create_task(self._outbox_loop())
        await self.source_conflict_auto.start()

    async def _outbox_loop(self) -> None:
        from app.services.support_flow import flush_outbox

        while True:
            try:
                await flush_outbox(self)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Уведомления сохранены; доставка будет повторена")
            await asyncio.sleep(1)

    async def _dispatch_loop(self) -> None:
        """Обслуживает очередь и завершает неактивные AI-диалоги.

        Живёт в API, а не в воркере: работа — это короткие запросы к БД, а
        уведомления всё равно уходят через websocket-комнаты, то есть отсюда.
        Проход берётся под коротким локом — при `--scale api=3` цикл крутится в
        каждом инстансе, и без лока одно обращение назначили или закрыли бы дважды.
        """
        from app.services.dispatch import (
            LOCK_KEY,
            LOCK_TTL_SECONDS,
            autoassign_pending,
            reclaim_stalled,
        )
        from app.services.session_lifecycle import close_idle_sessions

        while True:
            await asyncio.sleep(DISPATCH_TICK_SECONDS)
            try:
                got_lock = await self.cache.set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL_SECONDS)
                if not got_lock:
                    continue
                # Сначала отнимаем брошенное, потом раздаём: освободившееся
                # обращение находит нового владельца в этом же проходе, а не
                # ждёт следующего тика в очереди.
                await reclaim_stalled(self)
                await autoassign_pending(self)
                await close_idle_sessions(self)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Фоновое обслуживание диалогов: проход не удался")

    async def _fail_orphaned_ingests(self) -> None:
        """Помечает импорты, брошенные без исполнителя.

        Очередь работает через BRPOP без подтверждений: сообщение снимается сразу,
        и если воркер умер посреди разбора, задача осталась бы в «разбирается»
        навсегда, а админка опрашивала бы её бесконечно. Возобновить импорт
        нельзя — часть статей уже создана, повтор дал бы дубли, — поэтому честно
        помечаем ошибкой и оставляем решение человеку.

        Трогаем только те, что давно не подавали признаков жизни. Этот код
        выполняется при старте ЛЮБОГО процесса, включая api рядом с работающим
        воркером и каждый инстанс при `--scale api=3`; без проверки давности он
        обрывал бы живые импорты. Порог с запасом: разметка трёхсотстраничного
        PDF идёт больше двадцати минут и всё это время прогресс не меняется.
        """
        from app.models import IngestJob, utcnow

        deadline = utcnow() - timedelta(minutes=STALE_INGEST_MINUTES)
        try:
            async with self.db.session_factory() as db:
                stale = (
                    (
                        await db.execute(
                            select(IngestJob).where(
                                IngestJob.status.in_(("pending", "running")),
                                IngestJob.updated_at < deadline,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for job in stale:
                    job.status = "error"
                    job.error = "Импорт прерван перезапуском сервиса. Загрузите документ заново."
                    job.finished_at = utcnow()
                if stale:
                    await db.commit()
                    log.warning("Импортов признано брошенными: %d", len(stale))
        except Exception:
            log.exception("Не удалось пометить прерванные импорты")

    async def _start_kb_listener(self) -> None:
        pubsub = self.cache.pubsub()
        await pubsub.subscribe(KB_CHANNEL)

        async def _listen() -> None:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    await self.retriever.refresh_from_db(self.db.session_factory)
                except Exception:
                    log.exception("Не удалось перечитать индекс БЗ")

        self._kb_listener = asyncio.create_task(_listen())

    async def _start_guides_listener(self) -> None:
        """Правка сценария в админке одного инстанса — новый каталог у всех."""
        pubsub = self.cache.pubsub()
        await pubsub.subscribe(guides_store.GUIDES_CHANNEL)

        async def _listen() -> None:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    await guides_store.refresh(self.db.session_factory)
                except Exception:
                    log.exception("Не удалось перечитать сценарии по порталу")

        self._guides_listener = asyncio.create_task(_listen())

    async def stop(self) -> None:
        await self.source_conflict_auto.stop()
        await self.source_conflict_runner.stop()
        await self.dyn.stop()
        await self.ws.stop()
        for task in (self._kb_listener, self._guides_listener, self._dispatcher, self._outbox):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self.toxicity is not None:
            await self.toxicity.aclose()
        with contextlib.suppress(Exception):
            await self.cache.aclose()
        await self.db.dispose()
