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
    op.create_index("ix_system_metrics_collected_at", "system_metrics", ["collected_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_system_metrics_collected_at", table_name="system_metrics")
    op.drop_table("system_metrics")
