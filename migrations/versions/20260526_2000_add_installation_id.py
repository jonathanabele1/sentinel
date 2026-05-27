"""add installation_id to review_runs

Revision ID: 0002_add_installation_id
Revises: 0001_initial
Create Date: 2026-05-26 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_installation_id"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "review_runs",
        sa.Column("installation_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_runs", "installation_id")
