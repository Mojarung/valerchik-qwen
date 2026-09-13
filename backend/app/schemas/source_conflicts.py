"""Contracts for the bounded, manual source audit; no chat policy is implied."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_ARTICLE_ID_LENGTH = 32

Outcome = Literal["conflict", "compatible", "uncertain"]


class SourceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    article_id: str
    source_version: str
    content_hash: str
    question: str
    answer: str
    audience: str
    kind: str
    category: str
    source: str
    doc_key: str
    origin: dict[str, Any]
    created_at: datetime


class PairAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same_fact: Literal["yes", "no", "unknown"]
    conditions: Literal["overlap", "disjoint", "unknown"]
    claims: Literal["incompatible", "compatible", "unknown"]
    material: Literal["yes", "no", "unknown"]
    # Defaults keep historical JSON readable. New detector responses must set
    # these fields explicitly; the detector checks model_fields_set.
    scope_alignment: Literal["explicit", "different", "unknown"] = "explicit"
    coexistence: Literal["possible", "impossible", "unknown"] = "impossible"
    fact: str = Field(default="", max_length=2000)
    conditions_text: str = Field(default="", max_length=4000)
    left_scope_quote: str = Field(default="", max_length=8000)
    right_scope_quote: str = Field(default="", max_length=8000)
    coexistence_reason: str = Field(default="", max_length=4000)
    left_quote: str = Field(default="", max_length=8000)
    right_quote: str = Field(default="", max_length=8000)
    explanation: str = Field(default="", max_length=4000)

    @property
    def outcome(self) -> Outcome:
        if (
            self.same_fact == "no"
            or self.conditions == "disjoint"
            or self.claims == "compatible"
            or self.scope_alignment == "different"
            or self.coexistence == "possible"
        ):
            return "compatible"
        if self.material == "no":
            return "compatible"
        if (self.same_fact, self.conditions, self.claims, self.material) == (
            "yes",
            "overlap",
            "incompatible",
            "yes",
        ) and (self.scope_alignment, self.coexistence) == ("explicit", "impossible"):
            return "conflict"
        return "uncertain"


class AuditLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_articles: int = Field(default=50, ge=2, le=200)
    max_pairs: int = Field(default=100, ge=1, le=500)
    top_k: int = Field(default=5, ge=1, le=50)
    max_article_chars: int = Field(default=8000, ge=100, le=20000)
    timeout_seconds: float = Field(default=300, gt=0, le=3600)
    request_timeout_seconds: float = Field(default=60, gt=0, le=120)
    max_calls: int = Field(default=100, ge=1, le=4000)
    max_output_tokens: int = Field(default=4000, ge=100, le=4000)


class AuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligible_articles: int = 0
    selected_articles: int = 0
    skipped_long_articles: int = 0
    skipped_ineligible_articles: int = 0
    pool_limited: bool = False
    candidate_pairs_truncated: bool = False
    candidate_pairs: int = 0
    evaluated_pairs: int = 0
    cached_pairs: int = 0
    stale_pairs: int = 0
    conflicts: int = 0
    compatible: int = 0
    uncertain: int = 0
    attempted_calls: int = 0
    failures: int = 0
    stop_reason: str = ""
    next_cursor: str = ""
    errors: list[str] = Field(default_factory=list)


class ConflictResult(BaseModel):
    id: str
    left: SourceSnapshot
    right: SourceSnapshot
    assessment: PairAssessment
    outcome: Outcome
    processing_version: str
    job_id: str | None
    stale: bool
    review_status: str = "unreviewed"
    preferred_article_id: str | None = None
    preferred_source: SourceSnapshot | None = None
    review_note: str = ""
    reviewed_by: str = ""
    reviewed_at: datetime | None = None
    created_at: datetime


class ConflictGroupSource(BaseModel):
    source: SourceSnapshot
    quote: str = ""


class ConflictGroup(BaseModel):
    id: str
    result_ids: list[str]
    question: str
    fact: str
    explanation: str
    sources: list[ConflictGroupSource]
    review_status: str = "unreviewed"
    preferred_article_id: str | None = None
    review_note: str = ""
    reviewed_by: str = ""
    reviewed_at: datetime | None = None
    created_at: datetime


class AuditJobOut(BaseModel):
    id: str
    scope: dict[str, Any]
    limits: AuditLimits
    processing_version: str
    actor: str
    status: str
    report: AuditReport
    owner: str | None
    lease_until: datetime | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class ConflictReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["unreviewed", "confirmed", "dismissed"]
    preferred_article_id: str | None = Field(default=None, max_length=MAX_ARTICLE_ID_LENGTH)
    note: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_preference(self) -> ConflictReview:
        if self.status == "confirmed" and not self.preferred_article_id:
            raise ValueError("Confirmed conflict requires the preferred article")
        if self.status != "confirmed" and self.preferred_article_id is not None:
            raise ValueError("Only a confirmed conflict can have a preferred article")
        return self


class ConflictAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    article_ids: list[str] = Field(min_length=2, max_length=200)
    limits: AuditLimits = Field(default_factory=AuditLimits)

    @model_validator(mode="after")
    def validate_scope(self) -> ConflictAuditRequest:
        if len(self.article_ids) > self.limits.max_articles:
            raise ValueError("Selected article count exceeds max_articles")
        if len(set(self.article_ids)) != len(self.article_ids):
            raise ValueError("Article IDs must be unique")
        if any(not value.strip() or len(value) > MAX_ARTICLE_ID_LENGTH for value in self.article_ids):
            raise ValueError("Article IDs must contain 1..32 characters")
        return self


class AutoAuditEntry(BaseModel):
    id: str
    article_id: str
    question: str
    source_version: str
    status: Literal["pending", "running", "retryable", "blocked", "skipped", "done", "superseded"]
    attempts: int
    job_id: str | None
    report: AuditReport | None = None
    last_error: str
    available_at: datetime
    updated_at: datetime


class AutoAuditStatus(BaseModel):
    enabled: bool
    configured: bool
    blocked_reason: str = ""
    daily_call_limit: int
    calls_used_today: int
    remaining_calls_today: int
    eligible_articles: int
    unchecked_articles: int
    estimated_backfill_calls: int
    estimated_pending_calls: int
    counts: dict[str, int]
    entries: list[AutoAuditEntry]


class AutoAuditBackfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all: Literal[True] = True


class AutoAuditBackfillResponse(BaseModel):
    scanned: int
    eligible: int
    enqueued: int
    estimated_calls: int
