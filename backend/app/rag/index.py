"""Векторный индекс фрагментов: тексты для эмбеддера и параллельная векторизация.

Точки фрагмента: основная (раздел, заголовок, текст) и по точке на каждый вопрос
пользователя к нему — живые формулировки из обращений и вопросы, написанные
моделью при импорте. Вопрос человека сравнивается с вопросами, а не только с
текстом инструкции: тексты одного рода близки сильнее.
"""

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from app.adapters.vectorstore import PHRASING_FIELD, VectorDoc

if TYPE_CHECKING:
    from app.adapters.embeddings import Embedder
    from app.models import KBArticle

EMBED_CHUNK = 16
EMBED_CONCURRENCY = 6
DOC_EMBED_CHARS = 1800
QWEN_INSTRUCT = (
    "Instruct: Найди фрагмент базы знаний портала закупок, который отвечает на вопрос пользователя\nQuery: "
)
# Модели обучены с префиксами задачи: без них запрос и документ живут в разных
# пространствах, и близость падает до шума. Ключ — часть имени модели в TEI.
_PREFIXES = (
    ("berta", "search_query: ", "search_document: "),
    ("frida", "search_query: ", "search_document: "),
    ("qwen3-embedding", QWEN_INSTRUCT, ""),
    ("e5", "query: ", "passage: "),
)


async def prefixes(embedder: Embedder) -> tuple[str, str]:
    """(префикс запроса, префикс документа) для модели эмбеддера."""
    model_id = getattr(embedder, "model_id", None)
    name = (await model_id()).lower() if model_id else ""
    return next(((query, doc) for key, query, doc in _PREFIXES if key in name), ("", ""))


def document_text(question: str, category: str, answer: str, prefix: str) -> str:
    return f"{prefix}{category} › {question}\n{answer}"[: len(prefix) + DOC_EMBED_CHARS]


def points_of(article: KBArticle, prefix: str) -> list[tuple[str, str, dict[str, Any]]]:
    """(id точки, текст для эмбеддера, payload): основная точка и по точке на вопрос к фрагменту."""
    origin = article.origin or {}
    payload = {"audience": article.audience or "all", "kind": origin.get("kind", "")}
    points = [
        (article.id, document_text(article.question, article.category, article.answer, prefix), payload)
    ]
    asked = dict.fromkeys([*(origin.get("examples") or []), *(origin.get("questions") or [])])
    for number, text in enumerate(asked, start=1):
        points.append((f"{article.id}:{number}", f"{prefix}{text}", {**payload, PHRASING_FIELD: text}))
    return points


async def embed_all(embedder: Embedder, texts: Sequence[str]) -> list[list[float]]:
    """Векторы пачками параллельно: TEI считает несколько пачек одновременно на всех ядрах."""
    gate = asyncio.Semaphore(EMBED_CONCURRENCY)

    async def one(chunk: Sequence[str]) -> list[list[float]]:
        async with gate:
            return await embedder.embed(list(chunk))

    parts = await asyncio.gather(
        *(one(texts[i : i + EMBED_CHUNK]) for i in range(0, len(texts), EMBED_CHUNK))
    )
    return [vector for part in parts for vector in part]


async def vector_docs(embedder: Embedder, articles: Sequence[KBArticle]) -> list[VectorDoc]:
    _, prefix = await prefixes(embedder)
    points = [(article.id, *point) for article in articles for point in points_of(article, prefix)]
    vectors = await embed_all(embedder, [text for _, _, text, _ in points]) if points else []
    return [
        VectorDoc(id=point_id, vector=vector, payload=payload, article_id=article_id)
        for (article_id, point_id, _, payload), vector in zip(points, vectors, strict=True)
    ]
