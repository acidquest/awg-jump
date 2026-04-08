"""Add routing settings singleton

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "routing_settings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("invert_geoip", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        "INSERT INTO routing_settings (id, invert_geoip, created_at, updated_at) "
        "VALUES (1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )


def downgrade() -> None:
    op.drop_table("routing_settings")
