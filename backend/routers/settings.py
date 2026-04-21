from __future__ import annotations

import hashlib
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.config import reload_settings, settings
from backend.env_manager import update_env_file
from backend.routers.auth import get_current_user


router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    web_mode: str = Field(pattern="^(http|https)$")
    web_port: int = Field(ge=1, le=65535)


class PasswordUpdate(BaseModel):
    current_password: str
    new_password: str = Field(min_length=4, max_length=256)


def _cert_meta(path: str) -> dict | None:
    cert_path = Path(path)
    if not cert_path.exists():
        return None
    content = cert_path.read_bytes()
    return {
        "path": str(cert_path),
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


@router.get("")
async def get_settings(_user: str = Depends(get_current_user)) -> dict:
    reload_settings()
    return {
        "admin_username": settings.admin_username,
        "web_mode": settings.web_mode,
        "web_port": settings.web_port,
        "tls_common_name": settings.tls_common_name,
        "tls_cert_path": settings.tls_cert_path,
        "tls_key_path": settings.tls_key_path,
        "cert": _cert_meta(settings.tls_cert_path),
        "restart_required": False,
    }


@router.put("")
async def update_settings(
    payload: SettingsUpdate,
    _user: str = Depends(get_current_user),
) -> dict:
    changed = (
        payload.web_mode != settings.web_mode
        or payload.web_port != settings.web_port
    )
    update_env_file(
        {
            "WEB_MODE": payload.web_mode,
            "WEB_PORT": str(payload.web_port),
        }
    )
    return {
        "status": "updated",
        "restart_required": changed,
        "web_mode": payload.web_mode,
        "web_port": payload.web_port,
    }


@router.post("/password")
async def update_password(
    payload: PasswordUpdate,
    _user: str = Depends(get_current_user),
) -> dict:
    reload_settings()
    if payload.current_password != settings.admin_password:
        raise HTTPException(status_code=400, detail="Current password is invalid")
    update_env_file({"ADMIN_PASSWORD": payload.new_password})
    return {"status": "updated", "restart_required": False}


@router.post("/tls")
async def upload_tls_material(
    cert_file: UploadFile = File(...),
    key_file: UploadFile = File(...),
    _user: str = Depends(get_current_user),
) -> dict:
    cert_bytes = await cert_file.read()
    key_bytes = await key_file.read()
    if b"BEGIN CERTIFICATE" not in cert_bytes:
        raise HTTPException(status_code=400, detail="Certificate must be PEM encoded")
    if b"BEGIN" not in key_bytes or b"PRIVATE KEY" not in key_bytes:
        raise HTTPException(status_code=400, detail="Private key must be PEM encoded")

    os.makedirs(settings.certs_dir, exist_ok=True)
    Path(settings.tls_cert_path).write_bytes(cert_bytes)
    Path(settings.tls_key_path).write_bytes(key_bytes)
    os.chmod(settings.tls_key_path, 0o600)
    reload_settings()
    return {
        "status": "updated",
        "restart_required": True,
        "cert": _cert_meta(settings.tls_cert_path),
    }
