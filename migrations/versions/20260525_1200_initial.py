"""initial schema: review_runs and step_executions

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-25 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("pr_url", sa.String(length=512), nullable=False),
        sa.Column("repo_full_name", sa.String(length=256), nullable=False),
        sa.Column("pr_number", sa.BigInteger(), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("plan_name", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cost_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_review_runs_repo_pr",
        "review_runs",
        ["repo_full_name", "pr_number"],
    )
    op.create_index("ix_review_runs_status", "review_runs", ["status"])
    op.create_index("ix_review_runs_started_at", "review_runs", ["started_at"])

    op.create_table(
        "step_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_name", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "inputs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "outputs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_step_executions_run_id", "step_executions", ["run_id"])
    op.create_index("ix_step_executions_step_name", "step_executions", ["step_name"])


def downgrade() -> None:
    op.drop_index("ix_step_executions_step_name", table_name="step_executions")
    op.drop_index("ix_step_executions_run_id", table_name="step_executions")
    op.drop_table("step_executions")
    op.drop_index("ix_review_runs_started_at", table_name="review_runs")
    op.drop_index("ix_review_runs_status", table_name="review_runs")
    op.drop_index("ix_review_runs_repo_pr", table_name="review_runs")
    op.drop_table("review_runs")
