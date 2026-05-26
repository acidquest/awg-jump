"""Add per-node upstream tunnel network

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(upstream_nodes)").fetchall()}
    if "tunnel_network" not in columns:
        op.add_column("upstream_nodes", sa.Column("tunnel_network", sa.String(64), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(upstream_nodes)").fetchall()}
    if "tunnel_network" in columns:
        op.drop_column("upstream_nodes", "tunnel_network")
