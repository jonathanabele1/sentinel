"""SQLAlchemy 2.0 async ORM models.

The two core tables: review_runs and step_executions. They are the audit trail
the whole orchestrator depends on; everything else is built on top.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base. Alembic discovers tables via Base.metadata."""


class ReviewRun(Base):
    """One execution of the review pipeline against a PR."""

    __tablename__ = "review_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pr_url: Mapped[str] = mapped_column(String(512), nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    pr_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    # GitHub App installation_id; needed for outbound API calls during the run.
    # Nullable for backward compatibility with rows created before this column existed.
    installation_id: Mapped[int | None] = mapped_column(BigInteger)
    plan_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    cost_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    request_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list[StepExecution]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="StepExecution.started_at",
    )

    __table_args__ = (
        Index("ix_review_runs_repo_pr", "repo_full_name", "pr_number"),
        Index("ix_review_runs_status", "status"),
        Index("ix_review_runs_started_at", "started_at"),
    )


class StepExecution(Base):
    """One step inside a ReviewRun. Inputs/outputs snapshotted for replay."""

    __tablename__ = "step_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("review_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    outputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(BigInteger)
    tokens_in: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[ReviewRun] = relationship(back_populates="steps")

    __table_args__ = (
        Index("ix_step_executions_run_id", "run_id"),
        Index("ix_step_executions_step_name", "step_name"),
    )


class ReviewFinding(Base):
    """A single issue surfaced by a specialist reviewer.

    One row per finding regardless of whether it was posted as an inline
    comment. Below-threshold findings are kept for the eval harness and the
    feedback loop.
    """

    __tablename__ = "review_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("review_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer: Mapped[str] = mapped_column(String(32), nullable=False)
    file: Mapped[str] = mapped_column(String(512), nullable=False)
    line_start: Mapped[int] = mapped_column(Integer, nullable=False)
    line_end: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    posted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    feedback: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_review_findings_run_id", "run_id"),
        Index("ix_review_findings_reviewer", "reviewer"),
        Index("ix_review_findings_severity_posted", "severity", "posted"),
    )
