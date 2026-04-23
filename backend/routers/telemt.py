from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.env_manager import update_env_file
from backend.models.telemt_user import TelemtUser
from backend.routers.auth import get_current_user
from backend.services import telemt as telemt_svc

router = APIRouter(prefix="/api/telemt", tags=["telemt"])


class TelemtSettingsUpdate(BaseModel):
    config_text: str = Field(min_length=1)


class TelemtUserCreate(BaseModel):
    username: str
    secret_hex: str | None = None
    enabled: bool = True


class TelemtUserUpdate(BaseModel):
    username: str
    secret_hex: str | None = None
    enabled: bool = True


async def _get_user_or_404(user_id: int, session: AsyncSession) -> TelemtUser:
    user = await session.get(TelemtUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="TeleMT user not found")
    return user


def _feature_guard() -> None:
    if not telemt_svc.runtime_enabled():
        raise HTTPException(status_code=404, detail="TeleMT feature is disabled")


@router.get("")
async def get_telemt_page(
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    _feature_guard()
    return await telemt_svc.build_page_payload(session)


@router.get("/users")
async def get_telemt_users(
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[dict]:
    _feature_guard()
    payload = await telemt_svc.build_page_payload(session)
    return payload["users"]


@router.put("/settings")
async def update_telemt_settings(
    payload: TelemtSettingsUpdate,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    _feature_guard()
    row = await telemt_svc.ensure_settings_row(session)
    try:
        config_text = telemt_svc.normalize_config_text(payload.config_text)
        parsed_port = telemt_svc.extract_port(config_text)
        parsed_tls_domain = telemt_svc.extract_tls_domain(config_text) or row.tls_domain
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    container_restart_required = parsed_port != row.port

    row.config_text = config_text
    row.port = parsed_port
    row.tls_domain = parsed_tls_domain
    row.restart_required = container_restart_required
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)

    if container_restart_required:
        update_env_file(
            {
                "TELEMT_PORT": str(parsed_port),
            }
        )

    await telemt_svc.refresh_generated_config(session)
    return {
        "status": "updated",
        "restart_required": row.restart_required,
        "service_restart_required": True,
        "settings": {
            "config_text": row.config_text,
            "port": row.port,
            "public_host": row.public_host,
            "restart_required": row.restart_required,
            "service_autostart": row.service_autostart,
            "docs_url": telemt_svc.TELEMT_CONFIG_DOCS_URL,
        },
    }


@router.post("/users", status_code=201)
async def create_telemt_user(
    payload: TelemtUserCreate,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    _feature_guard()
    try:
        username = telemt_svc.normalize_username(payload.username)
        secret_hex = telemt_svc.normalize_secret(payload.secret_hex or telemt_svc.generate_secret())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = TelemtUser(username=username, secret_hex=secret_hex, enabled=payload.enabled)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="TeleMT username already exists") from exc

    await telemt_svc.refresh_generated_config(session)
    return await telemt_svc.build_page_payload(session)


@router.put("/users/{user_id}")
async def update_telemt_user(
    user_id: int,
    payload: TelemtUserUpdate,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    _feature_guard()
    user = await _get_user_or_404(user_id, session)
    try:
        user.username = telemt_svc.normalize_username(payload.username)
        if payload.secret_hex is not None:
            user.secret_hex = telemt_svc.normalize_secret(payload.secret_hex)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user.enabled = payload.enabled
    user.updated_at = datetime.now(timezone.utc)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="TeleMT username already exists") from exc

    await telemt_svc.refresh_generated_config(session)
    return await telemt_svc.build_page_payload(session)


@router.delete("/users/{user_id}")
async def delete_telemt_user(
    user_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    _feature_guard()
    user = await _get_user_or_404(user_id, session)
    await session.delete(user)
    await telemt_svc.refresh_generated_config(session)
    return await telemt_svc.build_page_payload(session)


@router.post("/service/{action}")
async def telemt_service_action(
    action: str,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    _feature_guard()
    try:
        await telemt_svc.refresh_generated_config(session)
        result = telemt_svc.control_service(action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    desired_state = telemt_svc.service_autostart_for_action(action)
    if desired_state is not None and result.get("ok"):
        row = await telemt_svc.ensure_settings_row(session)
        row.service_autostart = desired_state
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        result["service_autostart"] = desired_state
    return result
