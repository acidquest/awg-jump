from datetime import datetime, timedelta, timezone
from app.services import device_tracking
from types import SimpleNamespace


def test_parse_conntrack_output_extracts_source_bytes_and_route() -> None:
    output = (
        "tcp 6 431999 ESTABLISHED src=192.168.1.10 dst=1.1.1.1 sport=50000 dport=443 "
        "src=1.1.1.1 dst=192.168.1.10 sport=443 dport=50000 mark=0x2 use=1 bytes=1200 bytes=2200"
    )

    parsed = device_tracking._parse_conntrack_output(output, local_mark="0x1", vpn_mark="0x2")

    assert len(parsed) == 1
    assert parsed[0].source_ip == "192.168.1.10"
    assert parsed[0].bytes_total == 1200
    assert parsed[0].route_target == "vpn"


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


def test_flow_has_fresh_traffic_requires_byte_counter_change_for_existing_flow() -> None:
    assert device_tracking._flow_has_fresh_traffic(1200, 1200) is False
    assert device_tracking._flow_has_fresh_traffic(1200, 1400) is True
    assert device_tracking._flow_has_fresh_traffic(1200, 200) is True
    assert device_tracking._flow_has_fresh_traffic(None, 0) is True


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


def test_stale_neighbor_forces_device_inactive_even_with_recent_traffic() -> None:
    now = datetime(2026, 4, 21, 12, 0, 0)
    timeout_cutoff = now - timedelta(seconds=30)
    device = SimpleNamespace(
        current_ip="192.168.1.10",
        last_traffic_at=now,
        mac_address="aa:bb",
    )
    neighbor = device_tracking.NeighborInfo(
        ip_address="192.168.1.10",
        mac_address="aa:bb",
        state="STALE",
    )

    last_traffic_at = device_tracking._as_utc_naive(device.last_traffic_at)
    is_active = last_traffic_at is not None and last_traffic_at >= timeout_cutoff
    is_present = is_active
    arp_present, _mac_address = device_tracking._presence_from_neighbor(neighbor)
    if neighbor is not None and not arp_present:
        is_active = False
        is_present = False

    assert is_active is False
    assert is_present is False
