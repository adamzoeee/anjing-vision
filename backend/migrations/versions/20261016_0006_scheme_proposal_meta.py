"""Persist bounded proposal narrative and independently computed evidence.

Revision ID: 20261016_0006
Revises: 20260926_0005
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261016_0006"
down_revision: str | None = "20260926_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "renovation_schemes",
        sa.Column("proposal_meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("renovation_schemes", "proposal_meta")
