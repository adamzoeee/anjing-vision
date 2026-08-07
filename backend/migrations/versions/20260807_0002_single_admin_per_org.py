"""Ensure each organization has at most one administrator.

Revision ID: 20260807_0002
Revises: 20260807_0001
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260807_0002"
down_revision: str | None = "20260807_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_users_single_admin_per_org",
        "users",
        ["org_id"],
        unique=True,
        sqlite_where=sa.text("role = 'admin'"),
        postgresql_where=sa.text("role = 'admin'"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_single_admin_per_org", table_name="users")
