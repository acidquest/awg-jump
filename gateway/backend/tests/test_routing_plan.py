from types import SimpleNamespace

from app.services.routing import build_routing_plan


def make_settings(mode: str = "localhost"):
    return SimpleNamespace(
        traffic_source_mode=mode,
        allowed_client_cidrs=["192.168.10.0/24"],
        allowed_client_hosts=["192.168.10.50"],
        tunnel_status="running",
    )


def make_policy():
    return SimpleNamespace(
        geoip_enabled=True,
        geoip_countries=["ru"],
        manual_prefixes=["1.1.1.1/32"],
        geoip_ipset_name="gateway_geoip_local",
        invert_geoip=False,
        default_policy="vpn",
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


def test_plan_reflects_inverted_geoip_rules(monkeypatch) -> None:
    monkeypatch.setattr("app.services.routing._default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setattr("app.services.routing._interface_exists", lambda _: True)
    monkeypatch.setattr("app.services.routing.load_cached_country", lambda _: ["203.0.113.0/24"])
    policy = make_policy()
    policy.invert_geoip = True
    plan = build_routing_plan(make_settings(), policy, make_active_node())
    assert any("--match-set gateway_geoip_local dst -j MARK --set-mark 0x2" in command for command in plan["commands"])
    assert not any(
        "--match-set gateway_geoip_local dst -j RETURN" in command and "AWG_GW_OUTPUT" in command
        for command in plan["commands"]
    )
