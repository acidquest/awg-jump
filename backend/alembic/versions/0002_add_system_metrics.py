"""Add system metrics history

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_tables = set(inspector.get_table_names())
    if "system_metrics" not in existing_tables:
        op.create_table(
            "system_metrics",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("collected_at", sa.DateTime(), nullable=False),
            sa.Column("cpu_usage_percent", sa.Float(), nullable=False, server_default="0"),
            sa.Column("cpu_total_ticks", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cpu_idle_ticks", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("memory_total_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("memory_used_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("memory_free_bytes", sa.Integer(), nullable=False, server_default="0"),
        )

    existing_indexes = {index["name"] for index in inspector.get_indexes("system_metrics")}
    if "ix_system_metrics_collected_at" not in existing_indexes:
        op.create_index("ix_system_metrics_collected_at", "system_metrics", ["collected_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_tables = set(inspector.get_table_names())
    if "system_metrics" not in existing_tables:
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("system_metrics")}
    if "ix_system_metrics_collected_at" in existing_indexes:
        op.drop_index("ix_system_metrics_collected_at", table_name="system_metrics")

    op.drop_table("system_metrics")
