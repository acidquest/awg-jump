"""Constants for the AWG Gateway integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "awg_gateway"

CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_SCAN_INTERVAL: Final = "scan_interval_seconds"
CONF_USE_HTTPS: Final = "use_https"
CONF_DEVICE_SCOPE: Final = "device_scope"

DEFAULT_PORT: Final = 8081
DEFAULT_USE_HTTPS: Final = True
DEFAULT_VERIFY_SSL: Final = True
DEFAULT_SCAN_INTERVAL: Final = 30
DEFAULT_DEVICE_SCOPE: Final = "marked"

DEVICE_SCOPE_ALL: Final = "all"
DEVICE_SCOPE_MARKED: Final = "marked"
DEVICE_SCOPES: Final = [DEVICE_SCOPE_MARKED, DEVICE_SCOPE_ALL]

PLATFORMS: Final = ["sensor", "switch", "device_tracker"]

API_TIMEOUT_SECONDS: Final = 15

COORDINATOR_DATA_STATUS: Final = "status"
COORDINATOR_DATA_DEVICES: Final = "devices"

DEFAULT_UPDATE_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
