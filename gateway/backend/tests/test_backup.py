import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from app.services.backup import (
    BACKUP_SCHEMA_VERSION,
    LEGACY_BACKUP_SCHEMA_VERSION,
    restore_backup_bytes,
    validate_backup_archive,
)


def make_backup_bytes(schema_version: str = BACKUP_SCHEMA_VERSION, include_metrics: bool | None = None) -> bytes:
    if include_metrics is None:
        include_metrics = schema_version == BACKUP_SCHEMA_VERSION
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"app": "awg-gateway", "schema_version": schema_version}),
        )
        zf.writestr("gateway.db", b"SQLite format 3\x00" + b"\x00" * 84)
        if include_metrics:
            zf.writestr("gateway-metrics.db", b"SQLite format 3\x00" + b"\x00" * 84)
    return buf.getvalue()


def test_validate_backup_schema_version() -> None:
    validate_backup_archive(make_backup_bytes())


def test_validate_legacy_backup_schema_version() -> None:
    validate_backup_archive(make_backup_bytes(schema_version=LEGACY_BACKUP_SCHEMA_VERSION))


def test_validate_backup_rejects_incompatible_schema() -> None:
    with pytest.raises(ValueError, match="Incompatible backup schema version"):
        validate_backup_archive(make_backup_bytes(schema_version="999"))


def test_validate_backup_rejects_missing_metrics_db_for_v2() -> None:
    with pytest.raises(ValueError, match="gateway-metrics.db"):
        validate_backup_archive(make_backup_bytes(include_metrics=False))


def test_restore_backup_replaces_both_databases_for_v2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "gateway.db"
    metrics_db_path = data_dir / "gateway-metrics.db"

    def init_db(path: Path, table_name: str, value: str) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute(f"CREATE TABLE {table_name} (value TEXT)")
            conn.execute(f"INSERT INTO {table_name}(value) VALUES (?)", (value,))
            conn.commit()
        finally:
            conn.close()

    init_db(db_path, "main_state", "old-main")
    init_db(metrics_db_path, "metrics_state", "old-metrics")

    backup_bytes = io.BytesIO()
    with zipfile.ZipFile(backup_bytes, mode="w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"app": "awg-gateway", "schema_version": BACKUP_SCHEMA_VERSION}),
        )
        restored_main = sqlite3.connect(":memory:")
        restored_metrics = sqlite3.connect(":memory:")
        try:
            restored_main.execute("CREATE TABLE main_state (value TEXT)")
            restored_main.execute("INSERT INTO main_state(value) VALUES ('new-main')")
            restored_main.commit()
            restored_metrics.execute("CREATE TABLE metrics_state (value TEXT)")
            restored_metrics.execute("INSERT INTO metrics_state(value) VALUES ('new-metrics')")
            restored_metrics.commit()
            zf.writestr("gateway.db", bytes(restored_main.serialize()))
            zf.writestr("gateway-metrics.db", bytes(restored_metrics.serialize()))
        finally:
            restored_main.close()
            restored_metrics.close()

    monkeypatch.setattr("app.services.backup.settings.data_dir", str(data_dir))
    monkeypatch.setattr("app.services.backup.settings.db_path", str(db_path))
    monkeypatch.setattr("app.services.backup.settings.metrics_db_path", str(metrics_db_path))
    monkeypatch.setattr("app.services.backup.settings.geoip_cache_dir", str(data_dir / "geoip"))
    monkeypatch.setattr("app.services.backup.settings.wg_config_dir", str(data_dir / "wg"))

    result = restore_backup_bytes(backup_bytes.getvalue())

    assert result["schema_version"] == BACKUP_SCHEMA_VERSION
    main_conn = sqlite3.connect(db_path)
    metrics_conn = sqlite3.connect(metrics_db_path)
    try:
        assert main_conn.execute("SELECT value FROM main_state").fetchone()[0] == "new-main"
        assert metrics_conn.execute("SELECT value FROM metrics_state").fetchone()[0] == "new-metrics"
    finally:
        main_conn.close()
        metrics_conn.close()


def test_restore_legacy_backup_drops_stale_metrics_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "gateway.db"
    metrics_db_path = data_dir / "gateway-metrics.db"

    stale_metrics = sqlite3.connect(metrics_db_path)
    try:
        stale_metrics.execute("CREATE TABLE metrics_state (value TEXT)")
        stale_metrics.execute("INSERT INTO metrics_state(value) VALUES ('stale')")
        stale_metrics.commit()
    finally:
        stale_metrics.close()

    backup_bytes = io.BytesIO()
    with zipfile.ZipFile(backup_bytes, mode="w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"app": "awg-gateway", "schema_version": LEGACY_BACKUP_SCHEMA_VERSION}),
        )
        restored_main = sqlite3.connect(":memory:")
        try:
            restored_main.execute("CREATE TABLE main_state (value TEXT)")
            restored_main.execute("INSERT INTO main_state(value) VALUES ('legacy-main')")
            restored_main.commit()
            zf.writestr("gateway.db", bytes(restored_main.serialize()))
        finally:
            restored_main.close()

    monkeypatch.setattr("app.services.backup.settings.data_dir", str(data_dir))
    monkeypatch.setattr("app.services.backup.settings.db_path", str(db_path))
    monkeypatch.setattr("app.services.backup.settings.metrics_db_path", str(metrics_db_path))
    monkeypatch.setattr("app.services.backup.settings.geoip_cache_dir", str(data_dir / "geoip"))
    monkeypatch.setattr("app.services.backup.settings.wg_config_dir", str(data_dir / "wg"))

    result = restore_backup_bytes(backup_bytes.getvalue())

    assert result["schema_version"] == LEGACY_BACKUP_SCHEMA_VERSION
    assert not metrics_db_path.exists()
