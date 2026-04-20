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
from app.services.external_ip import effective_fqdn_prefixes
from app.services.runtime_state import get_node_runtime_state, get_tunnel_runtime_state
from app.services.nftables_manager import TABLE_NAME as NFT_TABLE_NAME
from app.services.routing import build_routing_plan, firewall_backend, fqdn_ipset_name


BACKUP_SCHEMA_VERSION = "2"
LEGACY_BACKUP_SCHEMA_VERSION = "1"
BACKUP_KINDS_WITH_FILES = {"backup", "manual", "scheduled", "pre_reset"}


def build_manifest() -> dict:
    return {
        "app": "awg-gateway",
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def normalize_backup_schedule_time(raw_value: str) -> str:
    candidate = raw_value.strip()
    try:
        parsed = datetime.strptime(candidate, "%H:%M")
    except ValueError as exc:
        raise ValueError("backup_schedule_time must be in HH:MM format") from exc
    return parsed.strftime("%H:%M")


def backup_file_path(filename: str) -> Path:
    return Path(settings.backup_dir) / filename


def infer_backup_kind(filename: str) -> str:
    lowered = filename.lower()
    if "-pre-reset-" in lowered:
        return "pre_reset"
    if "-scheduled-" in lowered:
        return "scheduled"
    if "-manual-" in lowered:
        return "manual"
    if "-backup-" in lowered:
        return "backup"
    return "backup"


def _add_dir_to_zip(zf: zipfile.ZipFile, source_dir: str, prefix: str) -> None:
    path = Path(source_dir)
    if not path.exists():
        return
    for item in sorted(path.iterdir()):
        if item.is_file():
            zf.write(item, arcname=f"{prefix}/{item.name}")


def _add_sqlite_db_to_zip(zf: zipfile.ZipFile, source_path: str, archive_name: str) -> None:
    dst = sqlite3.connect(":memory:")
    try:
        if os.path.isfile(source_path):
            src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
            try:
                src.backup(dst)
            finally:
                src.close()
        zf.writestr(archive_name, bytes(dst.serialize()))
    finally:
        dst.close()


def build_backup_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(build_manifest(), indent=2))
        _add_sqlite_db_to_zip(zf, settings.db_path, "gateway.db")
        _add_sqlite_db_to_zip(zf, settings.metrics_db_path, "gateway-metrics.db")
        _add_dir_to_zip(zf, settings.geoip_cache_dir, "geoip_cache")
        _add_dir_to_zip(zf, settings.wg_config_dir, "wg_configs")
    return buf.getvalue()


