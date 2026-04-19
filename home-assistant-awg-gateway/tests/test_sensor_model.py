from __future__ import annotations

from custom_components.awg_gateway.sensor import SENSORS, _routing_mode_value


def test_traffic_sensors_are_compact_and_monotonic():
    traffic_sensors = [sensor for sensor in SENSORS if sensor.key.startswith("traffic_")]

    assert [sensor.key for sensor in traffic_sensors] == [
        "traffic_local_rx_total",
        "traffic_local_tx_total",
        "traffic_vpn_rx_total",
        "traffic_vpn_tx_total",
    ]
    assert [sensor.name for sensor in traffic_sensors] == [
        "Local Download",
        "Local Upload",
        "VPN Download",
        "VPN Upload",
    ]
    assert all(sensor.state_class.value == "total_increasing" for sensor in traffic_sensors)


def test_switch_and_sensor_names_are_explicit():
    unnamed = [sensor.key for sensor in SENSORS if not sensor.name]

    assert unnamed == []


def test_routing_mode_is_exposed_as_human_readable_value():
    assert _routing_mode_value({"routing_mode": {"target": "local"}}) == "Direct"
    assert _routing_mode_value({"routing_mode": {"target": "awg"}}) == "VPN"
