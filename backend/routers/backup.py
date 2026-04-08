"""
Backup router — экспорт/импорт ZIP-архива с config.db и метаданными.

Содержимое архива:
  config.db              — база данных SQLite (все таблицы: interfaces, peers,
                           upstream_nodes, geoip_sources, routing_rules, dns_domains)
  env_snapshot.json      — публичные параметры конфигурации (без паролей)
  wg_configs/            — резервные копии конфигов (если есть)
"""
import io
import json
import logging
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse

from backend.config import settings
from backend.routers.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backup", tags=["backup"])

_BACKUP_VERSION = "2"


def _json_safe_setting(name: str, default):
    value = getattr(settings, name, default)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return default


def _env_snapshot() -> dict:
    """Публичные параметры — без паролей и ключей."""
    return {
        "version": _BACKUP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "awg0_listen_port": _json_safe_setting("awg0_listen_port", 51820),
            "awg0_address": _json_safe_setting("awg0_address", "10.10.0.1/24"),
            "awg0_dns": _json_safe_setting("awg0_dns", "1.1.1.1"),
            "awg1_address": _json_safe_setting("awg1_address", "10.20.0.2/32"),
            "awg1_allowed_ips": _json_safe_setting("awg1_allowed_ips", "0.0.0.0/0"),
            "awg1_persistent_keepalive": _json_safe_setting("awg1_persistent_keepalive", 25),
            "physical_iface": _json_safe_setting("physical_iface", "eth0"),
            "routing_table_ru": _json_safe_setting("routing_table_ru", 100),
            "routing_table_vpn": _json_safe_setting("routing_table_vpn", 200),
            "fwmark_ru": _json_safe_setting("fwmark_ru", "0x1"),
            "fwmark_vpn": _json_safe_setting("fwmark_vpn", "0x2"),
            "geoip_source": _json_safe_setting(
                "geoip_source", "http://www.ipdeny.com/ipblocks/data/countries/"
            ),
            "geoip_update_cron": _json_safe_setting("geoip_update_cron", "0 4 * * *"),
            "node_awg_port": _json_safe_setting("node_awg_port", 51821),
            "node_vpn_subnet": _json_safe_setting("node_vpn_subnet", "10.20.0.0/24"),
        },
        "note": (
            "dns_domains (split DNS rules) are stored in config.db and restored automatically. "
            "Private keys and passwords are NOT included in this snapshot."
        ),
    }


def _build_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # config.db — атомарный WAL-safe снимок через sqlite3.backup() + serialize()
        # Использование sqlite3.backup() гарантирует консистентность при активной записи.
        if os.path.isfile(settings.db_path):
            src = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
            dst = sqlite3.connect(":memory:")
            try:
                src.backup(dst)
                zf.writestr("config.db", bytes(dst.serialize()))
            finally:
                src.close()
                dst.close()

        # env_snapshot.json
        zf.writestr("env_snapshot.json", json.dumps(_env_snapshot(), indent=2, default=str))

        # wg_configs/ (если есть)
        if os.path.isdir(settings.wg_config_dir):
            for fname in os.listdir(settings.wg_config_dir):
                fpath = os.path.join(settings.wg_config_dir, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, arcname=f"wg_configs/{fname}")

    return buf.getvalue()


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
    3. Заменить config.db из архива (включает dns_domains, peers, interfaces, nodes)
    4. Скопировать wg_configs/ из архива
    5. Перезагрузить split DNS из новой БД
    6. Вернуть инструкцию перезапустить контейнер (alembic upgrade запустится при старте)
    """
    data = await file.read()

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if "config.db" not in zf.namelist():
                raise HTTPException(status_code=400, detail="Archive must contain config.db")

            # Бэкап текущей БД
            if os.path.exists(settings.db_path):
                bak_path = settings.db_path + ".bak"
                try:
                    shutil.copy2(settings.db_path, bak_path)
                except Exception as e:
                    logger.warning("Could not back up current db: %s", e)

            # Заменить config.db
            db_dir = os.path.dirname(settings.db_path)
            os.makedirs(db_dir, exist_ok=True)
            with zf.open("config.db") as src, open(settings.db_path, "wb") as dst:
                dst.write(src.read())

            # Восстановить wg_configs/ — только безопасные имена файлов
            os.makedirs(settings.wg_config_dir, exist_ok=True)
            for name in zf.namelist():
                if name.startswith("wg_configs/") and not name.endswith("/"):
                    fname = os.path.basename(name)
                    if not fname or fname.startswith(".") or "/" in fname or "\\" in fname:
                        logger.warning("Skipping suspicious archive entry: %s", name)
                        continue
                    dest = os.path.join(settings.wg_config_dir, fname)
                    with zf.open(name) as src, open(dest, "wb") as dst:
                        dst.write(src.read())

    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail=f"Invalid ZIP file: {e}")
    except HTTPException:
        raise
    except Exception as e:
        # Откат если что-то пошло не так
        bak = settings.db_path + ".bak"
        if os.path.exists(bak):
            shutil.copy2(bak, settings.db_path)
        raise HTTPException(status_code=500, detail=f"Import failed: {e}")

    # Перезагрузить split DNS из восстановленной БД
    try:
        import backend.services.dns_manager as dns_mgr
        await dns_mgr.apply_from_db()
        logger.info("Split DNS reloaded after backup import")
    except Exception as e:
        logger.warning("DNS reload after import failed: %s", e)

    return {
        "status": "imported",
        "message": "Restart the container to apply: docker-compose restart awg-jump",
    }


@router.get("/list")
async def list_backups(_user: str = Depends(get_current_user)) -> list[dict]:
    """Список сохранённых резервных копий в /data/backups/."""
    return _list_backups()
