from types import SimpleNamespace

from app.services.routing import build_routing_plan


def make_settings(mode: str = "localhost"):
    return SimpleNamespace(
        traffic_source_mode=mode,
        allowed_client_cidrs=["192.168.10.0/24"],
        allowed_client_hosts=["192.168.10.50"],
    )


def make_policy():
    return SimpleNamespace(
        geoip_enabled=True,
        geoip_countries=["ru"],
        manual_prefixes=["1.1.1.1/32"],
        geoip_ipset_name="gateway_geoip_local",
        invert_geoip=False,
        kill_switch_enabled=True,
    )


def test_plan_enables_safe_block_without_active_node() -> None:
    plan = build_routing_plan(make_settings(), make_policy(), None)
    assert not plan["safe_to_apply"]
    assert any("REJECT" in command for command in plan["commands"])
    assert "No active entry node selected" in plan["warnings"]
    assert "1.1.1.1/32" in plan["manual_prefixes"]
