from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from backend.database import Base


class Peer(Base):
    __tablename__ = "peers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    interface_id = Column(Integer, ForeignKey("interfaces.id", ondelete="CASCADE"), nullable=False)

    name = Column(String(128), nullable=False, default="")
    private_key = Column(String(64), nullable=True)   # хранится для генерации клиентского конфига
    public_key = Column(String(64), unique=True, nullable=False)
    preshared_key = Column(String(64), nullable=True)

    # IP-адрес(а), разрешённые для данного пира
    allowed_ips = Column(String(256), nullable=False, default="")

    # Для клиентских конфигов: IP адрес пира внутри туннеля
    tunnel_address = Column(String(64), nullable=True)

    persistent_keepalive = Column(Integer, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)

    last_handshake = Column(DateTime, nullable=True)
    rx_bytes = Column(Integer, nullable=True, default=0)
    tx_bytes = Column(Integer, nullable=True, default=0)
    client_code = Column(Integer, nullable=True)
    client_kind = Column(String(64), nullable=True)
    client_reported_ip = Column(String(64), nullable=True)
    client_reported_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Связи ────────────────────────────────────────────────────────────
    interface = relationship("Interface", back_populates="peers", lazy="selectin")
