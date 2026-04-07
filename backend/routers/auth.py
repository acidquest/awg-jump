"""
Авторизация — сессии в памяти, Bearer-токены.
Credentials никогда не логируются и не попадают в ответы.
"""
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from backend.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── In-memory сессии ─────────────────────────────────────────────────────
# token → {"username": str, "expires_at": datetime}
_sessions: dict[str, dict] = {}

# ── Защита от брутфорса ───────────────────────────────────────────────────
# ip → {"count": int, "locked_until": datetime | None}
_login_attempts: dict[str, dict] = defaultdict(lambda: {"count": 0, "locked_until": None})
_MAX_ATTEMPTS = 10
_LOCKOUT_MINUTES = 15

_security = HTTPBearer(auto_error=False)


def _cleanup_expired() -> None:
    now = datetime.now(timezone.utc)
    expired = [t for t, s in _sessions.items() if s["expires_at"] < now]
    for t in expired:
        del _sessions[t]


def create_session(username: str) -> str:
    _cleanup_expired()
    token = secrets.token_hex(32)
    _sessions[token] = {
        "username": username,
        "expires_at": datetime.now(timezone.utc)
        + timedelta(hours=settings.session_ttl_hours),
    }
    return token


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> str:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    session = _sessions.get(token)
    if session is None or session["expires_at"] < datetime.now(timezone.utc):
        if token in _sessions:
            del _sessions[token]
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return session["username"]


# ── Schemas ───────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_hours: int


# ── Routes ────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    client_ip = request.client.host if request.client else "unknown"
    now = datetime.now(timezone.utc)

    attempt_data = _login_attempts[client_ip]
    locked_until = attempt_data["locked_until"]
    if locked_until and now < locked_until:
        remaining = int((locked_until - now).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {remaining} seconds.",
        )

    valid = (
        body.username == settings.admin_username
        and body.password == settings.admin_password
    )

    if not valid:
        attempt_data["count"] += 1
        if attempt_data["count"] >= _MAX_ATTEMPTS:
            attempt_data["locked_until"] = now + timedelta(minutes=_LOCKOUT_MINUTES)
            attempt_data["count"] = 0
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # Успешный вход — сброс счётчика
    attempt_data["count"] = 0
    attempt_data["locked_until"] = None

    token = create_session(body.username)
    return TokenResponse(
        access_token=token,
        expires_in_hours=settings.session_ttl_hours,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> None:
    if credentials and credentials.credentials in _sessions:
        del _sessions[credentials.credentials]


@router.get("/me")
async def me(username: str = Depends(get_current_user)) -> dict:
    return {"username": username}
