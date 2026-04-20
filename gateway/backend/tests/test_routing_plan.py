from types import SimpleNamespace

import pytest

from app.services.routing import build_routing_plan, sync_prefix_ipset
from app.services.runtime_state import reset_gateway_runtime_state, set_tunnel_runtime_state


@pytest.fixture(autouse=True)
def tunnel_runtime_running():
    reset_gateway_runtime_state()
    set_tunnel_runtime_state(status="running")
    yield
    reset_gateway_runtime_state()


def make_settings(source_cidrs: list[str] | None = None, experimental_nftables: bool = False):
    return SimpleNamespace(
        traffic_source_mode="cidr_list",
        allowed_client_cidrs=source_cidrs or ["127.0.0.0/8"],
        allowed_client_hosts=[],
        dns_intercept_enabled=True,
        experimental_nftables=experimental_nftables,
        external_ip_local_service_url="https://ipinfo.io/ip",
        external_ip_vpn_service_url="https://ifconfig.me/ip",
        tunnel_status="running",
    )


def make_policy():
    return SimpleNamespace(
        geoip_enabled=True,
        countries_enabled=True,
        geoip_countries=["ru"],
        manual_prefixes_enabled=True,
        manual_prefixes=["1.1.1.1/32"],
        fqdn_prefixes_enabled=False,
        fqdn_prefixes=[],
        geoip_ipset_name="routing_prefixes",
        prefixes_route_local=True,
        kill_switch_enabled=True,
    )


def make_active_node():
    return SimpleNamespace(endpoint_host="72.56.6.16")


def test_plan_enables_safe_block_without_active_node(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: [])
    plan = build_routing_plan(make_settings(source_cidrs=["10.10.0.0/24"]), make_policy(), None)
    assert not plan["safe_to_apply"]
    assert any("AWG_GW_FORWARD -s 10.10.0.0/24 ! -o awg-gw0 -m mark --mark 0x2 -j DROP" in command for command in plan["commands"])
    assert "No active entry node selected" in plan["warnings"]
    assert "1.1.1.1/32" in plan["manual_prefixes"]


def test_plan_does_not_add_host_route_for_entry_endpoint(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])
    plan = build_routing_plan(make_settings(), make_policy(), make_active_node())
    assert plan["safe_to_apply"]
    assert not any("72.56.6.16/32 via" in command for command in plan["commands"])
    assert any("ip route replace default dev" in command for command in plan["commands"])


def test_plan_reflects_prefix_direction_rules(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])
    monkeypatch.setattr("app.services.routing.ipset_manager.count", lambda _: 0)
    policy = make_policy()
    policy.prefixes_route_local = False
    plan = build_routing_plan(make_settings(), policy, make_active_node())
    assert any("--match-set routing_prefixes_geoip dst -j MARK --set-mark 0x2" in command for command in plan["commands"])
    assert any("AWG_GW_OUTPUT -j MARK --set-mark 0x1" in command for command in plan["commands"])
    assert any("--match-set routing_prefixes_geoip dst -j RETURN" in command and "AWG_GW_OUTPUT" in command for command in plan["commands"])


def test_plan_uses_default_prefix_when_all_blocks_disabled(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: [])
    monkeypatch.setattr("app.services.routing.ipset_manager.count", lambda _: 0)
    policy = make_policy()
    policy.countries_enabled = False
    policy.manual_prefixes_enabled = False
    settings = make_settings()
    settings.external_ip_local_service_url = ""
    settings.external_ip_vpn_service_url = ""
    plan = build_routing_plan(settings, policy, make_active_node())
    assert plan["prefix_summary"]["fallback_default_route"] is True
    assert plan["geoip_prefix_count"] == 1


def test_default_prefix_is_expanded_for_ipset_restore(monkeypatch) -> None:
    from app.services.ipset_manager import _populate

    calls: list[str] = []

    def fake_run(args, input_data=None):
        calls.append(input_data or "")
        return 0, ""

    monkeypatch.setattr("app.services.ipset_manager._run", fake_run)
    _populate("routing_prefixes_new", ["0.0.0.0/0"])
    assert calls
    assert "add routing_prefixes_new 0.0.0.0/1" in calls[0]
    assert "add routing_prefixes_new 128.0.0.0/1" in calls[0]


def test_plan_includes_dns_intercept_for_localhost(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])
    monkeypatch.setattr("app.services.routing._dns_runtime_uid", lambda: 65534)
    monkeypatch.setattr("app.services.routing.ipset_manager.count", lambda _: 0)
    plan = build_routing_plan(make_settings(), make_policy(), make_active_node())
    assert any("AWG_GW_DNS_OUTPUT" in command and "--dport 53" in command for command in plan["commands"])


