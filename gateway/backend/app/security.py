from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
import ipaddress

from fastapi import Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import AdminUser, GatewaySettings


_security = HTTPBearer(auto_error=False)
_sessions: dict[str, dict[str, str | datetime]] = {}


def hash_password(password: str, *, salt: str | None = None) -> str:
    salt_value = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt_value.encode(),
        200_000,
    )
    return f"pbkdf2_sha256${salt_value}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, salt, digest = password_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    expected = hash_password(password, salt=salt)
    return hmac.compare_digest(expected, password_hash)


def create_session(username: str) -> str:
    token = secrets.token_hex(32)
    _sessions[token] = {
        "username": username,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours),
    }
    return token


def generate_api_access_key(length: int = 32) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def remove_session(token: str) -> None:
    _sessions.pop(token, None)


def clear_sessions() -> None:
    _sessions.clear()


def _get_session(token: str) -> dict[str, str | datetime] | None:
    session = _sessions.get(token)
    if session is None:
        return None
    expires_at = session["expires_at"]
    if isinstance(expires_at, datetime) and expires_at < datetime.now(timezone.utc):
        _sessions.pop(token, None)
        return None
    return session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    raw_token = credentials.credentials if credentials is not None else token
    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    session = _get_session(raw_token)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await db.scalar(select(AdminUser).where(AdminUser.username == session["username"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_api_settings(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> GatewaySettings:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header is required",
        )

    settings_row = await db.get(GatewaySettings, 1)
    if settings_row is None or not settings_row.api_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API access is disabled")
    if not settings_row.api_access_key or not hmac.compare_digest(settings_row.api_access_key, x_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    client_ip = resolve_request_ip(request)
    if settings_row.api_allowed_client_cidrs and not is_ip_allowed(client_ip, settings_row.api_allowed_client_cidrs):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API access is denied for this IP")
    return settings_row


async def require_api_control(
    settings_row: GatewaySettings = Depends(get_api_settings),
) -> GatewaySettings:
    if not settings_row.api_control_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API control mode is disabled")
    return settings_row


def resolve_request_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else None


def is_ip_allowed(client_ip: str | None, allowed_cidrs: list[str]) -> bool:
    if not allowed_cidrs:
        return True
    if not client_ip:
        return False
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for raw_cidr in allowed_cidrs:
        try:
            if address in ipaddress.ip_network(raw_cidr, strict=False):
                return True
        except ValueError:
            continue
    return False
