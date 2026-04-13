from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import AdminUser


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


def remove_session(token: str) -> None:
    _sessions.pop(token, None)


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