def test_plan_switches_preview_to_nftables(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing._connected_ipv4_prefixes", lambda _: ["192.168.10.0/24"])
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])
    monkeypatch.setattr("app.services.routing.nftables_manager.count", lambda _: 0)
    plan = build_routing_plan(make_settings(experimental_nftables=True), make_policy(), make_active_node())
    assert plan["firewall_backend"] == "nftables"
    assert any(command.startswith("nft add table ip awg_gw") for command in plan["commands"])
    assert any(command == "nft insert rule ip filter FORWARD jump AWG_GW_FORWARD" for command in plan["commands"])
    assert any(command.startswith("nft add rule ip filter AWG_GW_FORWARD ") for command in plan["commands"])
    assert any(command == "nft add rule ip awg_gw mangle_output ip daddr 192.168.10.0/24 return" for command in plan["commands"])
    assert any("ct mark set 0x1 meta mark set 0x1 counter return" in command for command in plan["commands"])
    assert any(command == "ip rule add fwmark 0x1 table 200" for command in plan["commands"])
    assert any(
        command == 'nft add rule ip awg_gw nat_postrouting oifname "awg-gw0" meta mark 0x2 counter masquerade'
        for command in plan["commands"]
    )
    assert any(
        command == 'nft add rule ip awg_gw mangle_output oifname "awg-gw0" tcp flags syn / syn,rst tcp option maxseg size set 1260'
        for command in plan["commands"]
    )
    assert not any(command.startswith("iptables ") for command in plan["commands"])


def test_plan_marks_localhost_output_for_both_destinations(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing._connected_ipv4_prefixes", lambda _: ["192.168.10.0/24"])
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])
    monkeypatch.setattr("app.services.routing.ipset_manager.count", lambda _: 0)

    plan = build_routing_plan(make_settings(), make_policy(), make_active_node())

    assert any(command == "iptables -t mangle -A AWG_GW_OUTPUT -d 192.168.10.0/24 -j RETURN" for command in plan["commands"])
    assert any("AWG_GW_OUTPUT -m set --match-set routing_prefixes_geoip dst -j MARK --set-mark 0x1" in command for command in plan["commands"])
    assert any(command == "iptables -t mangle -A AWG_GW_OUTPUT -j MARK --set-mark 0x2" for command in plan["commands"])
    assert any(command == "ip rule add fwmark 0x1 table 200" for command in plan["commands"])
    assert not any("iptables -t filter -A AWG_GW_OUTPUT" in command for command in plan["commands"])


def test_plan_applies_forced_device_route_before_generic_prerouting_rules(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing._connected_ipv4_prefixes", lambda _: ["192.168.10.0/24"])
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])
    monkeypatch.setattr("app.services.routing.ipset_manager.count", lambda _: 0)
    monkeypatch.setattr(
        "app.services.routing._load_device_route_overrides",
        lambda: [("10.10.0.5", "local"), ("10.99.0.5", "vpn")],
    )

    plan = build_routing_plan(make_settings(source_cidrs=["10.10.0.0/24"]), make_policy(), make_active_node())

    assert any(
        command == "iptables -t mangle -A AWG_GW_PREROUTING -s 10.10.0.5 -j CONNMARK --set-mark 0x1"
        for command in plan["commands"]
    )
    assert any(
        command == "iptables -t mangle -A AWG_GW_PREROUTING -s 10.10.0.5 -j MARK --set-mark 0x1"
        for command in plan["commands"]
    )
    assert any(
        command == "iptables -t mangle -A AWG_GW_PREROUTING -s 10.10.0.5 -j RETURN"
        for command in plan["commands"]
    )
    assert not any("10.99.0.5" in command for command in plan["commands"])


def test_plan_handles_localhost_and_prerouting_selectors_together(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing._connected_ipv4_prefixes", lambda _: ["192.168.10.0/24"])
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])
    monkeypatch.setattr("app.services.routing._dns_runtime_uid", lambda: 65534)
    monkeypatch.setattr("app.services.routing.ipset_manager.count", lambda _: 0)

    plan = build_routing_plan(make_settings(source_cidrs=["127.0.0.0/8", "10.10.0.0/24"]), make_policy(), make_active_node())

    assert any(command == "iptables -t mangle -A AWG_GW_OUTPUT -j MARK --set-mark 0x2" for command in plan["commands"])
    assert any(command == "iptables -t mangle -A AWG_GW_PREROUTING -s 10.10.0.0/24 -j MARK --set-mark 0x2" for command in plan["commands"])
    assert any(command == "iptables -t nat -A AWG_GW_DNS_PREROUTING -s 10.10.0.0/24 -p udp --dport 53 -j REDIRECT --to-ports 53" for command in plan["commands"])
    assert any(command == "iptables -t nat -A AWG_GW_DNS_OUTPUT -p udp --dport 53 -m owner ! --uid-owner 65534 -j REDIRECT --to-ports 53" for command in plan["commands"])
    assert any(
        command == "iptables -t nat -A AWG_GW_POSTROUTING -s 10.10.0.0/24 -o eth0 -m mark --mark 0x1 -j MASQUERADE"
        for command in plan["commands"]
    )
    assert any(
        command == "iptables -t filter -A AWG_GW_FORWARD -s 10.10.0.0/24 -o eth0 -m mark --mark 0x1 -j ACCEPT"
        for command in plan["commands"]
    )
    assert any(
        command == "iptables -t mangle -A AWG_GW_FORWARD_MANGLE -s 10.10.0.0/24 -o awg-gw0 -p tcp -m tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1260"
        for command in plan["commands"]
    )


