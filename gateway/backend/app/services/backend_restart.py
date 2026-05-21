from __future__ import annotations

import os
import signal
from datetime import datetime

from app.models import GatewaySettings


def normalize_backend_restart_time(raw_value: str) -> str:
    candidate = raw_value.strip()
    try:
        parsed = datetime.strptime(candidate, "%H:%M")
    except ValueError as exc:
        raise ValueError("backend_restart_time must be in HH:MM format") from exc
    return parsed.strftime("%H:%M")


def normalize_backend_restart_interval_days(raw_value: int) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("backend_restart_interval_days must be an integer") from exc
    if value < 1 or value > 31:
        raise ValueError("backend_restart_interval_days must be in range 1..31")
    return value


def backend_restart_is_due(settings_row: GatewaySettings, *, now: datetime) -> bool:
    if not settings_row.backend_restart_enabled:
        return False
    try:
        hour, minute = [int(part) for part in settings_row.backend_restart_time.split(":", 1)]
    except (TypeError, ValueError):
        return False

    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < scheduled:
        return False

    last_requested_at = settings_row.backend_restart_last_requested_at
    if last_requested_at is None:
        return True
    if last_requested_at.tzinfo is not None and now.tzinfo is not None:
        last_requested_at = last_requested_at.astimezone(now.tzinfo).replace(tzinfo=None)
    elif last_requested_at.tzinfo is not None:
        last_requested_at = last_requested_at.replace(tzinfo=None)

    if last_requested_at.date() == now.date():
        return False
    return (now.date() - last_requested_at.date()).days >= settings_row.backend_restart_interval_days


def request_backend_restart() -> None:
    os.kill(os.getpid(), signal.SIGTERM)
