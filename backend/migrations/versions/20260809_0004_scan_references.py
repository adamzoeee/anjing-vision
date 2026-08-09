"""Add metric reference measurements to scans.

Revision ID: 20260809_0004
Revises: 20260808_0003
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260809_0004"
down_revision: str | None = "20260808_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default 同时回填旧扫描，避免旧行在 ScanOut 序列化时出现 null。
    op.add_column(
        "scans",
        sa.Column(
            "reference_measurements",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("scans", "reference_measurements")
