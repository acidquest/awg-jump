from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import AdminUser, BackupRecord
from app.security import get_current_user
from app.services.backup import (
    build_backup_bytes,
    build_diagnostics_payload,
    record_backup,
    restore_backup_bytes,
)


router = APIRouter(prefix="/api/backup", tags=["backup"])


@router.get("/export")
async def export_backup(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> StreamingResponse:
    backup_bytes = build_backup_bytes()
    filename = f"awg-gateway-backup-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.zip"
    Path(settings.backup_dir).mkdir(parents=True, exist_ok=True)
    target = Path(settings.backup_dir) / filename
    target.write_bytes(backup_bytes)
    await record_backup(db, filename=filename, size_bytes=len(backup_bytes))
    return StreamingResponse(
        io.BytesIO(backup_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/restore")
async def restore_backup(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> JSONResponse:
    data = await file.read()
    try:
        result = restore_backup_bytes(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await record_backup(db, filename=file.filename or "uploaded.zip", size_bytes=len(data), kind="restore")
    return JSONResponse(result)


@router.get("/list")
async def list_backups(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> list[dict]:
    rows = (await db.execute(select(BackupRecord).order_by(BackupRecord.created_at.desc()))).scalars().all()
    return [
        {
            "id": row.id,
            "filename": row.filename,
            "size_bytes": row.size_bytes,
            "kind": row.kind,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/diagnostics")
async def diagnostics_bundle(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    return await build_diagnostics_payload(db)
