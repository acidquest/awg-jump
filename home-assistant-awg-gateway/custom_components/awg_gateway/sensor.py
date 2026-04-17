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


def _nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


SENSORS: tuple[AwgGatewaySensorDescription, ...] = (
    AwgGatewaySensorDescription(key="tunnel_status", translation_key="tunnel_status", value_fn=lambda d: _nested(d, "status", "tunnel_status")),
    AwgGatewaySensorDescription(key="active_node", translation_key="active_node", value_fn=lambda d: _nested(d, "active_node", "name")),
    AwgGatewaySensorDescription(
        key="active_node_latency",
        translation_key="active_node_latency",
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "active_node", "latency_ms"),
    ),
    AwgGatewaySensorDescription(
        key="uptime",
        translation_key="uptime",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("uptime_seconds"),
    ),
    AwgGatewaySensorDescription(key="active_stack", translation_key="active_stack", value_fn=lambda d: d.get("active_stack")),
    AwgGatewaySensorDescription(
        key="active_prefixes_count",
        translation_key="active_prefixes_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "active_prefixes", "count"),
    ),
    AwgGatewaySensorDescription(
        key="configured_prefixes_count",
        translation_key="configured_prefixes_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "active_prefixes", "configured_count"),
    ),
    AwgGatewaySensorDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "system", "cpu_usage_percent"),
    ),
    AwgGatewaySensorDescription(
        key="memory_total",
        translation_key="memory_total",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "system", "memory_total_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="memory_used",
        translation_key="memory_used",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "system", "memory_used_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="memory_free",
        translation_key="memory_free",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "system", "memory_free_bytes"),
    ),
    AwgGatewaySensorDescription(key="external_ip_local", translation_key="external_ip_local", value_fn=lambda d: _nested(d, "external_ip", "local")),
    AwgGatewaySensorDescription(key="external_ip_vpn", translation_key="external_ip_vpn", value_fn=lambda d: _nested(d, "external_ip", "vpn")),
    AwgGatewaySensorDescription(key="runtime_mode", translation_key="runtime_mode", value_fn=lambda d: d.get("runtime_mode")),
    AwgGatewaySensorDescription(key="routing_mode_target", translation_key="routing_mode_target", value_fn=lambda d: _nested(d, "routing_mode", "target")),
    AwgGatewaySensorDescription(
        key="traffic_local_rx_total",
        translation_key="traffic_local_rx_total",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _nested(d, "traffic", "current", "local", "rx_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_local_tx_total",
        translation_key="traffic_local_tx_total",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _nested(d, "traffic", "current", "local", "tx_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_vpn_rx_total",
        translation_key="traffic_vpn_rx_total",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _nested(d, "traffic", "current", "vpn", "rx_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_vpn_tx_total",
        translation_key="traffic_vpn_tx_total",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _nested(d, "traffic", "current", "vpn", "tx_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_last_hour_local_rx",
        translation_key="traffic_last_hour_local_rx",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "traffic", "last_hour", "local", "rx_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_last_hour_local_tx",
        translation_key="traffic_last_hour_local_tx",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "traffic", "last_hour", "local", "tx_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_last_hour_vpn_rx",
        translation_key="traffic_last_hour_vpn_rx",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "traffic", "last_hour", "vpn", "rx_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_last_hour_vpn_tx",
        translation_key="traffic_last_hour_vpn_tx",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "traffic", "last_hour", "vpn", "tx_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_last_day_local_rx",
        translation_key="traffic_last_day_local_rx",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "traffic", "last_day", "local", "rx_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_last_day_local_tx",
        translation_key="traffic_last_day_local_tx",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "traffic", "last_day", "local", "tx_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_last_day_vpn_rx",
        translation_key="traffic_last_day_vpn_rx",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "traffic", "last_day", "vpn", "rx_bytes"),
    ),
    AwgGatewaySensorDescription(
        key="traffic_last_day_vpn_tx",
        translation_key="traffic_last_day_vpn_tx",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _nested(d, "traffic", "last_day", "vpn", "tx_bytes"),
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

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator.data.status)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Attach selected context for complex sensors."""
        if self.entity_description.key == "active_node":
            active_node = self.coordinator.data.status.get("active_node") or {}
            return {
                "latency_ms": active_node.get("latency_ms"),
                "latency_target": active_node.get("latency_target"),
                "latency_via_interface": active_node.get("latency_via_interface"),
            }
        if self.entity_description.key == "routing_mode_target":
            routing_mode = self.coordinator.data.status.get("routing_mode") or {}
            return {"label": routing_mode.get("label")}
        return None