def test_plan_limits_direct_nft_forward_and_nat_to_local_mark(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing._connected_ipv4_prefixes", lambda _: [])
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])
    monkeypatch.setattr("app.services.routing.nftables_manager.count", lambda _: 0)

    plan = build_routing_plan(make_settings(source_cidrs=["10.10.0.0/24"], experimental_nftables=True), make_policy(), make_active_node())

    assert any(
        command == 'nft add rule ip awg_gw nat_postrouting ip saddr 10.10.0.0/24 oifname "eth0" meta mark 0x1 counter masquerade'
        for command in plan["commands"]
    )
    assert any(
        command == 'nft add rule ip filter AWG_GW_FORWARD ip saddr 10.10.0.0/24 oifname "eth0" meta mark 0x1 counter accept'
        for command in plan["commands"]
    )


def test_plan_applies_forced_device_route_in_nftables(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing._connected_ipv4_prefixes", lambda _: [])
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])
    monkeypatch.setattr("app.services.routing.nftables_manager.count", lambda _: 0)
    monkeypatch.setattr("app.services.routing._load_device_route_overrides", lambda: [("10.10.0.7", "vpn")])

    plan = build_routing_plan(make_settings(source_cidrs=["10.10.0.0/24"], experimental_nftables=True), make_policy(), make_active_node())

    assert any(
        command == "nft add rule ip awg_gw mangle_prerouting ip saddr 10.10.0.7 ct mark set 0x2 meta mark set 0x2 counter return"
        for command in plan["commands"]
    )


def test_plan_exempts_connected_lan_from_selected_cidr_marking(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing._connected_ipv4_prefixes", lambda _: ["192.168.10.0/24"])
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])
    monkeypatch.setattr("app.services.routing.ipset_manager.count", lambda _: 0)

    plan = build_routing_plan(make_settings(source_cidrs=["192.168.10.0/24"]), make_policy(), make_active_node())

    assert any(command == "iptables -t mangle -A AWG_GW_PREROUTING -d 192.168.10.0/24 -j RETURN" for command in plan["commands"])
    assert any(
        command == "iptables -t mangle -A AWG_GW_PREROUTING -s 192.168.10.0/24 -j MARK --set-mark 0x2"
        for command in plan["commands"]
    )


def test_plan_combines_countries_manual_and_fqdn_match_sets(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing._connected_ipv4_prefixes", lambda _: [])
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])
    monkeypatch.setattr("app.services.routing.ipset_manager.count", lambda name: 2 if name == "routing_prefixes_fqdn" else 0)

    policy = make_policy()
    policy.fqdn_prefixes_enabled = True
    policy.fqdn_prefixes = ["example.com"]

    plan = build_routing_plan(make_settings(), policy, make_active_node())

    assert any("--match-set routing_prefixes_geoip dst" in command for command in plan["commands"])
    assert any("--match-set routing_prefixes_manual dst" in command for command in plan["commands"])
    assert any("--match-set routing_prefixes_fqdn dst" in command for command in plan["commands"])
    assert plan["prefix_summary"]["resolved_prefixes"] == 2


def test_sync_prefix_ipset_keeps_fqdn_set_when_block_disabled(monkeypatch) -> None:
    from app.services.routing import sync_prefix_ipset

    policy = make_policy()
    policy.fqdn_prefixes_enabled = False
    policy.fqdn_prefixes = ["example.com"]

    create_calls: list[tuple[str, tuple[str, ...]]] = []

    monkeypatch.setattr("app.services.routing.ipset_manager.create_or_update", lambda name, prefixes: create_calls.append((name, tuple(prefixes))))
    monkeypatch.setattr("app.services.routing.ipset_manager.exists", lambda name: True)
    monkeypatch.setattr("app.services.routing.ipset_manager.create", lambda name: create_calls.append((name, tuple())))
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: [])

    sync_prefix_ipset(policy)

    assert ("routing_prefixes_manual", ("1.1.1.1/32",)) in create_calls
    assert ("routing_prefixes_fqdn", tuple()) in create_calls


