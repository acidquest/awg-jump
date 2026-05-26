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

    assert (
        "mangle",
        "FORWARD",
        [
            "-p", "tcp",
            "--tcp-flags", "SYN,RST", "SYN",
            "-o", "awg1",
            "-j", "TCPMSS", "--set-mss", "1260",
        ],
    ) in calls


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


def test_setup_iptables_geoip_upstream_keeps_geoip_on_local_mark(monkeypatch):
    calls: list[tuple[str, str, list[str]]] = []

    monkeypatch.setattr(routing, "_ensure_geoip_ipset", lambda: None)
    monkeypatch.setattr(routing, "_ensure_excluded_ipset", lambda: None)
    monkeypatch.setattr(routing, "_remove_all_policy_mark_rules", lambda: None)
    monkeypatch.setattr(
        routing,
        "_ipt_add",
        lambda table, chain, rule_args: calls.append((table, chain, rule_args.copy())),
    )

    routing.setup_iptables(invert_geoip=True, geoip_upstream_enabled=True)

    prerouting_rules = [
        rule_args for table, chain, rule_args in calls if table == "mangle" and chain == "PREROUTING"
    ]
    geoip_rules = [rule for rule in prerouting_rules if "geoip_local" in rule]
    assert geoip_rules
    assert any(routing.settings.fwmark_local in rule_args for rule_args in geoip_rules)
    assert (
        "mangle",
        "FORWARD",
        [
            "-p", "tcp",
            "--tcp-flags", "SYN,RST", "SYN",
            "-o", "awg2",
            "-j", "TCPMSS", "--set-mss", "1260",
        ],
    ) in calls
    assert ("nat", "POSTROUTING", ["-o", "awg2", "-j", "MASQUERADE"]) in calls


def test_setup_iptables_geoip_upstream_marks_exclusions_first(monkeypatch):
    calls: list[tuple[str, str, list[str]]] = []
    synced: list[list[str]] = []

    monkeypatch.setattr(routing, "_ensure_geoip_ipset", lambda: None)
    monkeypatch.setattr(routing, "_ensure_excluded_ipset", lambda: None)
    monkeypatch.setattr(routing, "_sync_excluded_ipset", lambda prefixes: synced.append(prefixes.copy()))
    monkeypatch.setattr(routing, "_remove_all_policy_mark_rules", lambda: None)
    monkeypatch.setattr(
        routing,
        "_ipt_add",
        lambda table, chain, rule_args: calls.append((table, chain, rule_args.copy())),
    )

    routing.setup_iptables(
        geoip_upstream_enabled=True,
        excluded_prefixes=["203.0.113.10/32"],
    )

    assert synced == [["203.0.113.10/32"]]
    prerouting_rules = [
        rule_args for table, chain, rule_args in calls if table == "mangle" and chain == "PREROUTING"
    ]
    assert prerouting_rules[0] == [
        "-i", "awg0",
        "-m", "set", "--match-set", "geoip_excluded", "dst",
        "-j", "MARK", "--set-mark", "0x3",
    ]
    assert "geoip_excluded" in prerouting_rules[1]
    assert "!" in prerouting_rules[1]


def test_setup_policy_routing_can_route_geoip_table_to_awg2(monkeypatch):
    routes: list[tuple[int, list[str]]] = []

    monkeypatch.setattr(routing, "_rule_exists", lambda _fwmark, _table: True)
    monkeypatch.setattr(routing, "_get_default_gateway", lambda _iface=None: "172.18.0.1")
    monkeypatch.setattr(
        routing,
        "_ensure_route",
        lambda table, route_args, description: routes.append((table, route_args.copy())),
    )
    monkeypatch.setattr(routing, "_delete_route", lambda *args, **kwargs: None)

    routing.setup_policy_routing("awg2")

    assert (routing.settings.routing_table_local, ["default", "dev", "awg2", "metric", "100"]) in routes


def test_setup_policy_routing_can_route_exclusions_to_local_table(monkeypatch):
    routes: list[tuple[int, list[str]]] = []
    rules: list[tuple[str, int]] = []

    monkeypatch.setattr(routing, "_rule_exists", lambda _fwmark, _table: False)
    monkeypatch.setattr(routing, "_get_default_gateway", lambda _iface=None: "172.18.0.1")
    monkeypatch.setattr(
        routing,
        "_run",
        lambda args: (rules.append((args[4], int(args[6]))) or (0, "")) if args[:3] == ["ip", "rule", "add"] else (0, ""),
    )
    monkeypatch.setattr(
        routing,
        "_ensure_route",
        lambda table, route_args, description: routes.append((table, route_args.copy())),
    )
    monkeypatch.setattr(routing, "_delete_route", lambda *args, **kwargs: None)

    routing.setup_policy_routing("awg2")

    assert ("0x3", 300) in rules
    assert (
        routing.settings.routing_table_excluded,
        ["default", "via", "172.18.0.1", "dev", routing.settings.physical_iface, "metric", "100"],
    ) in routes


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
