from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TrafficSourceMode(str, Enum):
    localhost = "localhost"
    selected_cidr = "selected_cidr"
    selected_hosts = "selected_hosts"


class TunnelStatus(str, Enum):
    stopped = "stopped"
    starting = "starting"
    running = "running"
    error = "error"


class RuntimeMode(str, Enum):
    auto = "auto"
    kernel = "kernel"
    userspace = "userspace"


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    password_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class GatewaySettings(Base):
    __tablename__ = "gateway_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    ui_language: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    runtime_mode: Mapped[str] = mapped_column(String(16), default=RuntimeMode.auto.value, nullable=False)
    traffic_source_mode: Mapped[str] = mapped_column(
        String(32), default=TrafficSourceMode.localhost.value, nullable=False
    )
    allowed_client_cidrs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_client_hosts: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    active_entry_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("entry_nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    tunnel_status: Mapped[str] = mapped_column(String(32), default=TunnelStatus.stopped.value)
    tunnel_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tunnel_last_applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    active_entry_node: Mapped["EntryNode | None"] = relationship(foreign_keys=[active_entry_node_id])


class EntryNode(Base):
    __tablename__ = "entry_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_conf: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(String(256), nullable=False)
    endpoint_host: Mapped[str] = mapped_column(String(256), nullable=False)
    endpoint_port: Mapped[int] = mapped_column(Integer, nullable=False)
    probe_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    public_key: Mapped[str] = mapped_column(String(128), nullable=False)
    private_key: Mapped[str] = mapped_column(String(128), nullable=False)
    preshared_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tunnel_address: Mapped[str] = mapped_column(String(64), nullable=False)
    dns_servers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_ips: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    persistent_keepalive: Mapped[int | None] = mapped_column(Integer, nullable=True)
    obfuscation: Mapped[dict[str, int | str]] = mapped_column(JSON, default=dict, nullable=False)
    latest_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_latency_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RoutingPolicy(Base):
    __tablename__ = "routing_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    geoip_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    geoip_countries: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["ru"], nullable=False)
    manual_prefixes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    geoip_ipset_name: Mapped[str] = mapped_column(String(64), default="gateway_geoip_local")
    invert_geoip: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_policy: Mapped[str] = mapped_column(String(16), default="vpn", nullable=False)
    kill_switch_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    strict_mode: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DnsUpstream(Base):
    __tablename__ = "dns_upstreams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zone: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    servers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    description: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DnsDomainRule(Base):
    __tablename__ = "dns_domain_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True, nullable=False)
    zone: Mapped[str] = mapped_column(String(16), default="local", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="backup", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
