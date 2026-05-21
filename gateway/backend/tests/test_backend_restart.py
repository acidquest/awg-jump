from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.backend_restart import (
    backend_restart_is_due,
    normalize_backend_restart_interval_days,
    normalize_backend_restart_time,
)


def _settings(**overrides):
    values = {
        "backend_restart_enabled": True,
        "backend_restart_interval_days": 7,
        "backend_restart_time": "04:00",
        "backend_restart_last_requested_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_backend_restart_due_after_configured_time_without_previous_run() -> None:
    assert backend_restart_is_due(_settings(), now=datetime(2026, 5, 17, 4, 0, 0)) is True


def test_backend_restart_not_due_before_configured_time() -> None:
    assert backend_restart_is_due(_settings(), now=datetime(2026, 5, 17, 3, 59, 0)) is False


def test_backend_restart_does_not_repeat_on_same_day() -> None:
    assert backend_restart_is_due(
        _settings(backend_restart_last_requested_at=datetime(2026, 5, 17, 4, 0, 1)),
        now=datetime(2026, 5, 17, 5, 0, 0),
    ) is False


def test_backend_restart_respects_interval_days() -> None:
    last_requested_at = datetime(2026, 5, 17, 4, 0, 1)

    assert backend_restart_is_due(
        _settings(backend_restart_interval_days=7, backend_restart_last_requested_at=last_requested_at),
        now=last_requested_at + timedelta(days=6, hours=1),
    ) is False
    assert backend_restart_is_due(
        _settings(backend_restart_interval_days=7, backend_restart_last_requested_at=last_requested_at),
        now=last_requested_at + timedelta(days=7, hours=1),
    ) is True


def test_backend_restart_normalizers_validate_ranges_and_time() -> None:
    assert normalize_backend_restart_time("4:05") == "04:05"
    assert normalize_backend_restart_interval_days(31) == 31
    with pytest.raises(ValueError):
        normalize_backend_restart_interval_days(0)
    with pytest.raises(ValueError):
        normalize_backend_restart_interval_days(32)
    with pytest.raises(ValueError):
        normalize_backend_restart_time("25:00")
