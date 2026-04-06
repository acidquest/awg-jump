"""Add private_key to upstream_nodes, make awg_address nullable

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-06

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("upstream_nodes") as batch_op:
        batch_op.add_column(sa.Column("private_key", sa.String(64), nullable=True))
        batch_op.alter_column("awg_address", existing_type=sa.String(64), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("upstream_nodes") as batch_op:
        batch_op.drop_column("private_key")
        batch_op.alter_column("awg_address", existing_type=sa.String(64), nullable=False)
