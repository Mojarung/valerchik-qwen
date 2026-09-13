"""Automatic discovery searches the corpus without hiding duplicate questions."""

from unittest.mock import AsyncMock

import pytest

from app.adapters.embeddings import HashEmbedder
from app.adapters.vectorstore import Hit
from app.db import Database
from app.models import KBArticle
from app.services.source_conflict_discovery import AnchoredCandidateFinder, ConflictNeighbourDiscovery
from tests.test_source_conflict_detector import snapshot


@pytest.fixture
async def corpus(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'discovery.db'}")
    await db.create_all()
    async with db.session_factory() as s:
        s.add_all(
            [
                KBArticle(
                    category="Тест",
                    id="anchor",
                    question="Максимальный срок оферты?",
                    answer="Срок оферты 12 месяцев.",
                    audience="supplier",
                ),
                KBArticle(
                    category="Тест",
                    id="duplicate",
                    question="Максимальный срок оферты?",
                    answer="Срок оферты 3 месяца.",
                    audience="supplier",
                ),
                KBArticle(
                    category="Тест",
                    id="other",
                    question="Продолжительность предложения?",
                    answer="Предложение действительно квартал.",
                    audience="all",
                ),
                KBArticle(
                    category="Тест",
                    id="customer",
                    question="Максимальный срок оферты?",
                    answer="1 месяц.",
                    audience="customer",
                ),
                KBArticle(
                    category="Тест",
                    id="archived",
                    question="Максимальный срок оферты?",
                    answer="1 месяц.",
                    status="archived",
                ),
                KBArticle(
                    category="Тест",
                    id="case",
                    question="Максимальный срок оферты?",
                    answer="Исправили.",
                    origin={"kind": "operator_case"},
                ),
                KBArticle(
                    category="Тест",
                    id="unrelated",
                    question="Как изменить пароль?",
                    answer="Настройки аккаунта.",
                ),
            ]
        )
        await s.commit()
    yield db
    await db.dispose()


async def test_union_keeps_duplicate_and_semantic_neighbour_filters_before_limit(corpus):
    embedder = HashEmbedder()
    embedder.semantic = True
    store = AsyncMock()
    store.search.return_value = [
        Hit(id="customer", score=0.99, payload={}),
        Hit(id="other", score=0.95, payload={}),
    ]
    finder = ConflictNeighbourDiscovery(corpus.session_factory, embedder, store)
    assert await finder.find("anchor", limit=2) == ["duplicate", "other"]


async def test_sql_fallback_includes_new_article_despite_failed_indexing(corpus):
    store = AsyncMock()
    store.search.side_effect = RuntimeError("unavailable")
    finder = ConflictNeighbourDiscovery(corpus.session_factory, HashEmbedder(), store)
    assert "duplicate" in await finder.find("anchor", limit=5)
    async with corpus.session_factory() as s:
        s.add(
            KBArticle(
                category="Тест",
                id="new",
                question="Максимальный срок оферты?",
                answer="До 6 месяцев.",
                audience="supplier",
            )
        )
        await s.commit()
    assert "new" in await finder.find("anchor", limit=5)
    async with corpus.session_factory() as s:
        article = await s.get(KBArticle, "duplicate")
        article.status = "archived"
        await s.commit()
    assert "duplicate" not in await finder.find("anchor", limit=5)


async def test_no_overlap_does_not_spend_calls_on_arbitrary_articles(corpus):
    store = AsyncMock()
    finder = ConflictNeighbourDiscovery(corpus.session_factory, HashEmbedder(), store)
    assert await finder.find("unrelated", limit=5) == []
    assert await finder.find("archived", limit=5) == []
    store.search.assert_not_awaited()


