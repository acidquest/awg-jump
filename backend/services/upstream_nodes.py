from __future__ import annotations

import ipaddress
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.interface import Interface, InterfaceProtocol
from backend.models.peer import Peer
from backend.models.upstream_node import ProvisioningMode, UpstreamNode

DEFAULT_CLIENT_ALLOWED_IPS = "0.0.0.0/0"
DEFAULT_CLIENT_KEEPALIVE = 25

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


def _interface_input(value: str) -> ipaddress.IPv4Interface:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Interface address is required")
    if "/" not in normalized:
        normalized = f"{normalized}/24"
    iface = ipaddress.ip_interface(normalized)
    if iface.version != 4:
        raise ValueError("Only IPv4 upstream tunnel addresses are supported")
    return iface


def derive_node_tunnel_addresses(interface_address: str) -> tuple[str, str, str]:
    """
    Возвращает (client_address, awg_address, tunnel_network) для upstream-ноды.

    Пользователь вводит адрес локального jump-интерфейса. Если префикс не указан,
    считаем сеть /24. На удаленной ноде берем первый свободный usable IP этой сети.
    """
    iface = _interface_input(interface_address)
    if iface.network.prefixlen == 32:
        iface = ipaddress.ip_interface(f"{iface.ip}/24")
    network = iface.network
    if network.prefixlen > 30:
        raise ValueError("Interface address must describe a subnet with at least two usable hosts")

    first_host = int(network.network_address) + 1
    last_host = int(network.broadcast_address) - 1
    remote_ip = None
    for value in range(first_host, last_host + 1):
        candidate = ipaddress.IPv4Address(value)
        if candidate != iface.ip:
            remote_ip = candidate
            break
    if remote_ip is None:
        raise ValueError("Cannot derive upstream node address from this subnet")

    return f"{iface.ip}/32", f"{remote_ip}/32", str(network)


async def assign_tunnel_from_interface_address(
    session: AsyncSession,
    node: UpstreamNode,
    interface_address: str,
) -> None:
    client_address, awg_address, tunnel_network = derive_node_tunnel_addresses(interface_address)
    new_network = ipaddress.ip_network(tunnel_network)

    result = await session.execute(select(UpstreamNode).where(UpstreamNode.id != node.id))
    for other in result.scalars().all():
        if other.tunnel_network:
            try:
                other_network = ipaddress.ip_network(other.tunnel_network, strict=False)
            except ValueError:
                other_network = None
            if other_network and new_network.overlaps(other_network):
                raise ValueError(f"Tunnel network overlaps with node '{other.name}' ({other.tunnel_network})")
        if other.client_address == client_address:
            raise ValueError(f"Client address is already used by node '{other.name}'")
        if other.awg_address == awg_address:
            raise ValueError(f"Upstream AWG address is already used by node '{other.name}'")

    node.client_address = client_address
    node.awg_address = awg_address
    node.tunnel_network = tunnel_network


def node_interface_address_for_remote(node: UpstreamNode) -> str:
    if not node.awg_address:
        raise RuntimeError("Node AWG address is not configured")
    prefix = 24
    if node.tunnel_network:
        try:
            prefix = ipaddress.ip_network(node.tunnel_network, strict=False).prefixlen
        except ValueError:
            prefix = 24
    return f"{ipaddress.ip_interface(node.awg_address).ip}/{prefix}"


async def get_interface_or_raise(session: AsyncSession, interface_name: str) -> Interface:
    result = await session.execute(select(Interface).where(Interface.name == interface_name))
    iface = result.scalar_one_or_none()
    if iface is None:
        raise RuntimeError(f"{interface_name} interface not found")
    return iface


async def get_awg1_or_raise(session: AsyncSession) -> Interface:
    return await get_interface_or_raise(session, "awg1")


async def get_awg2_or_raise(session: AsyncSession) -> Interface:
    return await get_interface_or_raise(session, "awg2")


def inherit_client_settings_from_interface(node: UpstreamNode, iface: Interface) -> None:
    node.client_address = node.client_address or iface.address or None
    node.client_dns = node.client_dns if node.client_dns is not None else iface.dns
    node.client_allowed_ips = node.client_allowed_ips or iface.allowed_ips or DEFAULT_CLIENT_ALLOWED_IPS
    node.client_keepalive = (
        node.client_keepalive
        if node.client_keepalive is not None
        else iface.persistent_keepalive
    )
    for _key, attr in CLIENT_OBF_FIELD_MAP.items():
        if getattr(node, attr) is None:
            source_attr = attr.replace("client_", "")
            setattr(node, attr, getattr(iface, source_attr))


def assign_client_settings_from_parsed(node: UpstreamNode, parsed) -> None:
    node.client_address = parsed.tunnel_address
    node.client_dns = ",".join(parsed.dns_servers) if parsed.dns_servers else None
    node.client_allowed_ips = ",".join(parsed.allowed_ips) if parsed.allowed_ips else None
    node.client_keepalive = parsed.persistent_keepalive
    for key, attr in CLIENT_OBF_FIELD_MAP.items():
        setattr(node, attr, parsed.obfuscation.get(key))


async def apply_node_to_interface(session: AsyncSession, node: UpstreamNode, interface_name: str) -> Interface:
    import backend.services.awg as awg_svc

    iface = await get_interface_or_raise(session, interface_name)
    inherit_client_settings_from_interface(node, iface)
    if not node.client_address:
        raise RuntimeError("Node client interface address is not configured")
    if not node.public_key:
        raise RuntimeError("Node public key is not configured")

    if node.provisioning_mode == ProvisioningMode.manual and node.private_key:
        iface.private_key = node.private_key
        iface.public_key = awg_svc.derive_public_key(node.private_key, protocol=InterfaceProtocol.awg)
    elif interface_name == "awg2":
        awg1 = await get_awg1_or_raise(session)
        iface.private_key = awg1.private_key
        iface.public_key = awg1.public_key

    iface.address = node.client_address
    iface.dns = node.client_dns
    iface.endpoint = f"{node.host}:{node.awg_port}"
    iface.preshared_key = node.preshared_key
    iface.allowed_ips = node.client_allowed_ips or DEFAULT_CLIENT_ALLOWED_IPS
    iface.persistent_keepalive = (
        node.client_keepalive
        if node.client_keepalive is not None
        else DEFAULT_CLIENT_KEEPALIVE
    )

    obf_changed = False
    for _key, attr in CLIENT_OBF_FIELD_MAP.items():
        iface_attr = attr.replace("client_", "")
        value = getattr(node, attr)
        if getattr(iface, iface_attr) != value:
            setattr(iface, iface_attr, value)
            obf_changed = True
    if obf_changed:
        iface.obf_generated_at = datetime.now(timezone.utc)

    session.add(node)
    session.add(iface)
    await session.flush()

    synthetic_peer = Peer(
        interface_id=iface.id,
        name=node.name,
        public_key=node.public_key,
        preshared_key=node.preshared_key,
        allowed_ips=iface.allowed_ips or DEFAULT_CLIENT_ALLOWED_IPS,
        persistent_keepalive=iface.persistent_keepalive,
        enabled=True,
    )
    await awg_svc.apply_interface(iface, [synthetic_peer])
    return iface


async def apply_node_to_awg1(session: AsyncSession, node: UpstreamNode) -> Interface:
    return await apply_node_to_interface(session, node, "awg1")


async def apply_node_to_awg2(session: AsyncSession, node: UpstreamNode) -> Interface:
    return await apply_node_to_interface(session, node, "awg2")
