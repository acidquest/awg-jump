import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

logger = logging.getLogger(__name__)

_INSECURE_DEFAULTS = {
    "changeme",
    "insecure-default-key-change-me",
    "admin",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Администратор ────────────────────────────────────────────────────
    admin_username: str = "admin"
    admin_password: str = "changeme"

    # ── Веб-интерфейс ────────────────────────────────────────────────────
    web_port: int = 8080
    secret_key: str = "insecure-default-key-change-me"
    session_ttl_hours: int = 8

    # ── Публичный адрес сервера (для Endpoint в клиентских конфигах) ─────
    server_host: str = ""

    # ── AWG0 (сервер, принимает клиентов) ────────────────────────────────
    awg0_listen_port: int = 51820
    awg0_private_key: str = ""
    awg0_address: str = "10.10.0.1/24"
    awg0_dns: str = "1.1.1.1"

    # ── AWG1 (клиент, upstream VPN) ──────────────────────────────────────
    awg1_endpoint: str = ""
    awg1_private_key: str = ""
    awg1_public_key: str = ""
    awg1_preshared_key: str = ""
    awg1_address: str = "10.20.0.2/32"
    awg1_allowed_ips: str = "0.0.0.0/0"
    awg1_persistent_keepalive: int = 25

    # ── Маршрутизация ────────────────────────────────────────────────────
    physical_iface: str = "eth0"
    routing_table_ru: int = 100
    routing_table_vpn: int = 200
    fwmark_ru: str = "0x1"
    fwmark_vpn: str = "0x2"

    # ── GeoIP ────────────────────────────────────────────────────────────
    geoip_source_ru: str = "http://www.ipdeny.com/ipblocks/data/countries/ru.zone"
    geoip_update_cron: str = "0 4 * * *"
    geoip_fetch_timeout: int = 30

    # ── Upstream ноды ────────────────────────────────────────────────────
    node_health_check_interval: int = 30
    node_health_check_timeout: int = 5
    node_failover_threshold: int = 3
    node_awg_port: int = 51821
    node_vpn_subnet: str = "10.20.0.0/24"

    # ── Разработка ────────────────────────────────────────────────────────
    # Включить OpenAPI docs (/api/docs, /api/redoc) — только для разработки
    enable_api_docs: bool = False

    # ── Пути ─────────────────────────────────────────────────────────────
    data_dir: str = "/data"
    db_path: str = "/data/config.db"
    geoip_cache_dir: str = "/data/geoip"
    backup_dir: str = "/data/backups"
    wg_config_dir: str = "/data/wg_configs"


settings = Settings()


def validate_security_settings() -> None:
    """Предупреждает о небезопасных дефолтных значениях."""
    if settings.admin_password in _INSECURE_DEFAULTS:
        logger.warning(
            "SECURITY WARNING: ADMIN_PASSWORD is set to an insecure default value. "
            "Set a strong password via the ADMIN_PASSWORD environment variable."
        )
    if settings.secret_key in _INSECURE_DEFAULTS:
        logger.warning(
            "SECURITY WARNING: SECRET_KEY is set to an insecure default value. "
            "Set a random secret via the SECRET_KEY environment variable."
        )
    if settings.admin_username == "admin":
        logger.warning(
            "SECURITY WARNING: ADMIN_USERNAME is set to the default 'admin'. "
            "Consider changing it via the ADMIN_USERNAME environment variable."
        )
