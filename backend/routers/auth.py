"""
Авторизация — сессии в памяти, Bearer-токены.
Credentials никогда не логируются и не попадают в ответы.
"""
import secrets
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
async def login(body: LoginRequest) -> TokenResponse:
    if (
        body.username != settings.admin_username
        or body.password != settings.admin_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
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
