"""Persist bounded proposal narrative and independently computed evidence.

Revision ID: 20261016_0007
Revises: 20261016_0006
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261016_0007"
down_revision: str | None = "20261016_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "renovation_schemes",
        sa.Column("proposal_meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("renovation_schemes", "proposal_meta")
