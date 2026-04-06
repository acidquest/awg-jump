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

    # AWG параметры ноды
    awg_address = Column(String(64), nullable=False)  # 10.20.0.x/32
    public_key = Column(String(64), nullable=True)    # AWG pubkey, заполняется при деплое
    preshared_key = Column(String(64), nullable=True)

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
