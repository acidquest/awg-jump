from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AWG Gateway"
    app_version: str = "0.1.0"
    web_port: int = 8081
    allow_api_docs: bool = False

    admin_username: str = "admin"
    admin_password: str = "changeme"
    session_ttl_hours: int = 8

    data_dir: str = "/data"
    db_path: str = "/data/gateway.db"
    backup_dir: str = "/data/backups"
    geoip_cache_dir: str = "/data/geoip"
    wg_config_dir: str = "/data/wg"
    diagnostics_dir: str = "/data/diagnostics"
    runtime_dir: str = "/var/run/awg-gateway"
    dns_runtime_dir: str = "/data/dns"

    tunnel_interface: str = "awg-gw0"
    amneziawg_go_binary: str = "amneziawg-go"
    awg_binary: str = "awg"
    default_tunnel_address: str = "10.44.0.2/32"
    default_dns_servers: str = "1.1.1.1,8.8.8.8"
    geoip_source: str = "https://www.ipdeny.com/ipblocks/data/countries"
    geoip_fetch_timeout: int = 30
    latency_ping_count: int = 1
    latency_ping_timeout_sec: int = 2
    routing_table_local: int = 200
    routing_table_vpn: int = 201
    fwmark_local: str = "0x1"
    fwmark_vpn: str = "0x2"

    ui_default_language: str = "en"


settings = Settings()


def ensure_directories() -> None:
    for path in [
        settings.data_dir,
        settings.backup_dir,
        settings.geoip_cache_dir,
        settings.wg_config_dir,
        settings.diagnostics_dir,
        settings.runtime_dir,
        settings.dns_runtime_dir,
    ]:
        Path(path).mkdir(parents=True, exist_ok=True)
