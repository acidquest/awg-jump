from datetime import datetime, timedelta, timezone
from app.services import device_tracking
from app.models import METRICS_DB_TABLES, TrackedDevice, TrackedDeviceFlowState
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _create_metrics_session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: [table.create(sync_conn, checkfirst=True) for table in METRICS_DB_TABLES])
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    return engine, session_factory


def _settings_row() -> SimpleNamespace:
    return SimpleNamespace(
        device_tracking_enabled=True,
        device_activity_timeout_seconds=300,
        allowed_client_cidrs=["192.168.1.0/24"],
    )


def test_parse_conntrack_output_extracts_source_total_bytes_and_route() -> None:
    output = (
        "tcp 6 431999 ESTABLISHED src=192.168.1.10 dst=1.1.1.1 sport=50000 dport=443 "
        "src=1.1.1.1 dst=192.168.1.10 sport=443 dport=50000 mark=0x2 use=1 bytes=1200 bytes=2200"
    )

    parsed = device_tracking._parse_conntrack_output(output, local_mark="0x1", vpn_mark="0x2")

    assert len(parsed) == 1
    assert parsed[0].source_ip == "192.168.1.10"
    assert parsed[0].bytes_total == 3400
    assert parsed[0].route_target == "vpn"


def test_parse_conntrack_output_counts_reply_direction_growth() -> None:
    previous = (
        "tcp 6 431999 ESTABLISHED src=192.168.1.10 dst=1.1.1.1 sport=50000 dport=443 "
        "src=1.1.1.1 dst=192.168.1.10 sport=443 dport=50000 mark=0x2 use=1 bytes=1200 bytes=2200"
    )
    current = (
        "tcp 6 431999 ESTABLISHED src=192.168.1.10 dst=1.1.1.1 sport=50000 dport=443 "
        "src=1.1.1.1 dst=192.168.1.10 sport=443 dport=50000 mark=0x2 use=1 bytes=1200 bytes=5200"
    )

    previous_parsed = device_tracking._parse_conntrack_output(previous)[0]
    current_parsed = device_tracking._parse_conntrack_output(current)[0]

    assert previous_parsed.flow_key == current_parsed.flow_key
    assert device_tracking._flow_has_fresh_traffic(previous_parsed.bytes_total, current_parsed.bytes_total) is True
    assert device_tracking._flow_delta(previous_parsed.bytes_total, current_parsed.bytes_total) == 3000


def test_parse_conntrack_output_selects_tracked_ip_from_reply_destination() -> None:
    output = (
        "tcp 6 431999 ESTABLISHED src=1.1.1.1 dst=203.0.113.10 sport=443 dport=50000 "
        "src=203.0.113.10 dst=192.168.1.10 sport=50000 dport=443 mark=0x2 use=1 bytes=1200 bytes=5200"
    )

    parsed = device_tracking._parse_conntrack_output(output, selectors=["192.168.1.0/24"])

    assert len(parsed) == 1
    assert parsed[0].source_ip == "192.168.1.10"
    assert parsed[0].bytes_total == 6400


def test_parse_conntrack_output_marks_missing_byte_counters() -> None:
    output = (
        "tcp 6 431999 ESTABLISHED src=192.168.1.10 dst=1.1.1.1 sport=50000 dport=443 "
        "src=1.1.1.1 dst=192.168.1.10 sport=443 dport=50000 mark=0x2 use=1"
    )

    parsed = device_tracking._parse_conntrack_output(output)

    assert len(parsed) == 1
    assert parsed[0].source_ip == "192.168.1.10"
    assert parsed[0].bytes_total == 0
    assert parsed[0].has_byte_counters is False


def test_new_flow_observation_without_byte_counters_is_active_once() -> None:
    observation = device_tracking.FlowObservation(
        flow_key="tcp|192.168.1.10|1.1.1.1|50000|443",
        source_ip="192.168.1.10",
        bytes_total=0,
        route_target="vpn",
        has_byte_counters=False,
    )

    assert device_tracking._flow_observation_is_active(None, observation) is True


def test_existing_flow_observation_without_byte_counters_does_not_stay_active() -> None:
    observation = device_tracking.FlowObservation(
        flow_key="tcp|192.168.1.10|1.1.1.1|50000|443",
        source_ip="192.168.1.10",
        bytes_total=0,
        route_target="vpn",
        has_byte_counters=False,
    )
    flow_state = SimpleNamespace(last_bytes=0)

    assert device_tracking._flow_observation_is_active(flow_state, observation) is False


def test_parse_ip_neigh_output_extracts_mac_and_state() -> None:
    output = "192.168.1.10 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE"

    parsed = device_tracking._parse_ip_neigh_output(output)

    assert parsed["192.168.1.10"].mac_address == "aa:bb:cc:dd:ee:ff"
    assert parsed["192.168.1.10"].state == "REACHABLE"


def test_presence_from_neighbor_uses_only_confirmed_states() -> None:
    present, mac = device_tracking._presence_from_neighbor(
        device_tracking.NeighborInfo(ip_address="192.168.1.10", mac_address="aa:bb", state="REACHABLE")
    )

    assert present is True
    assert mac == "aa:bb"


