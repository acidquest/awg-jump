from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AdminUser, DnsUpstream, GatewaySettings, RoutingPolicy, RuntimeMode
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
        db.add(
            GatewaySettings(
                id=1,
                ui_language=settings.ui_default_language,
                runtime_mode=RuntimeMode.auto.value,
                dns_intercept_enabled=True,
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
                servers=["77.88.8.8"],
                description="DNS servers for domains routed outside the tunnel",
            )
        )

    vpn_dns = await db.scalar(select(DnsUpstream).where(DnsUpstream.zone == "vpn"))
    if vpn_dns is None:
        db.add(
            DnsUpstream(
                zone="vpn",
                servers=[server.strip() for server in settings.default_dns_servers.split(",") if server.strip()],
                description="DNS servers for domains resolved through the tunnel",
            )
        )

    await db.commit()
