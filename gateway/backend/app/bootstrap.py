from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AdminUser, DnsUpstream, GatewaySettings, RoutingPolicy, RuntimeMode
from app.services.external_ip import validate_service_pair
from app.services.traffic_sources import default_allowed_source_cidrs
from app.security import hash_password


async def ensure_bootstrap_state(db: AsyncSession) -> None:
    admin = await db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_username))
    if admin is None:
        db.add(
            AdminUser(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                password_changed=False,
            )
        )

    gateway_settings = await db.get(GatewaySettings, 1)
    if gateway_settings is None:
        local_service_url, vpn_service_url = validate_service_pair(
            settings.external_ip_local_service_url,
            settings.external_ip_vpn_service_url,
        )
        db.add(
            GatewaySettings(
                id=1,
                ui_language=settings.ui_default_language,
                runtime_mode=RuntimeMode.auto.value,
                allowed_client_cidrs=default_allowed_source_cidrs(),
                gateway_enabled=True,
                dns_intercept_enabled=True,
                experimental_nftables=False,
                api_enabled=False,
                api_access_key=None,
                api_control_enabled=False,
                api_allowed_client_cidrs=[],
                device_tracking_enabled=True,
                device_activity_timeout_seconds=300,
                device_api_default_scope="all",
                external_ip_local_service_url=local_service_url,
                external_ip_vpn_service_url=vpn_service_url,
            )
        )

    routing_policy = await db.get(RoutingPolicy, 1)
    if routing_policy is None:
        db.add(RoutingPolicy(id=1))

    local_dns = await db.scalar(select(DnsUpstream).where(DnsUpstream.zone == "local"))
    if local_dns is None:
        db.add(
            DnsUpstream(
                zone="local",
                name="Local",
                servers=["77.88.8.8"],
                description="",
                is_builtin=True,
                protocol="plain",
            )
        )

    vpn_dns = await db.scalar(select(DnsUpstream).where(DnsUpstream.zone == "vpn"))
    if vpn_dns is None:
        db.add(
            DnsUpstream(
                zone="vpn",
                name="Upstream",
                servers=[server.strip() for server in settings.default_dns_servers.split(",") if server.strip()],
                description="",
                is_builtin=True,
                protocol="plain",
            )
        )

    await db.commit()
