"""
Backup router — экспорт/импорт ZIP-архива с config.db и runtime-данными.

Содержимое архива:
  config.db              — база данных SQLite (все таблицы приложения)
  env_snapshot.json      — публичные параметры конфигурации (без паролей)
  wg_configs/            — сгенерированные WG-конфиги
  geoip_cache/           — локальный кэш GeoIP-префиксов
  certs/                 — TLS сертификаты FastAPI
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

_BACKUP_VERSION = "4"


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
            "classic_wg": _json_safe_setting("classic_wg", ""),
            "wg0_listen_port": _json_safe_setting("wg0_listen_port", None),
            "wg0_address": _json_safe_setting("wg0_address", "10.11.0.1/24"),
            "wg0_dns": _json_safe_setting("wg0_dns", "10.11.0.1"),
            "awg1_address": _json_safe_setting("awg1_address", "10.20.0.2/32"),
            "awg1_allowed_ips": _json_safe_setting("awg1_allowed_ips", "0.0.0.0/0"),
            "awg1_persistent_keepalive": _json_safe_setting("awg1_persistent_keepalive", 25),
            "physical_iface": _json_safe_setting("physical_iface", "eth0"),
            "routing_table_local": _json_safe_setting("routing_table_local", 100),
            "routing_table_vpn": _json_safe_setting("routing_table_vpn", 200),
            "fwmark_local": _json_safe_setting("fwmark_local", "0x1"),
            "fwmark_vpn": _json_safe_setting("fwmark_vpn", "0x2"),
            "geoip_source": _json_safe_setting(
                "geoip_source", "http://www.ipdeny.com/ipblocks/data/countries/"
            ),
            "geoip_update_cron": _json_safe_setting("geoip_update_cron", "0 4 * * *"),
            "node_awg_port": _json_safe_setting("node_awg_port", 51821),
            "node_vpn_subnet": _json_safe_setting("node_vpn_subnet", "10.20.0.0/24"),
            "web_mode": _json_safe_setting("web_mode", "https"),
            "web_port": _json_safe_setting("web_port", 8080),
            "tls_cert_path": _json_safe_setting("tls_cert_path", "/data/certs/server.crt"),
            "tls_key_path": _json_safe_setting("tls_key_path", "/data/certs/server.key"),
        },
        "note": (
            "dns_domains (split DNS rules) are stored in config.db and restored automatically. "
            "Private keys and passwords are NOT included in this snapshot."
        ),
    }


def _add_dir_to_zip(zf: zipfile.ZipFile, src_dir: str, arc_prefix: str) -> None:
    if not os.path.isdir(src_dir):
        return
    for fname in sorted(os.listdir(src_dir)):
        fpath = os.path.join(src_dir, fname)
        if os.path.isfile(fpath):
            zf.write(fpath, arcname=f"{arc_prefix}/{fname}")


def _validate_zip(data: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid ZIP file: {exc}") from exc

    names = set(zf.namelist())
    if "config.db" not in names:
        zf.close()
        raise ValueError("Archive must contain config.db")
    return zf


def _safe_archive_file_names(zf: zipfile.ZipFile, prefix: str) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for name in zf.namelist():
        if not name.startswith(f"{prefix}/") or name.endswith("/"):
            continue
        fname = os.path.basename(name)
        if not fname or fname.startswith(".") or "/" in fname or "\\" in fname:
            logger.warning("Skipping suspicious archive entry: %s", name)
            continue
        files.append((name, fname))
    return files


def _replace_directory_from_archive(
    zf: zipfile.ZipFile,
    archive_prefix: str,
    dest_dir: str,
) -> None:
    os.makedirs(dest_dir, exist_ok=True)

    for fname in os.listdir(dest_dir):
        fpath = os.path.join(dest_dir, fname)
        if os.path.isfile(fpath):
            os.unlink(fpath)

    for arcname, fname in _safe_archive_file_names(zf, archive_prefix):
        dest = os.path.join(dest_dir, fname)
        with zf.open(arcname) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)


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

        # runtime-файлы, которые нужны после восстановления без повторной генерации
        _add_dir_to_zip(zf, settings.wg_config_dir, "wg_configs")
        _add_dir_to_zip(zf, settings.geoip_cache_dir, "geoip_cache")
        _add_dir_to_zip(zf, settings.certs_dir, "certs")

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
        zf = _validate_zip(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    tmp_db_path = settings.db_path + ".import"
    try:
        with zf:
            # Бэкап текущей БД
            if os.path.exists(settings.db_path):
                bak_path = settings.db_path + ".bak"
                try:
                    shutil.copy2(settings.db_path, bak_path)
                except Exception as e:
                    logger.warning("Could not back up current db: %s", e)

            # Атомарно заменить config.db через временный файл
            db_dir = os.path.dirname(settings.db_path)
            os.makedirs(db_dir, exist_ok=True)
            with zf.open("config.db") as src, open(tmp_db_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.replace(tmp_db_path, settings.db_path)

            _replace_directory_from_archive(zf, "wg_configs", settings.wg_config_dir)
            _replace_directory_from_archive(zf, "geoip_cache", settings.geoip_cache_dir)
            _replace_directory_from_archive(zf, "certs", settings.certs_dir)
    except HTTPException:
        raise
    except Exception as e:
        # Откат если что-то пошло не так
        bak = settings.db_path + ".bak"
        if os.path.exists(tmp_db_path):
            os.unlink(tmp_db_path)
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
