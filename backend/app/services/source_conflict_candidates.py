"""Bounded audit-specific union of full-text lexical and embedding neighbors."""

import math
import re
from dataclasses import dataclass

from app.adapters.embeddings import Embedder
from app.schemas.source_conflicts import SourceSnapshot

CANDIDATE_VERSION = "full-article-union-v1"
_WORD = re.compile(r"[\w]+", re.UNICODE)
_MIN_TOKEN_LENGTH = 3
_PAIR_SIZE = 2
_STOPWORDS = frozenset("как что для или при это его она они быть чтобы который какая какие какой где".split())


def article_text(source: SourceSnapshot) -> str:
    return f"{source.question}\n{source.answer}"


def _terms(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.casefold()) if len(word) >= _MIN_TOKEN_LENGTH} - _STOPWORDS


def _cosine(left: list[float], right: list[float]) -> float:
    norm = math.sqrt(sum(v * v for v in left) * sum(v * v for v in right))
    return sum(a * b for a, b in zip(left, right, strict=True)) / norm if norm else 0.0


@dataclass(frozen=True)
class CandidatePair:
    left: SourceSnapshot
    right: SourceSnapshot
    score: float


class SourceConflictCandidateFinder:
    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self.version = f"{CANDIDATE_VERSION}:{embedder.version}:semantic={embedder.semantic}"

    async def find(self, sources: list[SourceSnapshot], *, top_k: int, max_pairs: int) -> list[CandidatePair]:
        if top_k < 1 or max_pairs < 1:
            raise ValueError("Candidate limits must be positive")
        ordered = sorted(
            {source.article_id: source for source in sources}.values(), key=lambda s: s.article_id
        )
        if len(ordered) < _PAIR_SIZE:
            return []
        texts = [article_text(source) for source in ordered]
        vectors = await self.embedder.embed(texts)
        _validate_vectors(vectors, len(ordered))
        terms = [_terms(text) for text in texts]
        selected: dict[tuple[int, int], float] = {}
        for i, vector in enumerate(vectors):
            semantic = [(_cosine(vector, other), j) for j, other in enumerate(vectors) if i != j]
            lexical = [(_overlap(terms[i], other), j) for j, other in enumerate(terms) if i != j]
            for scores in (semantic, lexical):
                for score, j in sorted(scores, key=lambda item: (-item[0], ordered[item[1]].article_id))[
                    :top_k
                ]:
                    if score > 0:
                        key = min(i, j), max(i, j)
                        selected[key] = max(selected.get(key, 0), score)
        ranked = sorted(selected.items(), key=lambda item: (-item[1], item[0]))[:max_pairs]
        return [CandidatePair(ordered[i], ordered[j], score) for (i, j), score in ranked]


def _overlap(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left or right else 0.0


def _validate_vectors(vectors: list[list[float]], count: int) -> None:
    if len(vectors) != count or not vectors or not vectors[0]:
        raise ValueError("Invalid embedding count or empty embedding")
    dim = len(vectors[0])
    if any(len(vector) != dim or not all(math.isfinite(value) for value in vector) for vector in vectors):
        raise ValueError("Invalid embedding dimensions or non-finite embedding")