def test_presence_from_neighbor_ignores_stale_entries() -> None:
    present, mac = device_tracking._presence_from_neighbor(
        device_tracking.NeighborInfo(ip_address="192.168.1.10", mac_address="aa:bb", state="STALE")
    )

    assert present is False
    assert mac == "aa:bb"


def test_confirm_neighbor_presence_uses_ping_for_stale_neighbor(monkeypatch) -> None:
    neighbor = device_tracking.NeighborInfo(ip_address="192.168.1.10", mac_address="aa:bb", state="STALE")
    monkeypatch.setattr(device_tracking, "_ping", lambda ip_address: ip_address == "192.168.1.10")

    assert device_tracking._confirm_neighbor_presence(neighbor) is True


def test_confirm_neighbor_presence_ignores_failed_neighbor(monkeypatch) -> None:
    neighbor = device_tracking.NeighborInfo(ip_address="192.168.1.10", mac_address=None, state="FAILED")
    monkeypatch.setattr(device_tracking, "_ping", lambda _ip: True)

    assert device_tracking._confirm_neighbor_presence(neighbor) is False


def test_flow_has_fresh_traffic_requires_byte_counter_change_for_existing_flow() -> None:
    assert device_tracking._flow_has_fresh_traffic(1200, 1200) is False
    assert device_tracking._flow_has_fresh_traffic(1200, 1400) is True
    assert device_tracking._flow_has_fresh_traffic(1200, 200) is True
    assert device_tracking._flow_has_fresh_traffic(None, 1200) is True
    assert device_tracking._flow_has_fresh_traffic(None, 0) is False


def test_flow_delta_handles_new_growing_and_reset_counters() -> None:
    assert device_tracking._flow_delta(None, 1200) == 1200
    assert device_tracking._flow_delta(1200, 1400) == 200
    assert device_tracking._flow_delta(1200, 1200) == 0
    assert device_tracking._flow_delta(1200, 200) == 200


def test_ip_in_selectors_matches_only_selected_networks() -> None:
    assert device_tracking._ip_in_selectors("192.168.1.10", ["192.168.1.0/24"]) is True
    assert device_tracking._ip_in_selectors("10.0.0.5", ["192.168.1.0/24"]) is False
    assert device_tracking._ip_in_selectors("127.0.0.1", ["127.0.0.0/8"]) is False


def test_coerce_device_defaults_restores_legacy_null_fields() -> None:
    device = SimpleNamespace(
        total_bytes=None,
        is_marked=None,
        forced_route_target=None,
        manual_alias=None,
        last_route_target=None,
    )

    device_tracking._coerce_device_defaults(device)

    assert device.total_bytes == 0
    assert device.is_marked is False
    assert device.forced_route_target == "none"
    assert device.manual_alias == ""
    assert device.last_route_target == "unknown"


def test_as_utc_naive_normalizes_aware_values_for_internal_comparisons() -> None:
    aware = datetime(2026, 4, 20, 19, 44, 7, tzinfo=timezone.utc)

    normalized = device_tracking._as_utc_naive(aware)

    assert normalized == datetime(2026, 4, 20, 19, 44, 7)
    assert normalized.tzinfo is None


def test_latest_timestamp_returns_most_recent_naive_utc_value() -> None:
    latest = device_tracking._latest_timestamp(
        datetime(2026, 4, 21, 11, 59, 0),
        datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc),
        None,
    )

    assert latest == datetime(2026, 4, 21, 12, 0, 0)
    assert latest.tzinfo is None


def test_stale_neighbor_does_not_keep_device_present_when_ping_fails(monkeypatch) -> None:
    now = datetime(2026, 4, 21, 12, 0, 0)
    device = SimpleNamespace(
        current_ip="192.168.1.10",
        last_traffic_at=now - timedelta(seconds=45),
        last_present_at=now - timedelta(seconds=20),
        last_seen_at=now - timedelta(seconds=20),
        mac_address="aa:bb",
    )
    neighbor = device_tracking.NeighborInfo(
        ip_address="192.168.1.10",
        mac_address="aa:bb",
        state="STALE",
    )
    monkeypatch.setattr(device_tracking, "_ping", lambda _ip: False)

    is_active, is_present, confirmed_present, mac_address = device_tracking._evaluate_device_presence(
        device,
        neighbor=neighbor,
        now=now,
        activity_timeout_seconds=30,
    )

    assert is_active is False
    assert is_present is False
    assert confirmed_present is False
    assert mac_address == "aa:bb"


def test_fresh_traffic_confirms_presence_even_with_stale_neighbor() -> None:
    now = datetime(2026, 4, 21, 12, 0, 0)
    device = SimpleNamespace(
        current_ip="192.168.1.10",
        last_traffic_at=now - timedelta(seconds=5),
        last_present_at=now - timedelta(seconds=120),
        last_seen_at=now - timedelta(seconds=120),
        mac_address="aa:bb",
    )
    neighbor = device_tracking.NeighborInfo(
        ip_address="192.168.1.10",
        mac_address="aa:bb",
        state="STALE",
    )

    is_active, is_present, confirmed_present, mac_address = device_tracking._evaluate_device_presence(
        device,
        neighbor=neighbor,
        now=now,
        activity_timeout_seconds=300,
    )

    assert is_active is True
    assert is_present is True
    assert confirmed_present is True
    assert mac_address == "aa:bb"


