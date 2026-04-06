"""Add private_key to peers

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-06

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("peers") as batch_op:
        batch_op.add_column(sa.Column("private_key", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("peers") as batch_op:
        batch_op.drop_column("private_key")
