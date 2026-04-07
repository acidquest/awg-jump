"""GeoIP local multi-country zones

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "geoip_sources",
        sa.Column("display_name", sa.String(length=128), nullable=False, server_default=""),
    )
    op.execute("UPDATE geoip_sources SET display_name = name WHERE display_name = ''")
    op.execute(
        "UPDATE geoip_sources SET ipset_name = 'geoip_local' "
        "WHERE ipset_name = 'geoip_ru'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE geoip_sources SET ipset_name = 'geoip_ru' "
        "WHERE ipset_name = 'geoip_local'"
    )
    op.drop_column("geoip_sources", "display_name")