def test_stale_neighbor_becomes_inactive_after_timeout_and_failed_ping(monkeypatch) -> None:
    now = datetime(2026, 4, 21, 12, 0, 0)
    device = SimpleNamespace(
        current_ip="192.168.1.10",
        last_traffic_at=now - timedelta(seconds=120),
        last_present_at=now - timedelta(seconds=120),
        last_seen_at=now - timedelta(seconds=120),
        mac_address="aa:bb",
    )
    neighbor = device_tracking.NeighborInfo(
        ip_address="192.168.1.10",
        mac_address="aa:bb",
        state="STALE",
    )
    monkeypatch.setattr(device_tracking, "_ping", lambda _ip: False)

    is_active, is_present, confirmed_present, mac_address = device_tracking._evaluate_device_presence(
        device,
        neighbor=neighbor,
        now=now,
        activity_timeout_seconds=30,
    )

    assert is_active is False
    assert is_present is False
    assert confirmed_present is False
    assert mac_address == "aa:bb"


def test_reachable_neighbor_without_traffic_is_present_but_not_active() -> None:
    now = datetime(2026, 4, 21, 12, 0, 0)
    device = SimpleNamespace(
        current_ip="192.168.1.10",
        last_traffic_at=now - timedelta(seconds=120),
        last_present_at=now - timedelta(seconds=120),
        last_seen_at=now - timedelta(seconds=5),
        mac_address="aa:bb",
    )
    neighbor = device_tracking.NeighborInfo(
        ip_address="192.168.1.10",
        mac_address="aa:bb",
        state="REACHABLE",
    )

    is_active, is_present, confirmed_present, mac_address = device_tracking._evaluate_device_presence(
        device,
        neighbor=neighbor,
        now=now,
        activity_timeout_seconds=30,
    )

    assert is_active is False
    assert is_present is True
    assert confirmed_present is True
    assert mac_address == "aa:bb"


@pytest.mark.asyncio
async def test_collect_device_inventory_creates_present_neighbor_only_device(monkeypatch, tmp_path) -> None:
    engine, session_factory = await _create_metrics_session(tmp_path)

    async def load_neighbors():
        return {
            "192.168.1.10": device_tracking.NeighborInfo(
                ip_address="192.168.1.10",
                mac_address="aa:bb:cc:dd:ee:ff",
                state="REACHABLE",
            )
        }

    async def load_conntrack_observations(selectors=None):
        return []

    monkeypatch.setattr(
        device_tracking,
        "_load_neighbors",
        load_neighbors,
    )
    monkeypatch.setattr(device_tracking, "_load_conntrack_observations", load_conntrack_observations)
    monkeypatch.setattr(device_tracking, "_resolve_hostname", lambda _ip: None)

    try:
        async with session_factory() as session:
            await device_tracking.collect_device_inventory(session, _settings_row())
            await session.flush()

            device = await session.scalar(select(TrackedDevice).where(TrackedDevice.current_ip == "192.168.1.10"))

            assert device is not None
            assert device.mac_address == "aa:bb:cc:dd:ee:ff"
            assert device.is_present is True
            assert device.is_active is False
            assert device.last_present_at is not None
            assert device.last_presence_check_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_collect_device_inventory_creates_active_device_from_first_conntrack_observation(monkeypatch, tmp_path) -> None:
    engine, session_factory = await _create_metrics_session(tmp_path)
    observation = device_tracking.FlowObservation(
        flow_key="tcp|192.168.1.20|1.1.1.1|50000|443",
        source_ip="192.168.1.20",
        bytes_total=1200,
        route_target="vpn",
    )

    async def load_neighbors():
        return {
            "192.168.1.20": device_tracking.NeighborInfo(
                ip_address="192.168.1.20",
                mac_address="11:22:33:44:55:66",
                state="STALE",
            )
        }

    async def load_conntrack_observations(selectors=None):
        return [observation]

    monkeypatch.setattr(
        device_tracking,
        "_load_neighbors",
        load_neighbors,
    )
    monkeypatch.setattr(device_tracking, "_load_conntrack_observations", load_conntrack_observations)
    monkeypatch.setattr(device_tracking, "_resolve_hostname", lambda _ip: None)

    try:
        async with session_factory() as session:
            await device_tracking.collect_device_inventory(session, _settings_row())
            await session.flush()

            device = await session.scalar(select(TrackedDevice).where(TrackedDevice.current_ip == "192.168.1.20"))
            flow_state = await session.get(TrackedDeviceFlowState, observation.flow_key)

            assert device is not None
            assert device.is_present is True
            assert device.is_active is True
            assert device.last_route_target == "vpn"
            assert device.total_bytes == 1200
            assert flow_state is not None
            assert flow_state.device_id == device.id
    finally:
        await engine.dispose()
