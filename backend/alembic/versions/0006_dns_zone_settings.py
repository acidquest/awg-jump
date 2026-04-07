"""Add dns zone settings

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dns_zone_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("zone", sa.String(length=16), nullable=False, unique=True),
        sa.Column("dns_servers", sa.Text(), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.execute(
        "INSERT INTO dns_zone_settings (zone, dns_servers, description, updated_at) "
        "VALUES "
        "('local', '[\"77.88.8.8\"]', 'DNS for local routing zone (RU/etc)', CURRENT_TIMESTAMP), "
        "('vpn', '[\"1.1.1.1\", \"8.8.8.8\"]', 'DNS for VPN routing zone', CURRENT_TIMESTAMP)"
    )


def downgrade() -> None:
    op.drop_table("dns_zone_settings")
