"""Immutable source snapshots and version-aware comparisons, independent of search."""

import hashlib
import re
import unicodedata
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Select, and_, exists, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.models import AuditLog, KBArticle, new_id, utcnow
from app.models_conflicts import SourceClaim, SourceConflict
from app.schemas.source_conflicts import (
    ConflictGroup,
    ConflictGroupSource,
    ConflictResult,
    ConflictReview,
    Outcome,
    PairAssessment,
    SourceSnapshot,
)
from app.services import audience
from app.services.source_conflict_jobs import MAX_PAGE_SIZE, SourceConflictJobs
from app.services.source_conflict_lifecycle import auditable_source, content_hash, source_kind

MAX_SNAPSHOTS = 500
VERSION_MAX_LENGTH = 128
_GROUP_WORDS = re.compile(r"[^\w]+", re.UNICODE)


def snapshot_out(row: SourceClaim) -> SourceSnapshot:
    return SourceSnapshot.model_validate(row, from_attributes=True)


def _current(claim_id: Any) -> ColumnElement[bool]:
    claim = aliased(SourceClaim)
    article = aliased(KBArticle)
    return exists(
        select(claim.id)
        .join(article, article.id == claim.article_id)
        .where(
            claim.id == claim_id,
            article.status == "active",
            article.source_version == claim.source_version,
        )
    )


def _current_pair() -> ColumnElement[bool]:
    return and_(_current(SourceConflict.left_id), _current(SourceConflict.right_id))


def _current_result() -> ColumnElement[bool]:
    return and_(
        _current_pair(),
        or_(SourceConflict.preferred_claim_id.is_(None), _current(SourceConflict.preferred_claim_id)),
    )


def _pair_stmt(left_id: str, right_id: str, version: str) -> Select[tuple[SourceConflict]]:
    left_id, right_id = sorted((left_id, right_id))
    return select(SourceConflict).where(
        SourceConflict.left_id == left_id,
        SourceConflict.right_id == right_id,
        SourceConflict.processing_version == version,
    )


def _validate_quotes(assessment: PairAssessment, left: SourceClaim, right: SourceClaim) -> None:
    claim_quotes = ((assessment.left_quote, left), (assessment.right_quote, right))
    scope_quotes = ((assessment.left_scope_quote, left), (assessment.right_scope_quote, right))
    for quote, source in (*claim_quotes, *scope_quotes):
        if quote and quote not in source.question and quote not in source.answer:
            raise ValueError("quote must be an exact substring of the original source")
    # Historical/manual results predate the explicit scope fields. New model
    # responses cannot omit them because validate_assessment enforces that at
    # the detector boundary; the registry still needs to read and preserve old
    # evidence while requiring the two contradictory claims themselves.
    if assessment.outcome == "conflict":
        for quote, _ in claim_quotes:
            if quote.strip():
                continue
            raise ValueError("conflict requires an exact quote from both sources")


