import pytest
from fastapi import HTTPException

from app.models import GatewaySettings, RoutingPolicy
from app.routers import settings as settings_router


class FakeDb:
    def __init__(self) -> None:
        self.settings = GatewaySettings(
            id=1,
            ui_language="en",
            runtime_mode="auto",
            allowed_client_cidrs=[],
            allowed_client_hosts=[],
            dns_intercept_enabled=True,
            experimental_nftables=False,
            external_ip_local_service_url="https://ipinfo.io/ip",
            external_ip_vpn_service_url="https://ifconfig.me/ip",
        )
        self.policy = RoutingPolicy(id=1)

    async def get(self, model, key):
        if model is GatewaySettings and key == 1:
            return self.settings
        if model is RoutingPolicy and key == 1:
            return self.policy
        return None

    def add(self, _obj) -> None:
        pass

    async def flush(self) -> None:
        pass


@pytest.mark.asyncio
async def test_update_settings_returns_http_error_when_dnsmasq_restart_fails(monkeypatch) -> None:
    async def fake_restart_dnsmasq(_db) -> dict:
        raise RuntimeError("permission denied")

    async def fake_refresh_external_ip_info(_db, _settings_row, _policy, *, force: bool = False) -> dict:
        return {"ok": True, "force": force}

    monkeypatch.setattr(settings_router, "restart_dnsmasq", fake_restart_dnsmasq)
    monkeypatch.setattr(settings_router, "sync_firewall_backend", lambda _settings_row, _policy: None)
    monkeypatch.setattr(settings_router, "refresh_external_ip_info", fake_refresh_external_ip_info)

    payload = settings_router.GatewaySettingsUpdate(
        ui_language="ru",
        runtime_mode="auto",
        allowed_client_cidrs=["192.168.10.0/24"],
        dns_intercept_enabled=True,
        experimental_nftables=False,
        external_ip_local_service_url="https://ipinfo.io/ip",
        external_ip_vpn_service_url="https://ifconfig.me/ip",
    )

    with pytest.raises(HTTPException) as exc_info:
        await settings_router.update_settings(payload, db=FakeDb(), user=None)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to restart dnsmasq: permission denied"
