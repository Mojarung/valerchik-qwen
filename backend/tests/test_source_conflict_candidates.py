"""Audit candidate selection must retain competing answers and complete text."""

from datetime import UTC, datetime

import pytest

from app.schemas.source_conflicts import SourceSnapshot
from app.services.source_conflict_candidates import SourceConflictCandidateFinder


class RecordingEmbedder:
    version = "fixture-v1"
    dim = 2
    semantic = True

    def __init__(self, vectors):
        self.vectors = vectors
        self.texts = []

    async def embed(self, texts):
        self.texts.extend(texts)
        return self.vectors


def source(article_id, answer):
    return SourceSnapshot(
        id=article_id,
        article_id=article_id,
        source_version=article_id,
        content_hash=article_id,
        question="Размер файла?",
        answer=answer,
        audience="all",
        kind="answer",
        category="",
        source="manual",
        doc_key="",
        origin={},
        created_at=datetime.now(UTC),
    )


async def test_identical_questions_keep_both_answers_and_full_tail():
    embedder = RecordingEmbedder([[1, 0], [1, 0]])
    finder = SourceConflictCandidateFinder(embedder)
    left = source("b", "Описание. " * 60 + "До 10 МБ.")
    right = source("a", "Описание. " * 60 + "До 20 МБ.")
    pairs = await finder.find([left, right], top_k=3, max_pairs=10)
    assert len(pairs) == 1
    assert pairs[0].left.article_id == "a"
    assert pairs[0].right.article_id == "b"
    assert any("До 10 МБ." in text for text in embedder.texts)
    assert any("До 20 МБ." in text for text in embedder.texts)


async def test_union_lexical_and_semantic_neighbors_is_canonical_and_bounded():
    embedder = RecordingEmbedder([[1, 0], [0, 1], [1, 0]])
    finder = SourceConflictCandidateFinder(embedder)
    articles = [
        source("a", "Уникальный объект лимит 10 МБ"),
        source("b", "Уникальный объект лимит 20 МБ"),
        source("c", "Регистрация аккаунта"),
    ]
    pairs = await finder.find(articles, top_k=1, max_pairs=10)
    ids = {(pair.left.article_id, pair.right.article_id) for pair in pairs}
    assert ("a", "b") in ids
    assert ("a", "c") in ids
    assert all(left < right for left, right in ids)
    limited = await finder.find(articles, top_k=1, max_pairs=1)
    assert len(limited) == 1


async def test_empty_pool_makes_no_embedding_request():
    embedder = RecordingEmbedder([])
    assert await SourceConflictCandidateFinder(embedder).find([], top_k=1, max_pairs=1) == []
    assert embedder.texts == []


async def test_malformed_vectors_fail_before_model_comparisons():
    with pytest.raises(ValueError, match="embedding"):
        await SourceConflictCandidateFinder(RecordingEmbedder([[1, 0]])).find(
            [source("a", "A"), source("b", "B")], top_k=1, max_pairs=5
        )
