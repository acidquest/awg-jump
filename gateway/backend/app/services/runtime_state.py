from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.models import TunnelStatus


@dataclass
class ExternalIpProbeState:
    value: str | None = None
    error: str | None = None
    checked_at: datetime | None = None


@dataclass
class TunnelRuntimeState:
    status: str = TunnelStatus.stopped.value
    last_error: str | None = None
    connected_at_epoch: int | None = None
    last_applied_at: datetime | None = None


@dataclass
class NodeRuntimeState:
    latency_ms: float | None = None
    latency_at: datetime | None = None
    latency_target: str | None = None
    latency_via_interface: str | None = None
    latency_method: str | None = None
    last_error: str | None = None


@dataclass
class FailoverRuntimeState:
    unhealthy_since: datetime | None = None
    last_event_at: datetime | None = None
    last_error: str | None = None


@dataclass
class GatewayRuntimeState:
    tunnel: TunnelRuntimeState = field(default_factory=TunnelRuntimeState)
    failover: FailoverRuntimeState = field(default_factory=FailoverRuntimeState)
    external_ip_local: ExternalIpProbeState = field(default_factory=ExternalIpProbeState)
    external_ip_vpn: ExternalIpProbeState = field(default_factory=ExternalIpProbeState)
    node_state: dict[int, NodeRuntimeState] = field(default_factory=dict)


_STATE = GatewayRuntimeState()


def gateway_runtime_state() -> GatewayRuntimeState:
    return _STATE


def get_tunnel_runtime_state() -> TunnelRuntimeState:
    return _STATE.tunnel


def get_failover_runtime_state() -> FailoverRuntimeState:
    return _STATE.failover


def set_failover_runtime_state(
    *,
    unhealthy_since: datetime | None = None,
    last_event_at: datetime | None = None,
    last_error: str | None = None,
) -> FailoverRuntimeState:
    state = _STATE.failover
    state.unhealthy_since = unhealthy_since
    state.last_event_at = last_event_at
    state.last_error = last_error
    return state


def set_tunnel_runtime_state(
    *,
    status: str | None = None,
    last_error: str | None = None,
    connected_at_epoch: int | None = None,
    last_applied_at: datetime | None = None,
) -> TunnelRuntimeState:
    state = _STATE.tunnel
    if status is not None:
        state.status = status
    state.last_error = last_error
    state.connected_at_epoch = connected_at_epoch
    if last_applied_at is not None:
        state.last_applied_at = last_applied_at
    return state


def get_node_runtime_state(node_id: int | None) -> NodeRuntimeState:
    if node_id is None:
        return NodeRuntimeState()
    if node_id not in _STATE.node_state:
        _STATE.node_state[node_id] = NodeRuntimeState()
    return _STATE.node_state[node_id]


def update_node_runtime_state(
    node_id: int | None,
    *,
    latency_ms: float | None = None,
    latency_at: datetime | None = None,
    latency_target: str | None = None,
    latency_via_interface: str | None = None,
    latency_method: str | None = None,
    last_error: str | None = None,
) -> NodeRuntimeState:
    state = get_node_runtime_state(node_id)
    state.latency_ms = latency_ms
    state.latency_at = latency_at
    state.latency_target = latency_target
    state.latency_via_interface = latency_via_interface
    state.latency_method = latency_method
    state.last_error = last_error
    return state


def should_refresh_node_latency(node_id: int | None, *, ttl_seconds: int = 20) -> bool:
    state = get_node_runtime_state(node_id)
    if state.latency_at is None:
        return True
    latency_at = state.latency_at
    if latency_at.tzinfo is None:
        latency_at = latency_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - latency_at >= timedelta(seconds=ttl_seconds)


def clear_node_runtime_state(node_id: int | None) -> None:
    if node_id is None:
        return
    _STATE.node_state.pop(node_id, None)


def reset_gateway_runtime_state() -> None:
    global _STATE
    _STATE = GatewayRuntimeState()
