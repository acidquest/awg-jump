"""Add dns_domains table for split DNS

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dns_domains",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("domain", sa.String(253), unique=True, nullable=False),
        sa.Column(
            "upstream",
            sa.Enum("yandex", "default", name="dnsupstream"),
            nullable=False,
            server_default="yandex",
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("dns_domains")
    op.execute("DROP TYPE IF EXISTS dnsupstream")
