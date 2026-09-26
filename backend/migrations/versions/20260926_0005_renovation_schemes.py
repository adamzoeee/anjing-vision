"""Add renovation schemes.

Revision ID: 20260926_0005
Revises: 20260809_0004
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260926_0005"
down_revision: str | None = "20260809_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "renovation_schemes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scan_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("edits", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("based_on", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_renovation_schemes_scan_id", "renovation_schemes", ["scan_id"])


def downgrade() -> None:
    op.drop_index("ix_renovation_schemes_scan_id", table_name="renovation_schemes")
    op.drop_table("renovation_schemes")
