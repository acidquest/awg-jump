from types import SimpleNamespace

import httpx

from app.services.geoip import refresh_policy_geoip


def make_policy():
    return SimpleNamespace(
        geoip_countries=["ru"],
        manual_prefixes=["1.1.1.1/32"],
        geoip_ipset_name="routing_prefixes",
    )


async def test_refresh_policy_geoip_uses_cache_on_http_error(monkeypatch) -> None:
    async def fail_fetch(country_code: str) -> list[str]:
        raise httpx.ReadError("stream broken")

    monkeypatch.setattr("app.services.geoip.fetch_country", fail_fetch)
    monkeypatch.setattr("app.services.geoip.load_cached_country", lambda country_code: ["203.0.113.0/24"])

    result = await refresh_policy_geoip(make_policy())

    assert result["countries"] == {"ru": 1}
    assert result["prefix_count"] == 2
    assert result["manual_prefixes"] == ["1.1.1.1/32"]
