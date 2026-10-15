"""Persistent upstream connection diagnostics."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for name, columns in [
        ("node_connection_incidents", [sa.Column("started_at", sa.DateTime(), nullable=False), sa.Column("ended_at", sa.DateTime())]),
        ("node_connection_attempts", [sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("applied", sa.Boolean(), nullable=False), sa.Column("error", sa.Text())]),
    ]:
        if name not in tables:
            op.create_table(name, sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("node_id", sa.Integer(), sa.ForeignKey("upstream_nodes.id", ondelete="SET NULL")),
                sa.Column("node_name", sa.String(128), nullable=False), *columns)
            date_column = "started_at" if name.endswith("incidents") else "created_at"
            op.create_index(f"ix_{name}_{date_column}", name, [date_column])


def downgrade():
    op.drop_table("node_connection_attempts")
    op.drop_table("node_connection_incidents")
