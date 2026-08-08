"""Reconcile databases created before Alembic was introduced.

Revision ID: 20260808_0003
Revises: 20260807_0002
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260808_0003"
down_revision: str | None = "20260807_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_index(inspector, table: str, columns: list[str]) -> bool:
    return any(
        item["column_names"] == columns
        for item in inspector.get_indexes(table)
    )


def _has_unique_constraint(inspector, table: str, columns: list[str]) -> bool:
    return any(
        item["column_names"] == columns
        for item in inspector.get_unique_constraints(table)
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    expected_tables = {"organizations", "users", "projects", "scans", "reports"}
    if not expected_tables.issubset(inspector.get_table_names()):
        missing = sorted(expected_tables.difference(inspector.get_table_names()))
        raise RuntimeError(f"旧数据库缺少必要表，不能自动迁移: {', '.join(missing)}")

    for name, table, columns in (
        ("ix_users_org_id", "users", ["org_id"]),
        ("ix_projects_org_id", "projects", ["org_id"]),
        ("ix_scans_project_id", "scans", ["project_id"]),
    ):
        if not _has_index(inspector, table, columns):
            op.create_index(name, table, columns, unique=False)

    inspector = sa.inspect(bind)
    if not _has_unique_constraint(inspector, "reports", ["scan_id"]):
        duplicate_scan_ids = bind.execute(
            sa.text(
                "SELECT scan_id FROM reports "
                "GROUP BY scan_id HAVING COUNT(*) > 1 ORDER BY scan_id LIMIT 10"
            )
        ).scalars().all()
        if duplicate_scan_ids:
            values = ", ".join(str(item) for item in duplicate_scan_ids)
            raise RuntimeError(
                "reports 中存在同一扫描的重复报告，无法建立一对一约束；"
                f"请先备份并处理这些 scan_id: {values}"
            )
        with op.batch_alter_table("reports") as batch_op:
            batch_op.create_unique_constraint("uq_reports_scan_id", ["scan_id"])


def downgrade() -> None:
    # This reconciliation may be a no-op on databases originally created by 0001.
    # Removing shared baseline indexes or constraints would make those databases
    # inconsistent, so the data-integrity reconciliation is intentionally irreversible.
    pass
