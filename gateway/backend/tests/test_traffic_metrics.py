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
