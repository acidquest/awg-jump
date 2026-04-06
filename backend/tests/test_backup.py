import io
import json
import zipfile
from unittest.mock import patch

import pytest
from httpx import AsyncClient


def _make_zip(include_db: bool = True) -> bytes:
    """Создаёт тестовый ZIP-архив."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        if include_db:
            # Минимальный SQLite файл (magic bytes)
            sqlite_magic = b"SQLite format 3\x00" + b"\x00" * 84
            zf.writestr("config.db", sqlite_magic)
        zf.writestr(
            "env_snapshot.json",
            json.dumps({"version": "1", "config": {}}),
        )
        zf.writestr("wg_configs/awg0.conf", "[Interface]\nPrivateKey = test\n")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_export_backup(client: AsyncClient, auth_headers: dict) -> None:
    with (
        patch("backend.routers.backup.settings") as mock_settings,
        patch("os.makedirs"),
        patch("builtins.open", create=True) as mock_open,
    ):
        import tempfile, os
        mock_settings.db_path = "/dev/null"  # пустой файл
        mock_settings.backup_dir = tempfile.mkdtemp()
        mock_settings.wg_config_dir = tempfile.mkdtemp()

        resp = await client.get("/api/backup/export", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "attachment" in resp.headers["content-disposition"]

    # Проверить что это валидный ZIP
    zip_data = resp.content
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        names = zf.namelist()
    assert "env_snapshot.json" in names


@pytest.mark.asyncio
async def test_import_valid_backup(client: AsyncClient, auth_headers: dict) -> None:
    zip_bytes = _make_zip(include_db=True)

    with (
        patch("backend.routers.backup.settings") as mock_settings,
        patch("os.makedirs"),
        patch("shutil.copy2"),
        patch("shutil.move"),
        patch("zipfile.ZipFile") as mock_zf_cls,
    ):
        import tempfile
        mock_settings.db_path = "/tmp/test_config.db"
        mock_settings.wg_config_dir = tempfile.mkdtemp()

        # Сделать реальный ZipFile для валидации
        real_zip = zipfile.ZipFile(io.BytesIO(zip_bytes))
        mock_zf_cls.return_value.__enter__ = lambda s: real_zip
        mock_zf_cls.return_value.__exit__ = lambda s, *a: real_zip.close()

        resp = await client.post(
            "/api/backup/import",
            files={"file": ("backup.zip", zip_bytes, "application/zip")},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "imported"


@pytest.mark.asyncio
async def test_import_missing_db(client: AsyncClient, auth_headers: dict) -> None:
    zip_bytes = _make_zip(include_db=False)
    resp = await client.post(
        "/api/backup/import",
        files={"file": ("backup.zip", zip_bytes, "application/zip")},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "config.db" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_import_invalid_zip(client: AsyncClient, auth_headers: dict) -> None:
    resp = await client.post(
        "/api/backup/import",
        files={"file": ("backup.zip", b"not a zip file", "application/zip")},
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_backups(client: AsyncClient, auth_headers: dict) -> None:
    with patch("backend.routers.backup._list_backups", return_value=[
        {"filename": "backup_20260101_120000.zip", "size_bytes": 1024,
         "created_at": "2026-01-01T12:00:00+00:00"},
    ]):
        resp = await client.get("/api/backup/list", headers=auth_headers)

    assert resp.status_code == 200
    backups = resp.json()
    assert isinstance(backups, list)
    assert len(backups) == 1
    assert backups[0]["filename"] == "backup_20260101_120000.zip"


@pytest.mark.asyncio
async def test_backup_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/backup/export")
    assert resp.status_code == 401


# ── Unit tests: backup helpers ────────────────────────────────────────────

def test_env_snapshot_excludes_secrets() -> None:
    from backend.routers.backup import _env_snapshot
    snapshot = _env_snapshot()
    snapshot_str = json.dumps(snapshot)
    # Пароли не должны попасть в снапшот
    assert "admin_password" not in snapshot_str
    assert "secret_key" not in snapshot_str
    assert "private_key" not in snapshot_str
    # Публичные параметры должны быть
    assert "awg0_listen_port" in snapshot_str
    assert "version" in snapshot


def test_validate_zip_good() -> None:
    from backend.routers.backup import _validate_zip
    zip_bytes = _make_zip(include_db=True)
    # Должно пройти без исключения
    _validate_zip(zip_bytes)


def test_validate_zip_no_db() -> None:
    from backend.routers.backup import _validate_zip
    zip_bytes = _make_zip(include_db=False)
    with pytest.raises(ValueError, match="config.db"):
        _validate_zip(zip_bytes)


def test_validate_zip_corrupt() -> None:
    from backend.routers.backup import _validate_zip
    with pytest.raises(ValueError, match="Invalid ZIP"):
        _validate_zip(b"garbage data here")
