from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import enum

from backend.database import Base


class InterfaceMode(str, enum.Enum):
    server = "server"
    client = "client"


class Interface(Base):
    __tablename__ = "interfaces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), unique=True, nullable=False)  # awg0, awg1
    mode = Column(SAEnum(InterfaceMode), nullable=False, default=InterfaceMode.server)

    # Ключи
    private_key = Column(String(64), nullable=False, default="")
    public_key = Column(String(64), nullable=False, default="")

    # Сетевые параметры
    listen_port = Column(Integer, nullable=True)
    address = Column(String(64), nullable=False, default="")
    dns = Column(String(128), nullable=True)

    # Для клиентского режима (awg1)
    endpoint = Column(String(256), nullable=True)
    preshared_key = Column(String(64), nullable=True)
    allowed_ips = Column(String(256), nullable=True)
    persistent_keepalive = Column(Integer, nullable=True)

    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Параметры обфускации AmneziaWG ───────────────────────────────────
    # Junk packets — используются только клиентской стороной
    obf_jc = Column(Integer, nullable=True)    # кол-во junk пакетов перед handshake
    obf_jmin = Column(Integer, nullable=True)  # мин. размер junk пакета
    obf_jmax = Column(Integer, nullable=True)  # макс. размер junk пакета (< 1280)

    # Padding — симметричные, одинаковые на обоих концах туннеля
    obf_s1 = Column(Integer, nullable=True)
    obf_s2 = Column(Integer, nullable=True)
    obf_s3 = Column(Integer, nullable=True)
    obf_s4 = Column(Integer, nullable=True)

    # Headers — симметричные, уникальные uint32, не равные 1/2/3/4
    obf_h1 = Column(Integer, nullable=True)
    obf_h2 = Column(Integer, nullable=True)
    obf_h3 = Column(Integer, nullable=True)
    obf_h4 = Column(Integer, nullable=True)

    obf_generated_at = Column(DateTime, nullable=True)

    # ── Связи ────────────────────────────────────────────────────────────
    peers = relationship("Peer", back_populates="interface", cascade="all, delete-orphan")
