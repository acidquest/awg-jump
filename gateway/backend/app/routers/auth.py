from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AdminUser, AuditEvent
from app.security import create_session, get_current_user, remove_session, verify_password, hash_password


router = APIRouter(prefix="/api/auth", tags=["auth"])
_security = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


@router.post("/login")
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> dict:
    user = await db.scalar(select(AdminUser).where(AdminUser.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    db.add(AuditEvent(event_type="auth.login", payload={"username": user.username}))
    await db.flush()
    return {
        "access_token": create_session(user.username),
        "token_type": "bearer",
        "password_changed": user.password_changed,
    }


@router.post("/logout")
async def logout(credentials: HTTPAuthorizationCredentials | None = Depends(_security)) -> dict:
    if credentials is not None:
        remove_session(credentials.credentials)
    return {"status": "logged_out"}


@router.get("/me")
async def me(user: AdminUser = Depends(get_current_user)) -> dict:
    return {"username": user.username, "password_changed": user.password_changed}


@router.post("/change-password")
async def change_password(
    payload: PasswordChangeRequest,
    user: AdminUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is invalid")
    user.password_hash = hash_password(payload.new_password)
    user.password_changed = True
    user.updated_at = datetime.now(timezone.utc)
    db.add(user)
    db.add(AuditEvent(event_type="auth.password_changed", payload={"username": user.username}))
    await db.flush()
    return {"status": "password_changed"}
