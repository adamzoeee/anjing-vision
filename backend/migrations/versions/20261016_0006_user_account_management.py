"""Add user account management fields.

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
    # 停用时间戳：NULL = 启用。保留账号与历史归属，不做物理删除。
    op.add_column("users", sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "updated_at")
    op.drop_column("users", "disabled_at")
