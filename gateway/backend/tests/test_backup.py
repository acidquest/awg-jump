import io
import json
import zipfile

import pytest

from app.services.backup import BACKUP_SCHEMA_VERSION, validate_backup_archive


def make_backup_bytes(schema_version: str = BACKUP_SCHEMA_VERSION) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"app": "awg-gateway", "schema_version": schema_version}),
        )
        zf.writestr("gateway.db", b"SQLite format 3\x00" + b"\x00" * 84)
    return buf.getvalue()


def test_validate_backup_schema_version() -> None:
    validate_backup_archive(make_backup_bytes())


def test_validate_backup_rejects_incompatible_schema() -> None:
    with pytest.raises(ValueError, match="Incompatible backup schema version"):
        validate_backup_archive(make_backup_bytes(schema_version="999"))