def test_plan_ignores_disabled_fqdn_match_set(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: [])
    monkeypatch.setattr("app.services.routing.ipset_manager.count", lambda _: 5)

    policy = make_policy()
    policy.countries_enabled = False
    policy.manual_prefixes_enabled = False
    policy.fqdn_prefixes_enabled = False
    policy.fqdn_prefixes = ["example.com"]

    settings = make_settings()
    settings.external_ip_local_service_url = ""
    settings.external_ip_vpn_service_url = ""
    plan = build_routing_plan(settings, policy, make_active_node())

    assert not any("--match-set routing_prefixes_fqdn" in command for command in plan["commands"])
    assert plan["prefix_summary"]["resolved_prefixes"] == 0


def test_plan_uses_system_fqdn_host_without_fallback_default_route(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: [])
    monkeypatch.setattr("app.services.routing.ipset_manager.count", lambda name: 1 if name == "routing_prefixes_fqdn" else 0)

    policy = make_policy()
    policy.countries_enabled = False
    policy.manual_prefixes_enabled = False
    policy.fqdn_prefixes_enabled = False
    policy.fqdn_prefixes = []

    plan = build_routing_plan(make_settings(), policy, make_active_node())

    assert plan["prefix_summary"]["fallback_default_route"] is False
    assert any("--match-set routing_prefixes_fqdn dst" in command for command in plan["commands"])


def test_sync_prefix_ipset_flushes_fqdn_runtime_set_on_dns_reload(monkeypatch) -> None:
    from app.services.routing import sync_prefix_ipset

    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr("app.services.routing.ipset_manager.create_or_update", lambda name, prefixes: calls.append((name, tuple(prefixes))))
    monkeypatch.setattr("app.services.routing.ipset_manager.exists", lambda name: True)
    monkeypatch.setattr("app.services.routing.ipset_manager.create", lambda name: calls.append((name, tuple())))
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: [])

    policy = make_policy()
    policy.fqdn_prefixes_enabled = True
    policy.fqdn_prefixes = ["example.com"]

    sync_prefix_ipset(policy, flush_fqdn=True)

    assert ("routing_prefixes_fqdn", tuple()) in calls


def test_sync_prefix_ipset_creates_all_enabled_sets(monkeypatch) -> None:
    from app.services.routing import sync_prefix_ipset

    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr("app.services.routing.ipset_manager.create_or_update", lambda name, prefixes: calls.append((name, tuple(prefixes))))
    monkeypatch.setattr("app.services.routing.ipset_manager.exists", lambda name: False)
    monkeypatch.setattr("app.services.routing.ipset_manager.create", lambda name: calls.append((name, tuple())))
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])

    policy = make_policy()
    policy.fqdn_prefixes_enabled = True
    policy.fqdn_prefixes = ["example.com"]

    sync_prefix_ipset(policy)

    assert ("routing_prefixes_geoip", ("203.0.113.0/24",)) in calls
    assert ("routing_prefixes_manual", ("1.1.1.1/32",)) in calls
    assert ("routing_prefixes_fqdn", tuple()) in calls


def test_sync_prefix_ipset_splits_geoip_and_manual(monkeypatch) -> None:
    from app.services.routing import sync_prefix_ipset

    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr("app.services.routing.ipset_manager.create_or_update", lambda name, prefixes: calls.append((name, tuple(prefixes))))
    monkeypatch.setattr("app.services.routing.ipset_manager.exists", lambda name: True)
    monkeypatch.setattr("app.services.routing.ipset_manager.create", lambda name: calls.append((name, tuple())))
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])

    sync_prefix_ipset(make_policy())

    assert ("routing_prefixes_geoip", ("203.0.113.0/24",)) in calls
    assert ("routing_prefixes_manual", ("1.1.1.1/32",)) in calls


def test_sync_prefix_ipset_uses_nft_manager_when_enabled(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr("app.services.routing.nftables_manager.create_or_update", lambda name, prefixes: calls.append((name, tuple(prefixes))))
    monkeypatch.setattr("app.services.routing.nftables_manager.exists", lambda name: True)
    monkeypatch.setattr("app.services.routing.nftables_manager.create", lambda name: calls.append((name, tuple())))
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])

    sync_prefix_ipset(make_policy(), make_settings(experimental_nftables=True))

    assert ("routing_prefixes_geoip", ("203.0.113.0/24",)) in calls
    assert ("routing_prefixes_manual", ("1.1.1.1/32",)) in calls
