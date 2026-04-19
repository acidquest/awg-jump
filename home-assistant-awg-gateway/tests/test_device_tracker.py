from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.awg_gateway.coordinator import AwgGatewayDevicesData, AwgGatewayDevicesUpdateCoordinator
from custom_components.awg_gateway.device_tracker import AwgGatewayDeviceTracker


def _build_tracker(initial_devices: list[dict]):
    with patch("homeassistant.helpers.frame.report_usage"):
        coordinator = AwgGatewayDevicesUpdateCoordinator(
            object(),
            object(),
            "entry-1",
            {"scan_interval_seconds": 30, "device_scope": "marked"},
        )

    coordinator.data = AwgGatewayDevicesData(devices=initial_devices, devices_payload={"devices": initial_devices})
    coordinator.last_update_success = True
    entry = SimpleNamespace(entry_id="entry-1", runtime_data=SimpleNamespace(devices_coordinator=coordinator))
    return AwgGatewayDeviceTracker(entry, initial_devices[0]["identity_key"]), coordinator


def test_device_tracker_moves_to_not_home_when_device_disappears_from_payload():
    tracker, coordinator = _build_tracker(
        [
            {
                "identity_key": "mac:aa",
                "display_name": "Phone",
                "is_present": True,
                "mac_address": "AA:AA:AA:AA:AA:AA",
                "current_ip": "192.0.2.10",
            }
        ]
    )

    assert tracker.state == "home"
    assert tracker.name == "Phone"

    coordinator.data = AwgGatewayDevicesData(devices=[], devices_payload={"devices": []})

    assert tracker.state == "not_home"
    assert tracker.name == "Phone"


def test_device_tracker_is_unavailable_when_last_refresh_failed():
    tracker, coordinator = _build_tracker(
        [
            {
                "identity_key": "mac:aa",
                "display_name": "Phone",
                "is_present": True,
                "mac_address": "AA:AA:AA:AA:AA:AA",
            }
        ]
    )

    assert tracker.available is True

    coordinator.last_update_success = False

    assert tracker.available is False
