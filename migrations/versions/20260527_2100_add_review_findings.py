"""add review_findings table

Revision ID: 0003_add_review_findings
Revises: 0002_add_installation_id
Create Date: 2026-05-27 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_add_review_findings"
down_revision: str | None = "0002_add_installation_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Which specialist reviewer produced it. Always one of the
        # ReviewerCategory literal values; not enforced at the DB level
        # because Postgres ENUMs are a migration pain when adding values.
        sa.Column("reviewer", sa.String(length=32), nullable=False),
        sa.Column("file", sa.String(length=512), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=False),
        sa.Column("line_end", sa.Integer(), nullable=False),
        # info | low | medium | high | critical
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        # Stored as a fraction (0.0-1.0). Real numbers because we'll
        # compute calibration curves on these in Week 5.
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column(
            "posted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # Reaction tracking for the feedback loop (Week 7). NULL until a
        # human gives this finding a 👍/👎 on the PR comment.
        sa.Column(
            "feedback",
            sa.String(length=16),
            nullable=True,
            comment="thumbs_up | thumbs_down | null",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_review_findings_run_id",
        "review_findings",
        ["run_id"],
    )
    op.create_index(
        "ix_review_findings_reviewer",
        "review_findings",
        ["reviewer"],
    )
    # For "show me all high+ findings across the last N runs" queries
    # that Week 5 eval and Week 7 dashboards will run.
    op.create_index(
        "ix_review_findings_severity_posted",
        "review_findings",
        ["severity", "posted"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_findings_severity_posted", table_name="review_findings")
    op.drop_index("ix_review_findings_reviewer", table_name="review_findings")
    op.drop_index("ix_review_findings_run_id", table_name="review_findings")
    op.drop_table("review_findings")