def build_backup_filename(*, kind: str, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    suffix = current.strftime("%Y%m%d_%H%M%S")
    return f"awg-gateway-{kind}-{suffix}.zip"


def _read_backup_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        return json.loads(zf.read("manifest.json"))
    except KeyError as exc:
        raise ValueError("Backup archive must contain manifest.json") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid backup manifest: {exc}") from exc


def validate_backup_archive(data: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid ZIP archive: {exc}") from exc
    names = set(zf.namelist())
    if "gateway.db" not in names:
        zf.close()
        raise ValueError("Backup archive must contain gateway.db")
    manifest = _read_backup_manifest(zf)
    if manifest.get("app") != "awg-gateway":
        zf.close()
        raise ValueError("Backup archive is not for awg-gateway")
    schema_version = manifest.get("schema_version")
    if schema_version not in {LEGACY_BACKUP_SCHEMA_VERSION, BACKUP_SCHEMA_VERSION}:
        zf.close()
        raise ValueError(
            f"Incompatible backup schema version: {schema_version} not in "
            f"{{{LEGACY_BACKUP_SCHEMA_VERSION}, {BACKUP_SCHEMA_VERSION}}}"
        )
    if schema_version == BACKUP_SCHEMA_VERSION and "gateway-metrics.db" not in names:
        zf.close()
        raise ValueError("Backup archive schema v2 must contain gateway-metrics.db")
    return zf


def restore_backup_bytes(data: bytes) -> dict:
    zf = validate_backup_archive(data)
    tmp_db_path = f"{settings.db_path}.restore"
    tmp_metrics_db_path = f"{settings.metrics_db_path}.restore"
    with zf:
        manifest = _read_backup_manifest(zf)
        schema_version = manifest.get("schema_version")
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        with zf.open("gateway.db") as src, open(tmp_db_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(tmp_db_path, settings.db_path)
        if schema_version == BACKUP_SCHEMA_VERSION and "gateway-metrics.db" in zf.namelist():
            with zf.open("gateway-metrics.db") as src, open(tmp_metrics_db_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.replace(tmp_metrics_db_path, settings.metrics_db_path)
        elif os.path.exists(settings.metrics_db_path):
            os.unlink(settings.metrics_db_path)
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
    return {"status": "restored", "schema_version": schema_version}


async def record_backup(db: AsyncSession, filename: str, size_bytes: int, kind: str = "backup") -> None:
    db.add(BackupRecord(filename=filename, size_bytes=size_bytes, kind=kind))
    await db.flush()


async def create_backup_file(db: AsyncSession, *, kind: str = "manual", filename: str | None = None) -> dict:
    backup_bytes = build_backup_bytes()
    Path(settings.backup_dir).mkdir(parents=True, exist_ok=True)
    target_name = filename or build_backup_filename(kind=kind)
    target = backup_file_path(target_name)
    target.write_bytes(backup_bytes)
    await record_backup(db, filename=target_name, size_bytes=len(backup_bytes), kind=kind)
    return {
        "filename": target_name,
        "size_bytes": len(backup_bytes),
        "kind": kind,
        "path": str(target),
    }


async def prune_backup_files(db: AsyncSession, *, retention_count: int) -> None:
    if retention_count < 1:
        retention_count = 1
    rows = (
        await db.execute(
            select(BackupRecord)
            .where(BackupRecord.kind.in_(tuple(BACKUP_KINDS_WITH_FILES)))
            .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
        )
    ).scalars().all()
    for row in rows[retention_count:]:
        target = backup_file_path(row.filename)
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        await db.delete(row)


async def restore_backup_record(db: AsyncSession, backup_record: BackupRecord) -> dict:
    target = backup_file_path(backup_record.filename)
    if not target.is_file():
        raise ValueError(f"Backup file {backup_record.filename} is missing on the server")
    result = restore_backup_bytes(target.read_bytes())
    await record_backup(db, filename=backup_record.filename, size_bytes=backup_record.size_bytes, kind="restore")
    return result


async def delete_backup_record(db: AsyncSession, backup_record: BackupRecord) -> dict:
    target = backup_file_path(backup_record.filename)
    deleted_file = False
    if target.is_file():
        target.unlink()
        deleted_file = True
    await db.delete(backup_record)
    return {
        "status": "deleted",
        "filename": backup_record.filename,
        "deleted_file": deleted_file,
    }


async def delete_backup_by_filename(db: AsyncSession, filename: str) -> dict:
    target = backup_file_path(filename)
    deleted_file = False
    if target.is_file():
        target.unlink()
        deleted_file = True
    rows = (await db.execute(select(BackupRecord).where(BackupRecord.filename == filename))).scalars().all()
    for row in rows:
        await db.delete(row)
    return {
        "status": "deleted",
        "filename": filename,
        "deleted_file": deleted_file,
    }


async def sync_backup_records_from_files(db: AsyncSession) -> None:
    backup_dir = Path(settings.backup_dir)
    if not backup_dir.exists():
        return
    known_rows = (await db.execute(select(BackupRecord))).scalars().all()
    known_by_filename = {row.filename: row for row in known_rows}
    changed = False
    for item in sorted(backup_dir.glob("*.zip")):
        stat = item.stat()
        existing = known_by_filename.get(item.name)
        if existing is None:
            db.add(
                BackupRecord(
                    filename=item.name,
                    size_bytes=stat.st_size,
                    kind=infer_backup_kind(item.name),
                    created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                )
            )
            changed = True
            continue
        if existing.size_bytes != stat.st_size:
            existing.size_bytes = stat.st_size
            db.add(existing)
            changed = True
    if changed:
        await db.flush()


async def build_diagnostics_payload(db: AsyncSession) -> dict:
    gateway_settings = await db.get(GatewaySettings, 1)
    routing_policy = await db.get(RoutingPolicy, 1)
    entry_nodes = (await db.execute(select(EntryNode).order_by(EntryNode.position.asc(), EntryNode.id.asc()))).scalars().all()
    upstreams = (await db.execute(select(DnsUpstream).order_by(DnsUpstream.zone))).scalars().all()
    domain_rules = (await db.execute(select(DnsDomainRule).order_by(DnsDomainRule.domain))).scalars().all()
    active_node = gateway_settings.active_entry_node if gateway_settings else None
    tunnel_state = get_tunnel_runtime_state()
    return {
        "manifest": build_manifest(),
        "gateway_settings": {
            "ui_language": gateway_settings.ui_language if gateway_settings else "en",
            "allowed_client_cidrs": gateway_settings.allowed_client_cidrs if gateway_settings else [],
            "experimental_nftables": gateway_settings.experimental_nftables if gateway_settings else False,
            "failover_enabled": gateway_settings.failover_enabled if gateway_settings else False,
            "tunnel_status": tunnel_state.status,
            "tunnel_last_error": tunnel_state.last_error,
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
                "position": node.position,
                "is_active": node.is_active,
                "latest_latency_ms": get_node_runtime_state(node.id).latency_ms,
                "latest_latency_at": (
                    get_node_runtime_state(node.id).latency_at.isoformat()
                    if get_node_runtime_state(node.id).latency_at
                    else None
                ),
            }
            for node in entry_nodes
        ],
        "routing_plan": build_routing_plan(gateway_settings, routing_policy, active_node) if gateway_settings and routing_policy else {},
        "dns_preview": build_dnsmasq_preview(
            upstreams,
            domain_rules,
            fqdn_prefixes=effective_fqdn_prefixes(routing_policy, gateway_settings),
            ipset_name=fqdn_ipset_name(routing_policy) if routing_policy else "routing_prefixes_fqdn",
            use_nftset=firewall_backend(gateway_settings) == "nftables",
            nft_table_name=NFT_TABLE_NAME,
        ),
    }
