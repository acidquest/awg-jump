"""Sensor platform for AWG Gateway."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AwgGatewayConfigEntry
from .entity import AwgGatewayCoordinatorEntity


@dataclass(frozen=True, kw_only=True)
class AwgGatewaySensorDescription(SensorEntityDescription):
    """Describe an AWG Gateway sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None


def _nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _traffic_attrs(scope: str, direction: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def attrs(data: dict[str, Any]) -> dict[str, Any]:
        traffic = data.get("traffic") or {}
        current = traffic.get("current") or {}
        return {
            "traffic_scope": scope,
            "traffic_direction": direction,
            "collected_at": current.get("collected_at"),
            "local_interface_name": current.get("local_interface_name"),
            "vpn_interface_name": current.get("vpn_interface_name"),
            "last_hour_bytes": _nested(traffic, "last_hour", scope, f"{direction}_bytes"),
            "last_day_bytes": _nested(traffic, "last_day", scope, f"{direction}_bytes"),
        }

    return attrs


def _active_node_attrs(data: dict[str, Any]) -> dict[str, Any]:
    active_node = data.get("active_node") or {}
    return {
        "latency_ms": active_node.get("latency_ms"),
        "latency_target": active_node.get("latency_target"),
        "latency_via_interface": active_node.get("latency_via_interface"),
    }


def _routing_mode_attrs(data: dict[str, Any]) -> dict[str, Any]:
    routing_mode = data.get("routing_mode") or {}
    return {
        "raw_target": routing_mode.get("target"),
        "raw_label": routing_mode.get("label"),
    }


def _routing_mode_value(data: dict[str, Any]) -> str | None:
    target = _nested(data, "routing_mode", "target")
    if target == "local":
        return "Direct"
    if target == "awg":
        return "VPN"
    return target


SENSORS: tuple[AwgGatewaySensorDescription, ...] = (
    AwgGatewaySensorDescription(key="tunnel_status", name="Tunnel Status", value_fn=lambda d: _nested(d, "status", "tunnel_status")),
    AwgGatewaySensorDescription(
        key="active_node",
        name="Active Node",
        value_fn=lambda d: _nested(d, "active_node", "name"),
        attrs_fn=_active_node_attrs,
    ),
    AwgGatewaySensorDescription(
        key="active_node_latency",
        name="Active Node Latency",
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "active_node", "latency_ms"),
    ),
    AwgGatewaySensorDescription(
        key="uptime",
        name="Uptime",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("uptime_seconds"),
    ),
    AwgGatewaySensorDescription(key="active_stack", name="Active Stack", value_fn=lambda d: d.get("active_stack")),
    AwgGatewaySensorDescription(
        key="active_prefixes_count",
        name="Active Prefixes",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "active_prefixes", "count"),
    ),
    AwgGatewaySensorDescription(
        key="configured_prefixes_count",
        name="Configured Prefixes",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "active_prefixes", "configured_count"),
    ),
    AwgGatewaySensorDescription(
        key="cpu_usage",
        name="CPU Usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "system", "cpu_usage_percent"),
    ),
    AwgGatewaySensorDescription(
        key="memory_total",
        name="Memory Total",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "system", "memory_total_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="memory_used",
        name="Memory Used",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "system", "memory_used_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="memory_free",
        name="Memory Free",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "system", "memory_free_bytes"),
    ),
    AwgGatewaySensorDescription(key="external_ip_local", name="Local External IP", value_fn=lambda d: _nested(d, "external_ip", "local")),
    AwgGatewaySensorDescription(key="external_ip_vpn", name="VPN External IP", value_fn=lambda d: _nested(d, "external_ip", "vpn")),
    AwgGatewaySensorDescription(key="runtime_mode", name="Runtime Mode", value_fn=lambda d: d.get("runtime_mode")),
    AwgGatewaySensorDescription(
        key="routing_mode_target",
        name="Traffic Route",
        value_fn=_routing_mode_value,
        attrs_fn=_routing_mode_attrs,
    ),
    AwgGatewaySensorDescription(
        key="traffic_local_rx_total",
        name="Local Download",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _nested(d, "traffic", "current", "local", "rx_bytes"),
        attrs_fn=_traffic_attrs("local", "rx"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_local_tx_total",
        name="Local Upload",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _nested(d, "traffic", "current", "local", "tx_bytes"),
        attrs_fn=_traffic_attrs("local", "tx"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_vpn_rx_total",
        name="VPN Download",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _nested(d, "traffic", "current", "vpn", "rx_bytes"),
        attrs_fn=_traffic_attrs("vpn", "rx"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_vpn_tx_total",
        name="VPN Upload",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _nested(d, "traffic", "current", "vpn", "tx_bytes"),
        attrs_fn=_traffic_attrs("vpn", "tx"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AwgGatewayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AWG Gateway sensors."""
    async_add_entities(AwgGatewaySensor(entry, description) for description in SENSORS)


class AwgGatewaySensor(AwgGatewayCoordinatorEntity, SensorEntity):
    """Represent an AWG Gateway sensor."""

    entity_description: AwgGatewaySensorDescription

    def __init__(self, entry: AwgGatewayConfigEntry, description: AwgGatewaySensorDescription) -> None:
        super().__init__(entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator.data.status)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Attach selected context for complex sensors."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data.status)
