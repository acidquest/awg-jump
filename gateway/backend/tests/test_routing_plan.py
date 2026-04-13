from types import SimpleNamespace

from app.services.routing import build_routing_plan


def make_settings(mode: str = "localhost"):
    return SimpleNamespace(
        traffic_source_mode=mode,
        allowed_client_cidrs=["192.168.10.0/24"],
        allowed_client_hosts=["192.168.10.50"],
        dns_intercept_enabled=True,
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
        strict_mode=True,
    )


def make_active_node():
    return SimpleNamespace(endpoint_host="72.56.6.16")


def test_plan_enables_safe_block_without_active_node(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: [])
    plan = build_routing_plan(make_settings(), make_policy(), None)
    assert not plan["safe_to_apply"]
    assert any("REJECT" in command for command in plan["commands"])
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
    assert not any(
        "--match-set routing_prefixes_geoip dst -j RETURN" in command and "AWG_GW_OUTPUT" in command
        for command in plan["commands"]
    )


def test_plan_uses_default_prefix_when_all_blocks_disabled(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: [])
    monkeypatch.setattr("app.services.routing.ipset_manager.count", lambda _: 0)
    policy = make_policy()
    policy.countries_enabled = False
    policy.manual_prefixes_enabled = False
    plan = build_routing_plan(make_settings(), policy, make_active_node())
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
    assert not any(name == "routing_prefixes_fqdn" and prefixes == tuple() for name, prefixes in create_calls)


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
