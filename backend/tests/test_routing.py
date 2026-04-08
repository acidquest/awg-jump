from backend.services import routing


def test_setup_iptables_limits_output_rules_to_dns(monkeypatch):
    calls: list[tuple[str, str, list[str]]] = []

    monkeypatch.setattr(routing, "_ensure_geoip_ipset", lambda: None)
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
