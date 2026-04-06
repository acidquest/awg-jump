"""
Backup router — экспорт/импорт ZIP-архива с config.db и метаданными.

Содержимое архива:
  config.db              — база данных SQLite
  env_snapshot.json      — публичные параметры конфигурации (без паролей)
  wg_configs/            — резервные копии конфигов (если есть)
"""
import io
import json
import logging
import os
import shutil
import zipfile
from datetime import datetime, timezone
from typing import BinaryIO

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.config import settings
from backend.routers.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backup", tags=["backup"])

_BACKUP_VERSION = "1"


def _env_snapshot() -> dict:
    """Публичные параметры — без паролей и ключей."""
    return {
        "version": _BACKUP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "awg0_listen_port": settings.awg0_listen_port,
            "awg0_address": settings.awg0_address,
            "awg0_dns": settings.awg0_dns,
            "awg1_address": settings.awg1_address,
            "awg1_allowed_ips": settings.awg1_allowed_ips,
            "awg1_persistent_keepalive": settings.awg1_persistent_keepalive,
            "physical_iface": settings.physical_iface,
            "routing_table_ru": settings.routing_table_ru,
            "routing_table_vpn": settings.routing_table_vpn,
            "fwmark_ru": settings.fwmark_ru,
            "fwmark_vpn": settings.fwmark_vpn,
            "geoip_source_ru": settings.geoip_source_ru,
            "geoip_update_cron": settings.geoip_update_cron,
            "node_awg_port": settings.node_awg_port,
            "node_vpn_subnet": settings.node_vpn_subnet,
        },
    }


def _build_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # config.db
        if os.path.exists(settings.db_path):
            zf.write(settings.db_path, arcname="config.db")

        # env_snapshot.json
        zf.writestr("env_snapshot.json", json.dumps(_env_snapshot(), indent=2))

        # wg_configs/ (если есть)
        if os.path.isdir(settings.wg_config_dir):
            for fname in os.listdir(settings.wg_config_dir):
                fpath = os.path.join(settings.wg_config_dir, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, arcname=f"wg_configs/{fname}")

    return buf.getvalue()


def _validate_zip(data: bytes) -> None:
    """Проверяет что архив корректен и содержит config.db."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile as e:
        raise ValueError(f"Invalid ZIP file: {e}")

    if "config.db" not in names:
        raise ValueError("Archive must contain config.db")


def _list_backups() -> list[dict]:
    if not os.path.isdir(settings.backup_dir):
        return []
    backups = []
    for fname in sorted(os.listdir(settings.backup_dir), reverse=True):
        if fname.endswith(".zip"):
            fpath = os.path.join(settings.backup_dir, fname)
            stat = os.stat(fpath)
            backups.append({
                "filename": fname,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
    return backups


@router.get("/export")
async def export_backup(_user: str = Depends(get_current_user)) -> StreamingResponse:
    """Скачать ZIP-архив с config.db + env_snapshot.json + wg_configs/."""
    try:
        zip_bytes = _build_zip_bytes()
    except Exception as e:
        logger.error("Backup export failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    # Сохранить копию в backup_dir
    try:
        os.makedirs(settings.backup_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(settings.backup_dir, f"backup_{ts}.zip")
        with open(backup_path, "wb") as f:
            f.write(zip_bytes)
    except Exception as e:
        logger.warning("Could not save backup to disk: %s", e)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"awg-jump-backup-{ts}.zip"
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
async def import_backup(
    file: UploadFile = File(...),
    _user: str = Depends(get_current_user),
) -> dict:
    """
    Импорт резервной копии.
    1. Валидация ZIP
    2. Сохранить текущую БД как .bak
    3. Заменить config.db из архива
    4. Скопировать wg_configs/ из архива
    5. Вернуть инструкцию перезапустить контейнер (alembic upgrade запустится при старте)
    """
    data = await file.read()

    try:
        _validate_zip(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Бэкап текущей БД
    if os.path.exists(settings.db_path):
        bak_path = settings.db_path + ".bak"
        try:
            shutil.copy2(settings.db_path, bak_path)
        except Exception as e:
            logger.warning("Could not back up current db: %s", e)

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            # Заменить config.db
            db_dir = os.path.dirname(settings.db_path)
            os.makedirs(db_dir, exist_ok=True)
            zf.extract("config.db", path=db_dir)
            # Переместить в правильное место если нужно
            extracted = os.path.join(db_dir, "config.db")
            if extracted != settings.db_path:
                shutil.move(extracted, settings.db_path)

            # Восстановить wg_configs/
            os.makedirs(settings.wg_config_dir, exist_ok=True)
            for name in zf.namelist():
                if name.startswith("wg_configs/") and not name.endswith("/"):
                    fname = os.path.basename(name)
                    dest = os.path.join(settings.wg_config_dir, fname)
                    with zf.open(name) as src, open(dest, "wb") as dst:
                        dst.write(src.read())

    except Exception as e:
        # Откат если что-то пошло не так
        bak = settings.db_path + ".bak"
        if os.path.exists(bak):
            shutil.copy2(bak, settings.db_path)
        raise HTTPException(status_code=500, detail=f"Import failed: {e}")

    return {
        "status": "imported",
        "message": "Restart the container to apply: docker-compose restart awg-jump",
    }


@router.get("/list")
async def list_backups(_user: str = Depends(get_current_user)) -> list[dict]:
    """Список сохранённых резервных копий в /data/backups/."""
    return _list_backups()
