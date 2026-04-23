from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.interface import Interface, InterfaceProtocol
from backend.models.peer import Peer
from backend.models.upstream_node import ProvisioningMode, UpstreamNode

CLIENT_OBF_FIELD_MAP = {
    "JC": "client_obf_jc",
    "JMIN": "client_obf_jmin",
    "JMAX": "client_obf_jmax",
    "S1": "client_obf_s1",
    "S2": "client_obf_s2",
    "S3": "client_obf_s3",
    "S4": "client_obf_s4",
    "H1": "client_obf_h1",
    "H2": "client_obf_h2",
    "H3": "client_obf_h3",
    "H4": "client_obf_h4",
}


async def get_awg1_or_raise(session: AsyncSession) -> Interface:
    result = await session.execute(select(Interface).where(Interface.name == "awg1"))
    iface = result.scalar_one_or_none()
    if iface is None:
        raise RuntimeError("awg1 interface not found")
    return iface


def inherit_client_settings_from_interface(node: UpstreamNode, awg1: Interface) -> None:
    node.client_address = node.client_address or awg1.address or settings.awg1_address
    node.client_dns = node.client_dns if node.client_dns is not None else awg1.dns
    node.client_allowed_ips = node.client_allowed_ips or awg1.allowed_ips or settings.awg1_allowed_ips
    node.client_keepalive = (
        node.client_keepalive
        if node.client_keepalive is not None
        else awg1.persistent_keepalive
    )
    for _key, attr in CLIENT_OBF_FIELD_MAP.items():
        if getattr(node, attr) is None:
            source_attr = attr.replace("client_", "")
            setattr(node, attr, getattr(awg1, source_attr))


def assign_client_settings_from_parsed(node: UpstreamNode, parsed) -> None:
    node.client_address = parsed.tunnel_address
    node.client_dns = ",".join(parsed.dns_servers) if parsed.dns_servers else None
    node.client_allowed_ips = ",".join(parsed.allowed_ips) if parsed.allowed_ips else None
    node.client_keepalive = parsed.persistent_keepalive
    for key, attr in CLIENT_OBF_FIELD_MAP.items():
        setattr(node, attr, parsed.obfuscation.get(key))


async def apply_node_to_awg1(session: AsyncSession, node: UpstreamNode) -> Interface:
    import backend.services.awg as awg_svc

    awg1 = await get_awg1_or_raise(session)
    inherit_client_settings_from_interface(node, awg1)

    if node.provisioning_mode == ProvisioningMode.manual and node.private_key:
        awg1.private_key = node.private_key
        awg1.public_key = awg_svc.derive_public_key(node.private_key, protocol=InterfaceProtocol.awg)

    awg1.address = node.client_address or settings.awg1_address
    awg1.dns = node.client_dns
    awg1.endpoint = f"{node.host}:{node.awg_port}"
    awg1.preshared_key = node.preshared_key
    awg1.allowed_ips = node.client_allowed_ips or settings.awg1_allowed_ips
    awg1.persistent_keepalive = (
        node.client_keepalive
        if node.client_keepalive is not None
        else settings.awg1_persistent_keepalive
    )

    obf_changed = False
    for _key, attr in CLIENT_OBF_FIELD_MAP.items():
        iface_attr = attr.replace("client_", "")
        value = getattr(node, attr)
        if getattr(awg1, iface_attr) != value:
            setattr(awg1, iface_attr, value)
            obf_changed = True
    if obf_changed:
        awg1.obf_generated_at = datetime.now(timezone.utc)

    session.add(node)
    session.add(awg1)
    await session.flush()

    synthetic_peer = Peer(
        interface_id=awg1.id,
        name=node.name,
        public_key=node.public_key or "",
        preshared_key=node.preshared_key,
        allowed_ips=awg1.allowed_ips or settings.awg1_allowed_ips,
        persistent_keepalive=awg1.persistent_keepalive,
        enabled=True,
    )
    await awg_svc.apply_interface(awg1, [synthetic_peer])
    return awg1
