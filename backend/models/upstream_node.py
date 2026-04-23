from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import enum

from backend.database import Base


class NodeStatus(str, enum.Enum):
    pending = "pending"
    deploying = "deploying"
    online = "online"
    degraded = "degraded"
    offline = "offline"
    error = "error"


class ProvisioningMode(str, enum.Enum):
    managed = "managed"
    manual = "manual"


class DeployStatus(str, enum.Enum):
    running = "running"
    success = "success"
    failed = "failed"


class UpstreamNode(Base):
    __tablename__ = "upstream_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)

    # Подключение
    host = Column(String(256), nullable=False)       # IP или hostname
    ssh_port = Column(Integer, nullable=False, default=22)
    awg_port = Column(Integer, nullable=False, default=51821)
    provisioning_mode = Column(
        SAEnum(ProvisioningMode),
        nullable=False,
        default=ProvisioningMode.managed,
    )

    # AWG параметры ноды
    awg_address = Column(String(64), nullable=True)   # 10.20.0.x/32, заполняется при деплое
    probe_ip = Column(String(64), nullable=True)
    public_key = Column(String(64), nullable=True)    # AWG pubkey, заполняется при деплое
    private_key = Column(String(64), nullable=True)   # AWG private key (хранится для redeploy)
    preshared_key = Column(String(64), nullable=True)
    raw_conf = Column(Text, nullable=True)
    client_dns = Column(String(256), nullable=True)
    client_allowed_ips = Column(String(256), nullable=True)
    client_keepalive = Column(Integer, nullable=True)

    # Статус
    status = Column(
        SAEnum(NodeStatus),
        nullable=False,
        default=NodeStatus.pending,
    )
    is_active = Column(Boolean, nullable=False, default=False)  # только одна нода активна
    priority = Column(Integer, nullable=False, default=100)      # для failover порядка

    # Метки времени
    last_seen = Column(DateTime, nullable=True)
    last_deploy = Column(DateTime, nullable=True)

    # Метрики
    rx_bytes = Column(Integer, nullable=True, default=0)
    tx_bytes = Column(Integer, nullable=True, default=0)
    latency_ms = Column(Float, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Связи ────────────────────────────────────────────────────────────
    deploy_logs = relationship("DeployLog", back_populates="node", cascade="all, delete-orphan")
    shared_peers = relationship("NodePeer", back_populates="node", cascade="all, delete-orphan")


class DeployLog(Base):
    __tablename__ = "deploy_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    node_id = Column(Integer, ForeignKey("upstream_nodes.id", ondelete="CASCADE"), nullable=False)

    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime, nullable=True)

    status = Column(
        SAEnum(DeployStatus),
        nullable=False,
        default=DeployStatus.running,
    )
    log_output = Column(Text, nullable=True, default="")

    # ── Связи ────────────────────────────────────────────────────────────
    node = relationship("UpstreamNode", back_populates="deploy_logs")


class NodePeer(Base):
    __tablename__ = "node_peers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    node_id = Column(Integer, ForeignKey("upstream_nodes.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(128), nullable=False, default="")
    private_key = Column(String(64), nullable=False)
    public_key = Column(String(64), nullable=False)
    preshared_key = Column(String(64), nullable=True)
    tunnel_address = Column(String(64), nullable=False)
    allowed_ips = Column(String(256), nullable=False, default="0.0.0.0/0")
    persistent_keepalive = Column(Integer, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    node = relationship("UpstreamNode", back_populates="shared_peers")
