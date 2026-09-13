"""Индексация базы знаний: эмбеддинги → векторное хранилище (reindex без даунтайма),
лексический индекс, оповещение всех инстансов через pub/sub.

Статья в векторном индексе — несколько точек: основная (вопрос, ключевые слова,
начало ответа) и по точке на каждую реальную формулировку вопроса
(services/phrasings.py). Все точки несут ссылку на статью, поиск сворачивает их
в статью по лучшей, а снятие статьи убирает их все.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.adapters.vectorstore import VectorDoc
from app.models import KBArticle
from app.rag.index import vector_docs
from app.rag.search import KB_CHANNEL

if TYPE_CHECKING:
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)


class KBService:
    def __init__(self, rt: AppRuntime) -> None:
        self.rt = rt

    async def _vector_docs(self, articles: Sequence[KBArticle]) -> list[VectorDoc]:
        """Точки статей с векторами (тексты и префиксы эмбеддера — app/rag/index.py)."""
        return await vector_docs(self.rt.embedder, articles)

    async def index_all(self) -> int:
        """Полная переиндексация: новая коллекция собирается в фоне, алиас
        переключается атомарно — поиск работает всё время. Возвращает число статей."""
        async with self.rt.db.session_factory() as db:
            articles = (
                (await db.execute(select(KBArticle).where(KBArticle.status == "active"))).scalars().all()
            )
        docs = await self._vector_docs(articles)
        await self.rt.store.ensure_ready(self.rt.embedder.dim)
        await self.rt.store.reindex(docs)
        await self.rt.retriever.refresh_from_db(self.rt.db.session_factory)
        await self.notify_changed()
        log.info("Переиндексировано статей: %d (точек в векторном индексе: %d)", len(articles), len(docs))
        return len(articles)

    async def index_article(self, article_id: str) -> None:
        async with self.rt.db.session_factory() as db:
            article = await db.get(KBArticle, article_id)
        if article is None:
            return
        if article.status != "active":
            await self.rt.store.delete([article_id])
        else:
            await self.rt.store.upsert(await self._vector_docs([article]))
        await self.rt.retriever.refresh_from_db(self.rt.db.session_factory)
        await self.notify_changed()

    async def index_many(self, article_ids: list[str]) -> int:
        """Индексация пачки статей без полной переиндексации базы.

        После импорта методички новых статей могут быть сотни. Гонять ради них
        index_all() — минуты работы эмбеддера над давно проиндексированной базой,
        а по одной через index_article() — столько же запросов к эмбеддеру, но
        батчами по одному тексту.
        """
        if not article_ids:
            return 0
        async with self.rt.db.session_factory() as db:
            articles = (
                (
                    await db.execute(
                        select(KBArticle).where(KBArticle.id.in_(article_ids), KBArticle.status == "active")
                    )
                )
                .scalars()
                .all()
            )
        if not articles:
            return 0
        docs = await self._vector_docs(articles)
        await self.rt.store.ensure_ready(self.rt.embedder.dim)
        await self.rt.store.upsert(docs)
        await self.rt.retriever.refresh_from_db(self.rt.db.session_factory)
        await self.notify_changed()
        log.info("Проиндексировано статей пачкой: %d (точек: %d)", len(articles), len(docs))
        return len(articles)

    async def remove_article(self, article_id: str) -> None:
        """Снятие статьи: из векторного индекса уходят все её точки."""
        await self.rt.store.delete([article_id])
        await self.rt.retriever.refresh_from_db(self.rt.db.session_factory)
        await self.notify_changed()

    async def remove_many(self, article_ids: list[str]) -> None:
        """Снятие пачки статей с публикации одним проходом.

        Цикл по remove_article() перечитывал бы лексический индекс и рассылал
        оповещение на каждую статью — при выделении всей страницы это два
        десятка лишних обходов базы.
        """
        if not article_ids:
            return
        await self.rt.store.delete(article_ids)
        await self.rt.retriever.refresh_from_db(self.rt.db.session_factory)
        await self.notify_changed()

    async def notify_changed(self) -> None:
        """Другие инстансы перечитают лексический индекс (см. listener в runtime)."""
        await self.rt.cache.publish(KB_CHANNEL, "changed")
