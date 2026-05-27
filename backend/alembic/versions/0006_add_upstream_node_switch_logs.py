"""Add upstream node switch logs

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "upstream_node_switch_logs" in existing_tables:
        return

    op.create_table(
        "upstream_node_switch_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("from_node_id", sa.Integer(), sa.ForeignKey("upstream_nodes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("from_node_name", sa.String(128), nullable=True),
        sa.Column("to_node_id", sa.Integer(), sa.ForeignKey("upstream_nodes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("to_node_name", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("switch_type", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("upstream_node_switch_logs")
