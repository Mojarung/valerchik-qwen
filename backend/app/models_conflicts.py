"""Durable evidence, audit jobs and the automatic article queue."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.models import new_id, utcnow


class SourceClaim(Base):
    __tablename__ = "source_claims"
    __table_args__ = (UniqueConstraint("article_id", "source_version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # Evidence must survive article purge. This intentionally is not a live FK.
    article_id: Mapped[str] = mapped_column(String(32), index=True)
    source_version: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32))
    doc_key: Mapped[str] = mapped_column(String(64))
    origin: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SourceConflict(Base):
    __tablename__ = "source_conflicts"
    __table_args__ = (
        UniqueConstraint("left_id", "right_id", "processing_version"),
        Index("ix_source_conflicts_outcome_created", "outcome", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    left_id: Mapped[str] = mapped_column(ForeignKey("source_claims.id"), index=True)
    right_id: Mapped[str] = mapped_column(ForeignKey("source_claims.id"), index=True)
    assessment: Mapped[dict[str, Any]] = mapped_column(JSON)
    outcome: Mapped[str] = mapped_column(String(16))
    processing_version: Mapped[str] = mapped_column(String(128))
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    review_status: Mapped[str] = mapped_column(String(32), default="unreviewed")
    preferred_article_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    preferred_claim_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_claims.id"), nullable=True, index=True
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SourceConflictAuditJob(Base):
    __tablename__ = "source_conflict_audit_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    active_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    limits: Mapped[dict[str, Any]] = mapped_column(JSON)
    processing_version: Mapped[str] = mapped_column(String(128))
    actor: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SourceConflictAutoItem(Base):
    __tablename__ = "source_conflict_auto_items"
    __table_args__ = (
        UniqueConstraint("article_id", "source_version"),
        Index("ix_source_conflict_auto_ready", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    article_id: Mapped[str] = mapped_column(String(32), index=True)
    source_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class SourceConflictAutoControl(Base):
    __tablename__ = "source_conflict_auto_control"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    paused_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    budget_day: Mapped[str] = mapped_column(String(10), default="")
    calls: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
