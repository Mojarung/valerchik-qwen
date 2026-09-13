"""Corpus-wide neighbours for changed articles, independent of chat title deduplication."""

import asyncio
import logging
import re
from typing import Any

import httpx
from sqlalchemy import select

from app.adapters.embeddings import Embedder
from app.adapters.vectorstore import VectorStore
from app.db import SessionFactory
from app.models import KBArticle
from app.rag.index import prefixes
from app.rag.passages import KIND_SECTION
from app.schemas.source_conflicts import SourceSnapshot
from app.services import audience
from app.services.retrieval import KeywordIndex
from app.services.source_conflict_candidates import CandidatePair, SourceConflictCandidateFinder
from app.services.source_conflict_lifecycle import auditable_source

log = logging.getLogger(__name__)
DISCOVERY_VERSION = "corpus-union-anchor-v4"
_MAX_NEIGHBOURS = 20
_OVERFETCH = 8
_MIN_LEXICAL_OVERLAP = 0.35
_MIN_SEMANTIC_SCORE = 0.45
_RRF_OFFSET = 60


def _question_key(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold().replace("ё", "е")))


def _same_source_fragment(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Do not present chunks of one imported document as independent alternatives."""
    return bool(
        left["kind"] == right["kind"] == KIND_SECTION
        and left["doc_key"]
        and left["doc_key"] == right["doc_key"]
    )


class ConflictNeighbourDiscovery:
    def __init__(self, session_factory: SessionFactory, embedder: Embedder, store: VectorStore) -> None:
        self.session_factory = session_factory
        self.embedder = embedder
        self.store = store
        self._revision: list[tuple[Any, ...]] | None = None
        self._articles: dict[str, dict[str, Any]] = {}
        self._indexes: dict[str, KeywordIndex] = {}
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        # Read authoritative versions: a failed vector update must not hide newly saved evidence.
        async with self._lock, self.session_factory() as db:
            revision = [
                tuple(row)
                for row in await db.execute(
                    select(KBArticle.id, KBArticle.source_version)
                    .where(KBArticle.status == "active")
                    .order_by(KBArticle.id)
                )
            ]
            if revision == self._revision:
                return
            rows = (await db.scalars(select(KBArticle).where(KBArticle.status == "active"))).all()
            articles = {
                row.id: {
                    "article_id": row.id,
                    "question": row.question,
                    "answer": row.answer,
                    "audience": row.audience,
                    "doc_key": row.doc_key,
                    "kind": str((row.origin or {}).get("kind") or "answer"),
                }
                for row in rows
                if auditable_source(row.origin)
            }
            indexes = {}
            for role in audience.AUDIENCES:
                index = KeywordIndex()
                scoped = [row for row in articles.values() if audience.fits(row["audience"], role)]
                await asyncio.to_thread(index.build, scoped)
                indexes[role] = index
            self._articles, self._indexes, self._revision = articles, indexes, revision

    async def find(self, article_id: str, limit: int = 5) -> list[str]:
        if not 1 <= limit <= _MAX_NEIGHBOURS:
            raise ValueError("Neighbour count must be between 1 and 20")
        await self._refresh()
        articles, indexes = self._articles, self._indexes
        source = articles.get(article_id)
        if source is None:
            return []
        eligible = {
            key
            for key, row in articles.items()
            if key != article_id
            and audience.fits(row["audience"], source["audience"])
            and not _same_source_fragment(source, row)
        }
        index = indexes[audience.normalize(source["audience"])]
        count = limit * _OVERFETCH
        scores: dict[str, float] = {}
        query = f"{source['question']}\n{source['answer']}"
        for text in (source["question"], query):
            hits = await asyncio.to_thread(index.search, text, count)
            for rank, (key, _, overlap) in enumerate(hits):
                if key in eligible and overlap >= _MIN_LEXICAL_OVERLAP:
                    scores[key] = scores.get(key, 0) + 1 / (_RRF_OFFSET + rank + 1)
        for rank, key in enumerate(await self._semantic(query, count)):
            if key in eligible:
                scores[key] = scores.get(key, 0) + 1 / (_RRF_OFFSET + rank + 1)
        question = _question_key(source["question"])
        for key in eligible:
            if question and _question_key(articles[key]["question"]) == question:
                scores[key] = scores.get(key, 0) + 1
        return sorted(scores, key=lambda key: (-scores[key], key))[:limit]

    async def _semantic(self, query: str, count: int) -> list[str]:
        if not self.embedder.semantic:
            return []
        try:
            async with asyncio.timeout(15):
                query_prefix, _ = await prefixes(self.embedder)
                vectors = await self.embedder.embed([query_prefix + query[:8000]])
                hits = await self.store.search(vectors[0], count)
            return [hit.id for hit in hits if hit.score >= _MIN_SEMANTIC_SCORE]
        except httpx.HTTPError, OSError, ValueError, RuntimeError, IndexError, TimeoutError:
            # SQL includes articles whose vector indexing failed; no credentials in logs.
            log.warning("Automatic conflict discovery: semantic search unavailable")
            return []


class AnchoredCandidateFinder(SourceConflictCandidateFinder):
    """Discovery already ranked neighbours; only compare each one to the changed article."""

    def __init__(self, embedder: Embedder, anchor_id: str) -> None:
        super().__init__(embedder)
        self.anchor_id = anchor_id
        # Independent of article id so opposite-direction queue entries reuse the same pair.
        self.version = f"{DISCOVERY_VERSION}:{embedder.version}:semantic={embedder.semantic}"

    async def find(self, sources: list[SourceSnapshot], *, top_k: int, max_pairs: int) -> list[CandidatePair]:
        if top_k < 1 or max_pairs < 1:
            raise ValueError("Candidate limits must be positive")
        by_id = {source.article_id: source for source in sources}
        anchor = by_id.get(self.anchor_id)
        if anchor is None:
            return []
        pairs = []
        for key, other in sorted(by_id.items()):
            if key != self.anchor_id and audience.fits(other.audience, anchor.audience):
                left, right = sorted((anchor, other), key=lambda source: source.article_id)
                pairs.append(CandidatePair(left, right, 1.0))
        return pairs[:max_pairs]