async def test_same_section_chunks_are_not_automatic_candidates(corpus):
    async with corpus.session_factory() as db:
        db.add_all(
            [
                KBArticle(
                    id="section-a",
                    category="Инструкция",
                    question="1.7.1 Формирование XSD-схемы",
                    answer="Корневой элемент Schema.",
                    audience="supplier",
                    doc_key="yml-guide",
                    origin={"kind": "section"},
                ),
                KBArticle(
                    id="section-b",
                    category="Инструкция",
                    question="1.7.1 Формирование XSD-схемы",
                    answer="Атрибут объявляет простой тип.",
                    audience="supplier",
                    doc_key="yml-guide",
                    origin={"kind": "section"},
                ),
                KBArticle(
                    id="section-other-document",
                    category="Инструкция",
                    question="1.7.1 Формирование XSD-схемы",
                    answer="Используйте другой корневой элемент.",
                    audience="supplier",
                    doc_key="another-guide",
                    origin={"kind": "section"},
                ),
            ]
        )
        await db.commit()
    finder = ConflictNeighbourDiscovery(corpus.session_factory, HashEmbedder(), AsyncMock())
    neighbours = await finder.find("section-a", limit=5)
    assert "section-b" not in neighbours
    assert "section-other-document" in neighbours


async def test_chunks_from_same_document_are_not_automatic_candidates(corpus):
    async with corpus.session_factory() as db:
        db.add_all(
            [
                KBArticle(
                    id="section-parent",
                    category="Инструкция",
                    question="8.9.2 Работа со сметой",
                    answer="Верхнеуровневые шаги работы со сметой.",
                    audience="supplier",
                    doc_key="act-guide",
                    origin={"kind": "section"},
                ),
                KBArticle(
                    id="section-child",
                    category="Инструкция",
                    question="8.9.2.2 Создание сметы",
                    answer="Создайте смету в личном кабинете.",
                    audience="supplier",
                    doc_key="act-guide",
                    origin={"kind": "section"},
                ),
                KBArticle(
                    id="section-peer",
                    category="Инструкция",
                    question="8.9.3 Проверка сметы",
                    answer="Проверьте итоговую сумму и верхнеуровневые шаги работы со сметой.",
                    audience="supplier",
                    doc_key="act-guide",
                    origin={"kind": "section"},
                ),
            ]
        )
        await db.commit()
    finder = ConflictNeighbourDiscovery(corpus.session_factory, HashEmbedder(), AsyncMock())
    neighbours = await finder.find("section-parent", limit=5)
    assert "section-child" not in neighbours
    assert "section-peer" not in neighbours


async def test_anchored_finder_never_compares_neighbours_to_each_other():
    sources = [snapshot("a"), snapshot("b"), snapshot("c")]
    finder = AnchoredCandidateFinder(HashEmbedder(), "b")
    pairs = await finder.find(sources, top_k=5, max_pairs=5)
    assert {(p.left.article_id, p.right.article_id) for p in pairs} == {("a", "b"), ("b", "c")}


async def test_opposite_role_cannot_crowd_out_lexical_candidates(corpus):
    async with corpus.session_factory() as db:
        db.add_all(
            [
                KBArticle(
                    id=f"noise-{i}",
                    category="Тест",
                    audience="customer",
                    question=f"Транспортировка аккумуляторов {i}",
                    answer="Транспортировка аккумуляторов.",
                )
                for i in range(50)
            ]
        )
        db.add_all(
            [
                KBArticle(
                    id="target",
                    category="Тест",
                    audience="supplier",
                    question="Транспортировка аккумуляторов",
                    answer="Смотрите инструкцию.",
                ),
                KBArticle(
                    id="match",
                    category="Тест",
                    audience="supplier",
                    question="Упаковка батарей",
                    answer="Транспортировка аккумуляторов. "
                    + "Упаковка безопасна и соответствует нормативным требованиям. " * 100,
                ),
            ]
        )
        await db.commit()
    finder = ConflictNeighbourDiscovery(corpus.session_factory, HashEmbedder(), AsyncMock())
    assert "match" in await finder.find("target", limit=1)
