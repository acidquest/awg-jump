"""Add GeoIP upstream node role

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_tables = set(inspector.get_table_names())
    if "upstream_nodes" not in existing_tables:
        return

    existing_columns = {column["name"] for column in inspector.get_columns("upstream_nodes")}
    if "is_geoip" not in existing_columns:
        op.add_column(
            "upstream_nodes",
            sa.Column("is_geoip", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_tables = set(inspector.get_table_names())
    if "upstream_nodes" not in existing_tables:
        return

    existing_columns = {column["name"] for column in inspector.get_columns("upstream_nodes")}
    if "is_geoip" in existing_columns:
        op.drop_column("upstream_nodes", "is_geoip")
