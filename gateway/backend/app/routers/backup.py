from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import AdminUser, BackupRecord, GatewaySettings
from app.security import get_current_user
from app.services.backup import (
    build_backup_bytes,
    build_diagnostics_payload,
    backup_file_path,
    create_backup_file,
    delete_backup_by_filename,
    delete_backup_record,
    prune_backup_files,
    record_backup,
    restore_backup_bytes,
    restore_backup_record,
    sync_backup_records_from_files,
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


@router.post("/create")
async def create_backup(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> JSONResponse:
    settings_row = await db.get(GatewaySettings, 1)
    result = await create_backup_file(db, kind="manual")
    if settings_row is not None:
        await prune_backup_files(db, retention_count=settings_row.backup_retention_count)
    return JSONResponse({"status": "created", **result})


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
    await sync_backup_records_from_files(db)
    rows = (await db.execute(select(BackupRecord).order_by(BackupRecord.created_at.desc()))).scalars().all()
    return [
        {
            "id": row.id,
            "filename": row.filename,
            "size_bytes": row.size_bytes,
            "kind": row.kind,
            "created_at": row.created_at.isoformat(),
            "exists": backup_file_path(row.filename).is_file(),
        }
        for row in rows
    ]


@router.get("/{backup_id}/download")
async def download_backup_record(
    backup_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> FileResponse:
    row = await db.get(BackupRecord, backup_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backup record not found")
    target = backup_file_path(row.filename)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Backup file is missing on the server")
    return FileResponse(target, media_type="application/zip", filename=row.filename)


@router.get("/record/{backup_id}/download")
async def download_backup_record_explicit(
    backup_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> FileResponse:
    return await download_backup_record(backup_id, db, user)


@router.get("/download")
async def download_backup_by_filename(
    filename: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> FileResponse:
    await sync_backup_records_from_files(db)
    target = backup_file_path(filename)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Backup file is missing on the server")
    return FileResponse(target, media_type="application/zip", filename=filename)


@router.post("/{backup_id}/restore")
async def restore_backup_record_route(
    backup_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> JSONResponse:
    row = await db.get(BackupRecord, backup_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backup record not found")
    try:
        result = await restore_backup_record(db, row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/record/{backup_id}/restore")
async def restore_backup_record_route_explicit(
    backup_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> JSONResponse:
    return await restore_backup_record_route(backup_id, db, user)


@router.post("/restore-file")
async def restore_backup_by_filename(
    filename: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> JSONResponse:
    await sync_backup_records_from_files(db)
    target = backup_file_path(filename)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Backup file is missing on the server")
    try:
        result = restore_backup_bytes(target.read_bytes())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await record_backup(db, filename=filename, size_bytes=target.stat().st_size, kind="restore")
    return JSONResponse(result)


@router.delete("/{backup_id}")
async def delete_backup_record_route(
    backup_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> JSONResponse:
    row = await db.get(BackupRecord, backup_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backup record not found")
    result = await delete_backup_record(db, row)
    return JSONResponse(result)


@router.delete("/record/{backup_id}")
async def delete_backup_record_route_explicit(
    backup_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> JSONResponse:
    return await delete_backup_record_route(backup_id, db, user)


@router.post("/delete-file")
async def delete_backup_by_filename_route(
    filename: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> JSONResponse:
    await sync_backup_records_from_files(db)
    result = await delete_backup_by_filename(db, filename)
    if not result["deleted_file"]:
        raise HTTPException(status_code=404, detail="Backup file is missing on the server")
    return JSONResponse(result)


@router.get("/diagnostics")
async def diagnostics_bundle(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    return await build_diagnostics_payload(db)
