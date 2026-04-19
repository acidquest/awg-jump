from app.services import traffic_metrics


def test_sum_deltas_handles_counter_reset() -> None:
    assert traffic_metrics._sum_deltas(250, 100) == 150
    assert traffic_metrics._sum_deltas(40, 100) == 40


def test_parse_ip_link_bytes_reads_interface_totals() -> None:
    output = """
5: awg-gw0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1380 qdisc noqueue state UNKNOWN mode DEFAULT group default qlen 1000
    link/none
    RX:  bytes packets errors dropped  missed   mcast
       12345     100      0       0       0       0
    RX errors:  length    crc   frame    fifo overrun
                   0      0       0       0       0
    TX:  bytes packets errors dropped carrier collsns
       67890     200      0       0       0       0
    TX errors: aborted carrier fifo heartbeat transns
                   0       0      0         0       0
    """.strip()

    rx_bytes, tx_bytes = traffic_metrics._parse_ip_link_bytes(output)

    assert (rx_bytes, tx_bytes) == (12345, 67890)


def test_current_traffic_summary_is_read_only(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_get_current(_session):
        calls.append("current")

        class Snapshot:
            collected_at = type("T", (), {"isoformat": lambda self: "2026-04-17T22:40:25+00:00"})()
            local_interface_name = "eth0"
            vpn_interface_name = "awg-gw0"
            local_rx_bytes = 10
            local_tx_bytes = 20
            vpn_rx_bytes = 30
            vpn_tx_bytes = 40

        return Snapshot()

    async def fake_sum(_session, _model, _since):
        calls.append("sum")
        return {
            "local_rx_bytes": 1,
            "local_tx_bytes": 2,
            "vpn_rx_bytes": 3,
            "vpn_tx_bytes": 4,
        }

    async def fake_collect(_session):
        calls.append("collect")
        raise AssertionError("collect_traffic_metrics must not be called from get_traffic_usage_summary")

    monkeypatch.setattr(traffic_metrics, "get_current_traffic_usage", fake_get_current)
    monkeypatch.setattr(traffic_metrics, "_sum_aggregate_window", fake_sum)
    monkeypatch.setattr(traffic_metrics, "collect_traffic_metrics", fake_collect)

    import asyncio

    result = asyncio.run(traffic_metrics.get_traffic_usage_summary(object()))

    assert result["current"]["local"]["rx_bytes"] == 10
    assert "collect" not in calls
