from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import BackupRecord, DnsDomainRule, DnsUpstream, EntryNode, GatewaySettings, RoutingPolicy
from app.services.dns import build_dnsmasq_preview
from app.services.routing import build_routing_plan, fqdn_ipset_name


BACKUP_SCHEMA_VERSION = "1"


def build_manifest() -> dict:
    return {
        "app": "awg-gateway",
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _add_dir_to_zip(zf: zipfile.ZipFile, source_dir: str, prefix: str) -> None:
    path = Path(source_dir)
    if not path.exists():
        return
    for item in sorted(path.iterdir()):
        if item.is_file():
            zf.write(item, arcname=f"{prefix}/{item.name}")


def build_backup_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(build_manifest(), indent=2))
        if os.path.isfile(settings.db_path):
            src = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
            dst = sqlite3.connect(":memory:")
            try:
                src.backup(dst)
                zf.writestr("gateway.db", bytes(dst.serialize()))
            finally:
                src.close()
                dst.close()
        _add_dir_to_zip(zf, settings.geoip_cache_dir, "geoip_cache")
        _add_dir_to_zip(zf, settings.wg_config_dir, "wg_configs")
    return buf.getvalue()


def validate_backup_archive(data: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid ZIP archive: {exc}") from exc
    names = set(zf.namelist())
    if "manifest.json" not in names or "gateway.db" not in names:
        zf.close()
        raise ValueError("Backup archive must contain manifest.json and gateway.db")
    manifest = json.loads(zf.read("manifest.json"))
    if manifest.get("app") != "awg-gateway":
        zf.close()
        raise ValueError("Backup archive is not for awg-gateway")
    if manifest.get("schema_version") != BACKUP_SCHEMA_VERSION:
        zf.close()
        raise ValueError(
            f"Incompatible backup schema version: {manifest.get('schema_version')} != {BACKUP_SCHEMA_VERSION}"
        )
    return zf


def restore_backup_bytes(data: bytes) -> dict:
    zf = validate_backup_archive(data)
    tmp_db_path = f"{settings.db_path}.restore"
    with zf:
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        with zf.open("gateway.db") as src, open(tmp_db_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(tmp_db_path, settings.db_path)
        for name, target_dir in [("geoip_cache", settings.geoip_cache_dir), ("wg_configs", settings.wg_config_dir)]:
            Path(target_dir).mkdir(parents=True, exist_ok=True)
            for item in Path(target_dir).glob("*"):
                if item.is_file():
                    item.unlink()
            for archived in zf.namelist():
                if not archived.startswith(f"{name}/") or archived.endswith("/"):
                    continue
                target = Path(target_dir) / Path(archived).name
                with zf.open(archived) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    return {"status": "restored", "schema_version": BACKUP_SCHEMA_VERSION}


async def record_backup(db: AsyncSession, filename: str, size_bytes: int, kind: str = "backup") -> None:
    db.add(BackupRecord(filename=filename, size_bytes=size_bytes, kind=kind))
    await db.flush()


async def build_diagnostics_payload(db: AsyncSession) -> dict:
    gateway_settings = await db.get(GatewaySettings, 1)
    routing_policy = await db.get(RoutingPolicy, 1)
    entry_nodes = (await db.execute(select(EntryNode).order_by(EntryNode.id))).scalars().all()
    upstreams = (await db.execute(select(DnsUpstream).order_by(DnsUpstream.zone))).scalars().all()
    domain_rules = (await db.execute(select(DnsDomainRule).order_by(DnsDomainRule.domain))).scalars().all()
    active_node = gateway_settings.active_entry_node if gateway_settings else None
    return {
        "manifest": build_manifest(),
        "gateway_settings": {
            "ui_language": gateway_settings.ui_language if gateway_settings else "en",
            "traffic_source_mode": gateway_settings.traffic_source_mode if gateway_settings else "localhost",
            "allowed_client_cidrs": gateway_settings.allowed_client_cidrs if gateway_settings else [],
            "allowed_client_hosts": gateway_settings.allowed_client_hosts if gateway_settings else [],
            "tunnel_status": gateway_settings.tunnel_status if gateway_settings else "stopped",
            "tunnel_last_error": gateway_settings.tunnel_last_error if gateway_settings else None,
        },
        "routing_policy": {
            "countries_enabled": routing_policy.countries_enabled if routing_policy else False,
            "geoip_countries": routing_policy.geoip_countries if routing_policy else [],
            "manual_prefixes_enabled": routing_policy.manual_prefixes_enabled if routing_policy else False,
            "manual_prefixes": routing_policy.manual_prefixes if routing_policy else [],
            "fqdn_prefixes_enabled": routing_policy.fqdn_prefixes_enabled if routing_policy else False,
            "fqdn_prefixes": routing_policy.fqdn_prefixes if routing_policy else [],
            "ipset_name": routing_policy.geoip_ipset_name if routing_policy else "routing_prefixes",
            "prefixes_route_local": routing_policy.prefixes_route_local if routing_policy else True,
        },
        "entry_nodes": [
            {
                "id": node.id,
                "name": node.name,
                "endpoint": node.endpoint,
                "is_active": node.is_active,
                "latest_latency_ms": node.latest_latency_ms,
                "latest_latency_at": node.latest_latency_at.isoformat() if node.latest_latency_at else None,
            }
            for node in entry_nodes
        ],
        "routing_plan": build_routing_plan(gateway_settings, routing_policy, active_node) if gateway_settings and routing_policy else {},
        "dns_preview": build_dnsmasq_preview(
            upstreams,
            domain_rules,
            fqdn_prefixes=routing_policy.fqdn_prefixes if routing_policy and routing_policy.fqdn_prefixes_enabled else [],
            ipset_name=fqdn_ipset_name(routing_policy) if routing_policy else "routing_prefixes_fqdn",
        ),
    }
