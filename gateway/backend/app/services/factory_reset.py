from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.bootstrap import ensure_bootstrap_state
from app.config import ensure_directories, settings
from app.database import AsyncSessionLocal, commit_with_lock, engine, metrics_engine, prepare_session
from app.models import AuditEvent, GatewaySettings, MAIN_DB_TABLES, METRICS_DB_TABLES
from app.security import clear_sessions
from app.services.backup import build_backup_bytes
from app.services.dns_runtime import restart_dnsmasq, stop_dnsmasq
from app.services.maintenance import acquire_reset_lock, release_reset_lock
from app.services.routing import apply_local_passthrough
from app.services.runtime import stop_tunnel
from app.services.runtime_state import reset_gateway_runtime_state


logger = logging.getLogger(__name__)
RESET_CONFIRMATION_TEXT = "RESET"


def validate_reset_confirmation(confirm_text: str) -> None:
    if confirm_text.strip().upper() != RESET_CONFIRMATION_TEXT:
        raise ValueError(f"Confirmation text must be exactly {RESET_CONFIRMATION_TEXT}")


def _delete_sqlite_family(path: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(f"{path}{suffix}")
        except FileNotFoundError:
            continue


def _clear_directory(path: str) -> None:
    directory = Path(path)
    if not directory.exists():
        return
    for item in directory.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


async def _capture_pre_reset_backup() -> tuple[str, int]:
    backup_bytes = build_backup_bytes()
    filename = f"awg-gateway-pre-reset-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.zip"
    Path(settings.backup_dir).mkdir(parents=True, exist_ok=True)
    (Path(settings.backup_dir) / filename).write_bytes(backup_bytes)
    return filename, len(backup_bytes)


async def _stop_runtime_before_reset() -> None:
    try:
        async with AsyncSessionLocal() as session:
            prepare_session(session)
            settings_row = await session.get(GatewaySettings, 1)
            if settings_row is not None:
                await stop_tunnel(settings_row)
                apply_local_passthrough(settings_row)
    except Exception as exc:
        logger.warning("[factory-reset] failed to stop tunnel cleanly: %s", exc)
    try:
        stop_dnsmasq()
    except Exception as exc:
        logger.warning("[factory-reset] failed to stop dnsmasq cleanly: %s", exc)


async def _recreate_datastores() -> None:
    await engine.dispose()
    await metrics_engine.dispose()
    _delete_sqlite_family(settings.db_path)
    _delete_sqlite_family(settings.metrics_db_path)
    _clear_directory(settings.wg_config_dir)
    _clear_directory(settings.dns_runtime_dir)
    ensure_directories()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: [table.create(sync_conn, checkfirst=True) for table in MAIN_DB_TABLES])
    async with metrics_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: [table.create(sync_conn, checkfirst=True) for table in METRICS_DB_TABLES])


async def factory_reset(confirm_text: str) -> dict:
    validate_reset_confirmation(confirm_text)
    await acquire_reset_lock()
    try:
        backup_filename, backup_size_bytes = await _capture_pre_reset_backup()
        await _stop_runtime_before_reset()
        reset_gateway_runtime_state()
        await _recreate_datastores()
        clear_sessions()

        async with AsyncSessionLocal() as session:
            prepare_session(session)
            await ensure_bootstrap_state(session)
            session.add(
                AuditEvent(
                    event_type="settings.factory_reset",
                    payload={
                        "backup_filename": backup_filename,
                        "backup_size_bytes": backup_size_bytes,
                    },
                )
            )
            await commit_with_lock(session)

        try:
            async with AsyncSessionLocal() as session:
                prepare_session(session)
                await restart_dnsmasq(session)
        except Exception as exc:
            logger.warning("[factory-reset] dnsmasq restart after reset failed: %s", exc)

        return {
            "status": "reset",
            "backup_filename": backup_filename,
            "backup_size_bytes": backup_size_bytes,
            "confirmation_text": RESET_CONFIRMATION_TEXT,
            "requires_relogin": True,
        }
    finally:
        release_reset_lock()
