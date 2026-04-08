from backend.services import routing
import pytest


def test_setup_iptables_limits_output_rules_to_dns(monkeypatch):
    calls: list[tuple[str, str, list[str]]] = []

    monkeypatch.setattr(routing, "_ensure_geoip_ipset", lambda: None)
    monkeypatch.setattr(routing, "_remove_all_policy_mark_rules", lambda: None)
    monkeypatch.setattr(
        routing,
        "_ipt_add",
        lambda table, chain, rule_args: calls.append((table, chain, rule_args.copy())),
    )

    routing.setup_iptables()

    output_rules = [rule_args for table, chain, rule_args in calls if table == "mangle" and chain == "OUTPUT"]
    assert len(output_rules) == 4

    for rule_args in output_rules:
        assert "-p" in rule_args
        assert "--dport" in rule_args
        assert "53" in rule_args

    for proto in ("udp", "tcp"):
        assert any(rule_args[:4] == ["-p", proto, "--dport", "53"] for rule_args in output_rules)


def test_setup_iptables_inverted_swaps_marks(monkeypatch):
    calls: list[tuple[str, str, list[str]]] = []

    monkeypatch.setattr(routing, "_ensure_geoip_ipset", lambda: None)
    monkeypatch.setattr(routing, "_remove_all_policy_mark_rules", lambda: None)
    monkeypatch.setattr(
        routing,
        "_ipt_add",
        lambda table, chain, rule_args: calls.append((table, chain, rule_args.copy())),
    )

    routing.setup_iptables(invert_geoip=True)

    prerouting_rules = [
        rule_args for table, chain, rule_args in calls if table == "mangle" and chain == "PREROUTING"
    ]
    assert any(routing.settings.fwmark_vpn in rule_args for rule_args in prerouting_rules)
    assert any(routing.settings.fwmark_local in rule_args for rule_args in prerouting_rules)


@pytest.mark.asyncio
async def test_get_routing_status_includes_mode(client, auth_headers):
    resp = await client.get("/api/routing/status", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["invert_geoip"] is False
    assert data["geoip_destination"] == "local"
    assert data["other_destination"] == "vpn"


@pytest.mark.asyncio
async def test_update_routing_settings_applies_inverted_mode(client, auth_headers):
    resp = await client.put(
        "/api/routing/settings",
        headers=auth_headers,
        json={"invert_geoip": True},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "updated"
    assert data["invert_geoip"] is True
    assert data["geoip_destination"] == "vpn"
    assert data["other_destination"] == "local"
