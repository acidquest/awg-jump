"""Baseline schema — all tables

Revision ID: 0001
Revises:
Create Date: 2026-04-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── interfaces ───────────────────────────────────────────────────────
    op.create_table(
        "interfaces",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(32), unique=True, nullable=False),
        sa.Column(
            "mode",
            sa.Enum("server", "client", name="interfacemode"),
            nullable=False,
            server_default="server",
        ),
        sa.Column("private_key", sa.String(64), nullable=False, server_default=""),
        sa.Column("public_key", sa.String(64), nullable=False, server_default=""),
        sa.Column("listen_port", sa.Integer, nullable=True),
        sa.Column("address", sa.String(64), nullable=False, server_default=""),
        sa.Column("dns", sa.String(128), nullable=True),
        sa.Column("endpoint", sa.String(256), nullable=True),
        sa.Column("preshared_key", sa.String(64), nullable=True),
        sa.Column("allowed_ips", sa.String(256), nullable=True),
        sa.Column("persistent_keepalive", sa.Integer, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        # Обфускация — junk (клиентская сторона)
        sa.Column("obf_jc", sa.Integer, nullable=True),
        sa.Column("obf_jmin", sa.Integer, nullable=True),
        sa.Column("obf_jmax", sa.Integer, nullable=True),
        # Обфускация — padding (симметричные)
        sa.Column("obf_s1", sa.Integer, nullable=True),
        sa.Column("obf_s2", sa.Integer, nullable=True),
        sa.Column("obf_s3", sa.Integer, nullable=True),
        sa.Column("obf_s4", sa.Integer, nullable=True),
        # Обфускация — headers (симметричные)
        sa.Column("obf_h1", sa.Integer, nullable=True),
        sa.Column("obf_h2", sa.Integer, nullable=True),
        sa.Column("obf_h3", sa.Integer, nullable=True),
        sa.Column("obf_h4", sa.Integer, nullable=True),
        sa.Column("obf_generated_at", sa.DateTime, nullable=True),
    )

    # ── peers ────────────────────────────────────────────────────────────
    op.create_table(
        "peers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "interface_id",
            sa.Integer,
            sa.ForeignKey("interfaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("private_key", sa.String(64), nullable=True),
        sa.Column("public_key", sa.String(64), unique=True, nullable=False),
        sa.Column("preshared_key", sa.String(64), nullable=True),
        sa.Column("allowed_ips", sa.String(256), nullable=False, server_default=""),
        sa.Column("tunnel_address", sa.String(64), nullable=True),
        sa.Column("persistent_keepalive", sa.Integer, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("last_handshake", sa.DateTime, nullable=True),
        sa.Column("rx_bytes", sa.Integer, nullable=True, server_default="0"),
        sa.Column("tx_bytes", sa.Integer, nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    # ── geoip_sources ────────────────────────────────────────────────────
    op.create_table(
        "geoip_sources",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("country_code", sa.String(8), nullable=False, server_default="ru"),
        sa.Column("ipset_name", sa.String(64), nullable=False, server_default="geoip_local"),
        sa.Column("last_updated", sa.DateTime, nullable=True),
        sa.Column("prefix_count", sa.Integer, nullable=True, server_default="0"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )

    # ── upstream_nodes ───────────────────────────────────────────────────
    op.create_table(
        "upstream_nodes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("host", sa.String(256), nullable=False),
        sa.Column("ssh_port", sa.Integer, nullable=False, server_default="22"),
        sa.Column("awg_port", sa.Integer, nullable=False, server_default="51821"),
        sa.Column("awg_address", sa.String(64), nullable=True),
        sa.Column("public_key", sa.String(64), nullable=True),
        sa.Column("private_key", sa.String(64), nullable=True),
        sa.Column("preshared_key", sa.String(64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "deploying", "online", "degraded", "offline", "error",
                name="nodestatus",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("last_seen", sa.DateTime, nullable=True),
        sa.Column("last_deploy", sa.DateTime, nullable=True),
        sa.Column("rx_bytes", sa.Integer, nullable=True, server_default="0"),
        sa.Column("tx_bytes", sa.Integer, nullable=True, server_default="0"),
        sa.Column("latency_ms", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    # ── deploy_logs ──────────────────────────────────────────────────────
    op.create_table(
        "deploy_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "node_id",
            sa.Integer,
            sa.ForeignKey("upstream_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column(
            "status",
            sa.Enum("running", "success", "failed", name="deploystatus"),
            nullable=False,
            server_default="running",
        ),
        sa.Column("log_output", sa.Text, nullable=True, server_default=""),
    )

    # ── dns_domains ──────────────────────────────────────────────────────
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

    # ── dns_zone_settings ────────────────────────────────────────────────
    op.create_table(
        "dns_zone_settings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("zone", sa.String(length=16), nullable=False, unique=True),
        sa.Column("dns_servers", sa.Text(), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # ── routing_rules ────────────────────────────────────────────────────
    op.create_table(
        "routing_rules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("ipset_name", sa.String(64), nullable=True),
        sa.Column("fwmark", sa.String(16), nullable=True),
        sa.Column("table_id", sa.Integer, nullable=True),
        sa.Column("via_interface", sa.String(32), nullable=True),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    # ── routing_settings ─────────────────────────────────────────────────
    op.create_table(
        "routing_settings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("invert_geoip", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.execute(
        "INSERT INTO dns_zone_settings (zone, dns_servers, description, updated_at) "
        "VALUES "
        "('local', '[\"77.88.8.8\"]', 'DNS for local routing zone (RU/etc)', CURRENT_TIMESTAMP), "
        "('vpn', '[\"1.1.1.1\", \"8.8.8.8\"]', 'DNS for VPN routing zone', CURRENT_TIMESTAMP)"
    )

    op.execute(
        "INSERT INTO routing_settings (id, invert_geoip, created_at, updated_at) "
        "VALUES (1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )


def downgrade() -> None:
    op.drop_table("routing_settings")
    op.drop_table("routing_rules")
    op.drop_table("dns_zone_settings")
    op.drop_table("dns_domains")
    op.drop_table("deploy_logs")
    op.drop_table("upstream_nodes")
    op.drop_table("geoip_sources")
    op.drop_table("peers")
    op.drop_table("interfaces")
    op.execute("DROP TYPE IF EXISTS dnsupstream")