async def _claim(db: AsyncSession, article: KBArticle) -> SourceClaim:
    if not article.source_version:
        article.source_version = new_id()
        await db.flush()
    stmt = select(SourceClaim).where(
        SourceClaim.article_id == article.id, SourceClaim.source_version == article.source_version
    )
    existing = (await db.scalars(stmt)).one_or_none()
    if existing:
        return existing
    row = SourceClaim(
        article_id=article.id,
        source_version=article.source_version,
        content_hash=content_hash(article),
        question=article.question,
        answer=article.answer,
        category=article.category,
        audience=audience.normalize(article.audience),
        kind=source_kind(article.origin),
        source=article.source,
        doc_key=article.doc_key or "",
        origin=article.origin or {},
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        return (await db.scalars(stmt)).one()
    return row


async def _result(db: AsyncSession, row: SourceConflict) -> ConflictResult:
    left = await db.get(SourceClaim, row.left_id)
    right = await db.get(SourceClaim, row.right_id)
    if left is None or right is None:
        raise ValueError("result references missing source evidence")
    preferred = await db.get(SourceClaim, row.preferred_claim_id) if row.preferred_claim_id else None
    if preferred is None and row.preferred_article_id:
        preferred = next(
            (claim for claim in (left, right) if claim.article_id == row.preferred_article_id), None
        )
    current = await db.scalar(select(_current_result()).where(SourceConflict.id == row.id))
    return ConflictResult(
        id=row.id,
        left=snapshot_out(left),
        right=snapshot_out(right),
        assessment=PairAssessment.model_validate(row.assessment),
        outcome=PairAssessment.model_validate(row.assessment).outcome,
        processing_version=row.processing_version,
        job_id=row.job_id,
        stale=not bool(current),
        review_status=row.review_status,
        preferred_article_id=row.preferred_article_id,
        preferred_source=snapshot_out(preferred) if preferred is not None else None,
        review_note=row.review_note or "",
        reviewed_by=row.reviewed_by or "",
        reviewed_at=row.reviewed_at,
        created_at=row.created_at,
    )


def _normalized_question(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return " ".join(_GROUP_WORDS.sub(" ", normalized).split())


def _group_id(result: ConflictResult) -> str:
    left_question = _normalized_question(result.left.question)
    right_question = _normalized_question(result.right.question)
    if left_question and left_question == right_question:
        roles = ",".join(sorted({result.left.audience, result.right.audience}))
        digest = hashlib.sha256(f"{roles}\0{left_question}".encode()).hexdigest()[:24]
        return f"question-{digest}"
    return f"pair-{result.id}"


def _group(results: list[ConflictResult]) -> ConflictGroup:
    ordered = sorted(results, key=lambda item: (item.created_at, item.id))
    sources: dict[str, ConflictGroupSource] = {}
    for result in ordered:
        for source, quote in (
            (result.left, result.assessment.left_quote),
            (result.right, result.assessment.right_quote),
        ):
            existing = sources.get(source.article_id)
            if existing is None or len(quote) > len(existing.quote):
                sources[source.article_id] = ConflictGroupSource(source=source, quote=quote)

    statuses = {result.review_status for result in ordered}
    preferred_ids = {result.preferred_article_id for result in ordered}
    review_status = "unreviewed"
    preferred_article_id = None
    if statuses == {"dismissed"}:
        review_status = "dismissed"
    elif statuses == {"confirmed"} and len(preferred_ids) == 1 and None not in preferred_ids:
        review_status = "confirmed"
        preferred_article_id = next(iter(preferred_ids))

    reviewed = max(
        (result for result in ordered if result.reviewed_at is not None),
        # None отсеян фильтром выше — mypy через генератор этого не видит
        key=lambda item: cast(datetime, item.reviewed_at),
        default=None,
    )
    question = ordered[0].left.question
    if _normalized_question(question) != _normalized_question(ordered[0].right.question):
        question = ordered[0].assessment.fact or question
    facts = list(dict.fromkeys(item.assessment.fact for item in ordered if item.assessment.fact))
    explanations = list(
        dict.fromkeys(item.assessment.explanation for item in ordered if item.assessment.explanation)
    )
    return ConflictGroup(
        id=_group_id(ordered[0]),
        result_ids=[item.id for item in ordered],
        question=question,
        fact="; ".join(facts),
        explanation="\n".join(explanations),
        sources=sorted(sources.values(), key=lambda item: item.source.article_id),
        review_status=review_status,
        preferred_article_id=preferred_article_id,
        review_note=reviewed.review_note if reviewed is not None else "",
        reviewed_by=reviewed.reviewed_by if reviewed is not None else "",
        reviewed_at=reviewed.reviewed_at if reviewed is not None else None,
        created_at=max(item.created_at for item in ordered),
    )


class SourceConflictRegistry(SourceConflictJobs):
    async def snapshot_articles(self, article_ids: list[str]) -> list[SourceSnapshot]:
        if len(article_ids) > MAX_SNAPSHOTS:
            raise ValueError("snapshot batch is limited to 500 articles")
        async with self.session_factory() as db:
            articles = (
                await db.scalars(
                    select(KBArticle).where(KBArticle.id.in_(article_ids), KBArticle.status == "active")
                )
            ).all()
            by_id = {article.id: article for article in articles if auditable_source(article.origin)}
            snapshots = [
                snapshot_out(await _claim(db, by_id[article_id]))
                for article_id in dict.fromkeys(article_ids)
                if article_id in by_id
            ]
            await db.commit()
            return snapshots

    async def get_pair(self, left_id: str, right_id: str, processing_version: str) -> ConflictResult | None:
        async with self.session_factory() as db:
            row = (
                await db.scalars(_pair_stmt(left_id, right_id, processing_version).where(_current_pair()))
            ).one_or_none()
            result = await _result(db, row) if row else None
            return result if result and not result.stale else None

    async def record_pair(
        self,
        left: SourceSnapshot,
        right: SourceSnapshot,
        assessment: PairAssessment,
        processing_version: str,
        job_id: str | None = None,
    ) -> ConflictResult:
        if left.article_id == right.article_id:
            raise ValueError("cannot compare an article to itself")
        if not processing_version or len(processing_version) > VERSION_MAX_LENGTH:
            raise ValueError("processing_version must contain 1..128 characters")
        if left.id > right.id:
            left, right = right, left
            assessment = assessment.model_copy(
                update={
                    "left_quote": assessment.right_quote,
                    "right_quote": assessment.left_quote,
                    "left_scope_quote": assessment.right_scope_quote,
                    "right_scope_quote": assessment.left_scope_quote,
                }
            )
        async with self.session_factory() as db:
            row = await self._save_pair(db, left, right, assessment, (processing_version, job_id))
            await db.commit()
            return await _result(db, row)

    async def _save_pair(
        self,
        db: AsyncSession,
        left: SourceSnapshot,
        right: SourceSnapshot,
        assessment: PairAssessment,
        context: tuple[str, str | None],
    ) -> SourceConflict:
        stored_left = await db.get(SourceClaim, left.id)
        stored_right = await db.get(SourceClaim, right.id)
        if stored_left is None or stored_right is None:
            raise ValueError("unknown snapshot")
        if snapshot_out(stored_left) != left or snapshot_out(stored_right) != right:
            raise ValueError("snapshot differs from durable evidence")
        _validate_quotes(assessment, stored_left, stored_right)
        version, job_id = context
        stmt = _pair_stmt(left.id, right.id, version)
        existing = (await db.scalars(stmt)).one_or_none()
        if existing:
            return existing
        row = SourceConflict(
            left_id=left.id,
            right_id=right.id,
            assessment=assessment.model_dump(),
            outcome=assessment.outcome,
            processing_version=version,
            job_id=job_id,
        )
        try:
            async with db.begin_nested():
                db.add(row)
                await db.flush()
        except IntegrityError:
            return (await db.scalars(stmt)).one()
        return row

    async def get_result(self, result_id: str) -> ConflictResult | None:
        async with self.session_factory() as db:
            row = await db.get(SourceConflict, result_id)
            return await _result(db, row) if row else None

    async def list_results(  # noqa: PLR0913 — independently optional result filters
        self,
        *,
        article_id: str | None = None,
        outcome: Outcome | None = None,
        reviewed: bool | None = None,
        include_stale: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ConflictResult]:
        if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
            raise ValueError("invalid pagination")
        stmt = select(SourceConflict)
        if not include_stale:
            stmt = stmt.where(_current_result())
        if outcome:
            stmt = stmt.where(SourceConflict.outcome == outcome)
        if reviewed is True:
            stmt = stmt.where(SourceConflict.review_status != "unreviewed")
        elif reviewed is False:
            stmt = stmt.where(SourceConflict.review_status == "unreviewed")
        if article_id:
            claim_ids = select(SourceClaim.id).where(SourceClaim.article_id == article_id)
            stmt = stmt.where(
                or_(SourceConflict.left_id.in_(claim_ids), SourceConflict.right_id.in_(claim_ids))
            )
        async with self.session_factory() as db:
            rows = (
                await db.scalars(
                    stmt.order_by(SourceConflict.created_at.desc(), SourceConflict.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
            results = [await _result(db, row) for row in rows]
            return [result for result in results if include_stale or not result.stale]

    async def _groups(self, db: AsyncSession) -> list[ConflictGroup]:
        rows = (
            await db.scalars(
                select(SourceConflict)
                .where(
                    SourceConflict.outcome == "conflict",
                    _current_result(),
                )
                .order_by(SourceConflict.created_at.desc(), SourceConflict.id)
            )
        ).all()
        grouped: dict[str, list[ConflictResult]] = {}
        for row in rows:
            result = await _result(db, row)
            grouped.setdefault(_group_id(result), []).append(result)
        return sorted(
            (_group(results) for results in grouped.values()),
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

    async def list_groups(
        self, *, reviewed: bool | None = None, offset: int = 0, limit: int = 50
    ) -> list[ConflictGroup]:
        if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
            raise ValueError("invalid pagination")
        async with self.session_factory() as db:
            groups = await self._groups(db)
        if reviewed is True:
            groups = [group for group in groups if group.review_status != "unreviewed"]
        elif reviewed is False:
            groups = [group for group in groups if group.review_status == "unreviewed"]
        return groups[offset : offset + limit]

    async def get_group(self, group_id: str) -> ConflictGroup | None:
        async with self.session_factory() as db:
            return next((group for group in await self._groups(db) if group.id == group_id), None)

    async def review_group(self, group_id: str, body: ConflictReview, actor: str) -> ConflictGroup:
        async with self.session_factory() as db:
            group = next((item for item in await self._groups(db) if item.id == group_id), None)
            if group is None:
                raise LookupError("Conflict group not found")
            preferred_claim_id = None
            if body.status == "confirmed":
                selected = next(
                    (
                        item.source
                        for item in group.sources
                        if item.source.article_id == body.preferred_article_id
                    ),
                    None,
                )
                if selected is None:
                    raise ValueError("Preferred article must belong to this conflict group")
                preferred_claim_id = selected.id
            # UPDATE возвращает CursorResult (у него есть rowcount), тип execute шире
            updated = cast(
                CursorResult[Any],
                await db.execute(
                    update(SourceConflict)
                    .where(
                        SourceConflict.id.in_(group.result_ids),
                        SourceConflict.outcome == "conflict",
                        _current_result(),
                    )
                    .values(
                        review_status=body.status,
                        preferred_article_id=body.preferred_article_id,
                        preferred_claim_id=preferred_claim_id,
                        review_note=body.note,
                        reviewed_by=actor,
                        reviewed_at=utcnow(),
                    )
                ),
            )
            if updated.rowcount != len(group.result_ids):
                await db.rollback()
                raise ValueError("Conflict group became stale during review")
            db.add(
                AuditLog(
                    actor=actor,
                    action="source_conflict.group_review",
                    entity=group_id,
                    payload={
                        "result_ids": group.result_ids,
                        "status": body.status,
                        "preferred_article_id": body.preferred_article_id,
                        "note": body.note,
                    },
                )
            )
            await db.commit()
        reviewed = await self.get_group(group_id)
        if reviewed is None:
            raise ValueError("Conflict group became stale during review")
        return reviewed

    async def review(self, result_id: str, body: ConflictReview, actor: str) -> ConflictResult:
        async with self.session_factory() as db:
            row = await db.get(SourceConflict, result_id)
            if row is None:
                raise LookupError("Comparison not found")
            if body.status == "confirmed":
                claims = [await db.get(SourceClaim, claim_id) for claim_id in (row.left_id, row.right_id)]
                valid_ids = {claim.article_id for claim in claims if claim is not None}
                if body.preferred_article_id not in valid_ids:
                    raise ValueError("Preferred article must belong to this comparison")
                preferred_claim_id = next(
                    claim.id
                    for claim in claims
                    if claim is not None and claim.article_id == body.preferred_article_id
                )
            else:
                preferred_claim_id = None
            reviewed = await db.scalar(
                update(SourceConflict)
                .where(
                    SourceConflict.id == result_id,
                    SourceConflict.outcome == "conflict",
                    _current_pair(),
                )
                .values(
                    review_status=body.status,
                    preferred_article_id=body.preferred_article_id,
                    preferred_claim_id=preferred_claim_id,
                    review_note=body.note,
                    reviewed_by=actor,
                    reviewed_at=utcnow(),
                )
                .returning(SourceConflict.id)
            )
            if reviewed is None:
                raise ValueError("Only a current conflict can be reviewed")
            db.add(
                AuditLog(
                    actor=actor,
                    action="source_conflict.review",
                    entity=result_id,
                    payload={
                        "status": body.status,
                        "preferred_article_id": body.preferred_article_id,
                        "note": body.note,
                    },
                )
            )
            await db.commit()
            await db.refresh(row)
            return await _result(db, row)

    async def get_current_conflicts(
        self,
        article_ids: list[str],
        audience: str | None = None,
        limit: int = 20,
    ) -> list[ConflictResult]:
        if len(article_ids) > MAX_SNAPSHOTS or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("Invalid conflict lookup bounds")
        if not article_ids:
            return []
        # Both sources must apply to the known role. Unknown role only permits
        # universal sources; the caller can clarify the role before a new lookup.
        from app.services.audience import normalize

        allowed = {"all", normalize(audience)}
        left = aliased(SourceClaim)
        right = aliased(SourceClaim)
        stmt = (
            select(SourceConflict)
            .join(left, left.id == SourceConflict.left_id)
            .join(
                right,
                right.id == SourceConflict.right_id,
            )
            .where(
                or_(left.article_id.in_(article_ids), right.article_id.in_(article_ids)),
                SourceConflict.outcome == "conflict",
                SourceConflict.review_status == "confirmed",
                left.audience.in_(allowed),
                right.audience.in_(allowed),
                _current_result(),
            )
            .order_by(SourceConflict.created_at.desc(), SourceConflict.id)
            .limit(limit)
        )
        async with self.session_factory() as db:
            rows = (await db.scalars(stmt)).all()
            results = [await _result(db, row) for row in rows]
            return [result for result in results if not result.stale]
