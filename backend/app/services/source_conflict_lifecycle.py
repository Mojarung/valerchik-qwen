"""Transactional source versions for all ORM article writers.

Core/bulk SQL updates bypass ORM hooks and must explicitly rotate source_version.
Current article CRUD, bulk actions, seed and document replacement use ORM writes.
Vector indexing is deliberately outside this source-of-truth lifecycle.
"""

import hashlib
import json
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app import models_conflicts  # noqa: F401 — register outbox even with direct metadata.create_all
from app.rag.passages import KIND_FAQ, KIND_SECTION
from app.services import audience, kbkind

SOURCE_FIELDS = ("question", "answer", "category", "audience", "source", "doc_key", "origin", "images")
AUDITABLE_KINDS = frozenset({kbkind.ANSWER, KIND_SECTION, KIND_FAQ})


def source_kind(origin: dict[str, Any] | None) -> str:
    """Preserve rag-v2 kinds; legacy articles without a kind remain normal answers."""
    return str((origin or {}).get("kind") or kbkind.ANSWER)


def auditable_source(origin: dict[str, Any] | None) -> bool:
    """Only user-facing rules can contradict each other.

    Support tickets describe individual incidents, while term and glossary rows
    define vocabulary. Comparing either group as a general rule creates false
    positives and spends the single model request assigned to an article.
    """
    return source_kind(origin) in AUDITABLE_KINDS


def semantic_origin(origin: dict[str, Any] | None) -> dict[str, Any]:
    """Search phrasings do not change the original evidence or its conditions."""
    return {key: value for key, value in (origin or {}).items() if key != "phrasings"}


def source_payload(article: Any) -> dict[str, Any]:
    return {
        "question": article.question,
        "answer": article.answer,
        "category": article.category,
        "audience": audience.normalize(article.audience),
        "source": article.source,
        "doc_key": article.doc_key or "",
        "origin": semantic_origin(article.origin),
        "kind": source_kind(article.origin),
        "images": article.images or [],
    }


def content_hash(article: Any) -> str:
    raw = json.dumps(source_payload(article), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _changed(article: Any) -> bool:
    state = inspect(article)
    if state.attrs.status.history.has_changes():
        return True
    for field in SOURCE_FIELDS:
        history = state.attrs[field].history
        if not history.has_changes():
            continue
        if field == "origin" and history.deleted and history.added:
            if semantic_origin(history.deleted[0]) == semantic_origin(history.added[0]):
                continue
        return True
    return False


@event.listens_for(Session, "before_flush")
def _rotate_versions(session: Session, flush_context: object, instances: object) -> None:
    from app.models import KBArticle, new_id
    from app.services.source_conflict_auto_queue import enqueue_article

    for article in session.new.union(session.dirty):
        if isinstance(article, KBArticle) and (article in session.new or _changed(article)):
            article.id = article.id or new_id()
            article.source_version = new_id()
            enqueue_article(session, article)
    for article in session.deleted:
        if isinstance(article, KBArticle):
            enqueue_article(session, article, deleted=True)
