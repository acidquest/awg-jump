"""
System router — агрегированный статус, логи, restart-routing.
"""
import asyncio
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.interface import Interface
from backend.models.upstream_node import UpstreamNode
from backend.models.upstream_node import NodeStatus
from backend.models.geoip import GeoipSource
from backend.routers.auth import get_current_user
from backend.config import settings
import backend.services.awg as awg_svc
import backend.services.ipset_manager as ipset_mgr
import backend.services.routing as routing_svc
import backend.services.geoip_fetcher as geoip_fetcher

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])

# Время старта процесса
_START_TIME = time.time()

# Лог-файлы (supervisor пишет сюда)
_LOG_FILES = {
    "uvicorn": "/var/log/supervisor/uvicorn.log",
    "supervisor": "/var/log/supervisor/supervisord.log",
}


@router.get("/status")
async def get_status(
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    """Агрегированный статус всей системы."""
    uptime_seconds = int(time.time() - _START_TIME)

    # AWG интерфейсы
    awg_status = awg_svc.get_status()
    result = await session.execute(select(Interface).order_by(Interface.id))
    ifaces = result.scalars().all()
    interfaces_out = []
    for iface in ifaces:
        live = awg_status.get(iface.name, {})
        interfaces_out.append({
            "name": iface.name,
            "mode": iface.mode.value if hasattr(iface.mode, "value") else iface.mode,
            "address": iface.address,
            "enabled": iface.enabled,
            "running": awg_svc.is_running(iface.name),
            "public_key": iface.public_key or "",
            "peers_count": len(live.get("peers", {})),
        })

    # GeoIP
    result_geoip = await session.execute(
        select(GeoipSource).where(GeoipSource.enabled == True)  # noqa: E712
    )
    sources = result_geoip.scalars().all()
    geoip_out = []
    for src in sources:
        geoip_out.append({
            "country_code": src.country_code,
            "display_name": src.display_name,
            "ipset_name": geoip_fetcher.LOCAL_GEOIP_IPSET_NAME,
            "prefix_count": src.prefix_count or 0,
            "last_updated": src.last_updated.isoformat() if src.last_updated else None,
            "cache_fresh": geoip_fetcher._is_cache_fresh(src.country_code),
        })

    # ipset
    try:
        ipset_list = ipset_mgr.list_sets()
    except Exception:
        ipset_list = []

    # Routing
    try:
        routing_status = routing_svc.get_status()
    except Exception as e:
        routing_status = {"error": str(e)}

    # Активная upstream нода
    result_node = await session.execute(
        select(UpstreamNode).where(UpstreamNode.is_active == True)  # noqa: E712
    )
    active_node = result_node.scalar_one_or_none()
    active_node_out = None
    if active_node:
        active_node_out = {
            "id": active_node.id,
            "name": active_node.name,
            "host": active_node.host,
            "external_ip": active_node.host,
            "status": active_node.status.value
            if hasattr(active_node.status, "value")
            else active_node.status,
            "latency_ms": active_node.latency_ms,
            "last_seen": active_node.last_seen.isoformat()
            if active_node.last_seen
            else None,
        }

    return {
        "uptime_seconds": uptime_seconds,
        "interfaces": interfaces_out,
        "geoip": geoip_out,
        "ipsets": ipset_list,
        "routing": routing_status,
        "active_node": active_node_out,
        "local_external_ip": settings.server_host or None,
    }


@router.get("/logs")
async def get_logs(
    service: str = Query("uvicorn", description="uvicorn|supervisor"),
    lines: int = Query(100, ge=1, le=5000),
    _user: str = Depends(get_current_user),
) -> dict:
    """Последние N строк из лог-файла сервиса."""
    log_path = _LOG_FILES.get(service)
    if log_path is None:
        return {"service": service, "lines": [], "error": "Unknown service"}

    if not os.path.exists(log_path):
        return {"service": service, "lines": [], "error": "Log file not found"}

    try:
        # Читаем tail через asyncio subprocess
        proc = await asyncio.create_subprocess_exec(
            "tail", "-n", str(lines), log_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        log_lines = stdout.decode(errors="replace").splitlines()
        return {"service": service, "lines": log_lines, "count": len(log_lines)}
    except Exception as e:
        return {"service": service, "lines": [], "error": str(e)}


@router.post("/restart-routing")
async def restart_routing(
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    """Сбросить и переприменить policy routing + iptables правила."""
    errors = []
    try:
        routing_svc.teardown()
    except Exception as e:
        errors.append(f"teardown: {e}")

    try:
        active_node = await session.scalar(
            select(UpstreamNode).where(
                UpstreamNode.is_active == True,  # noqa: E712
                UpstreamNode.status == NodeStatus.online,
            )
        )
        routing_svc.setup_policy_routing()
        routing_svc.update_vpn_route("awg1" if active_node else None)
        routing_svc.update_upstream_host_route(
            active_node.awg_address if active_node and active_node.awg_address else None
        )
        routing_svc.setup_iptables()
    except Exception as e:
        errors.append(f"setup: {e}")

    status = routing_svc.get_status()
    return {"status": "restarted" if not errors else "partial", "errors": errors, **status}
