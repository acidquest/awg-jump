"""
APScheduler — фоновые задачи:
  - GeoIP обновление по cron
  - AWG health-check каждые 60с
  - Node health-check + failover каждые NODE_HEALTH_CHECK_INTERVAL с
"""
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.config import settings

logger = logging.getLogger(__name__)

# Счётчики неудач хранятся в node_deployer (единственный источник правды)
# Импортируем оттуда чтобы не дублировать состояние

scheduler = AsyncIOScheduler()


# ── GeoIP обновление ─────────────────────────────────────────────────────

async def _geoip_update_job() -> None:
    logger.info("[scheduler] Running scheduled GeoIP update")
    try:
        from backend.routers.geoip import run_geoip_update
        await run_geoip_update()
    except Exception as e:
        logger.error("[scheduler] GeoIP update failed: %s", e)


# ── AWG health-check ─────────────────────────────────────────────────────

async def _awg_health_check() -> None:
    """Проверяет что enabled интерфейсы подняты, перезапускает упавшие."""
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.interface import Interface
    import backend.services.awg as awg_svc

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Interface).where(Interface.enabled == True)  # noqa: E712
            )
            ifaces = result.scalars().all()

        for iface in ifaces:
            if not iface.private_key:
                continue
            if not awg_svc.is_running(iface.name):
                logger.warning(
                    "[scheduler] Interface %s is down, restarting...", iface.name
                )
                try:
                    async with AsyncSessionLocal() as session:
                        await awg_svc.load_interface(iface, session)
                    logger.info("[scheduler] Interface %s restarted", iface.name)
                except Exception as e:
                    logger.error(
                        "[scheduler] Failed to restart %s: %s", iface.name, e
                    )
    except Exception as e:
        logger.error("[scheduler] AWG health-check error: %s", e)


# ── Peer stats sync ──────────────────────────────────────────────────────

async def _sync_peer_stats() -> None:
    """
    Синхронизирует last_handshake, rx_bytes, tx_bytes из awg show all dump → БД.
    Запускается каждые 30 секунд.
    """
    import backend.services.awg as awg_svc
    from backend.database import AsyncSessionLocal
    from backend.models.peer import Peer
    from sqlalchemy import select

    try:
        status = awg_svc.get_status()
        if not status:
            return

        async with AsyncSessionLocal() as session:
            for iface_name, iface_data in status.items():
                peers_data = iface_data.get("peers", {})
                if not peers_data:
                    continue
                result = await session.execute(
                    select(Peer).where(Peer.enabled == True)  # noqa: E712
                )
                db_peers = result.scalars().all()
                updated = False
                for peer in db_peers:
                    peer_stat = peers_data.get(peer.public_key)
                    if peer_stat is None:
                        continue
                    hs = peer_stat.get("latest_handshake", 0)
                    peer.last_handshake = (
                        datetime.fromtimestamp(hs, tz=timezone.utc) if hs else None
                    )
                    peer.rx_bytes = peer_stat.get("rx_bytes", 0)
                    peer.tx_bytes = peer_stat.get("tx_bytes", 0)
                    updated = True
                if updated:
                    await session.commit()
    except Exception as e:
        logger.error("[scheduler] Peer stats sync error: %s", e)


# ── Node health-check + failover ─────────────────────────────────────────

async def _node_health_check() -> None:
    """
    Проверяет доступность нод со статусом online/degraded.
    При превышении NODE_FAILOVER_THRESHOLD неудач → failover.
    """
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.routing_settings import RoutingSettings
    from backend.models.upstream_node import NodeStatus, UpstreamNode
    from backend.services.node_deployer import deployer

    from backend.services.node_deployer import _health_fail_counts

    try:
        async with AsyncSessionLocal() as session:
            settings_row = await session.get(RoutingSettings, 1)
            failover_enabled = True if settings_row is None else settings_row.failover_enabled
            result = await session.execute(
                select(UpstreamNode).where(
                    UpstreamNode.status.in_([
                        NodeStatus.online,
                        NodeStatus.degraded,
                    ]),
                    UpstreamNode.public_key.isnot(None),
                )
            )
            nodes = result.scalars().all()
            node_list = [(n.id, n.name) for n in nodes]

        for node_id, node_name in node_list:
            try:
                health = await deployer.check_health(node_id)
                if health["alive"]:
                    _health_fail_counts[node_id] = 0
                else:
                    if not failover_enabled:
                        _health_fail_counts[node_id] = 0
                        continue
                    _health_fail_counts[node_id] = _health_fail_counts.get(node_id, 0) + 1
                    count = _health_fail_counts[node_id]
                    logger.warning(
                        "[node_health] Node %d (%s) unhealthy, fail count=%d/%d",
                        node_id, node_name, count, settings.node_failover_threshold,
                    )
                    if count >= settings.node_failover_threshold:
                        # Проверить — активная ли нода (failover только для активной)
                        async with AsyncSessionLocal() as session:
                            node = await session.get(UpstreamNode, node_id)
                            is_active = node.is_active if node else False

                        if is_active:
                            logger.warning(
                                "[node_health] Threshold reached for active node %d, initiating failover",
                                node_id,
                            )
                            switched = await deployer.failover(node_id)
                            if switched:
                                _health_fail_counts[node_id] = 0
            except Exception as exc:
                logger.error("[node_health] Error checking node %d: %s", node_id, exc)

    except Exception as exc:
        logger.error("[node_health] Health check task error: %s", exc)


# ── System metrics sampling ──────────────────────────────────────────────

async def _system_metrics_sample() -> None:
    from backend.database import AsyncSessionLocal
    from backend.services.system_metrics import collect_system_metrics

    try:
        async with AsyncSessionLocal() as session:
            await collect_system_metrics(session)
    except Exception as exc:
        logger.error("[scheduler] System metrics sampling error: %s", exc)


# ── Инициализация ─────────────────────────────────────────────────────────

def setup_scheduler() -> None:
    """Регистрирует все задачи и запускает планировщик."""
    # GeoIP обновление по cron (из настроек, например "0 4 * * *")
    try:
        geoip_trigger = CronTrigger.from_crontab(settings.geoip_update_cron)
    except Exception:
        logger.warning("Invalid GEOIP_UPDATE_CRON '%s', using daily 04:00", settings.geoip_update_cron)
        geoip_trigger = CronTrigger(hour=4, minute=0)

    scheduler.add_job(
        _geoip_update_job,
        trigger=geoip_trigger,
        id="geoip_update",
        replace_existing=True,
        name="GeoIP scheduled update",
    )

    # AWG health-check каждые 60 секунд
    scheduler.add_job(
        _awg_health_check,
        trigger=IntervalTrigger(seconds=60),
        id="awg_health_check",
        replace_existing=True,
        name="AWG interface health check",
    )

    # Peer stats sync каждые 30 секунд
    scheduler.add_job(
        _sync_peer_stats,
        trigger=IntervalTrigger(seconds=30),
        id="peer_stats_sync",
        replace_existing=True,
        name="Peer stats sync (handshake/rx/tx)",
    )

    # Node health-check
    scheduler.add_job(
        _node_health_check,
        trigger=IntervalTrigger(seconds=settings.node_health_check_interval),
        id="node_health_check",
        replace_existing=True,
        name="Upstream node health check",
    )

    scheduler.add_job(
        _system_metrics_sample,
        trigger=IntervalTrigger(minutes=1),
        id="system_metrics_sample",
        replace_existing=True,
        name="System metrics sampling",
    )

    scheduler.start()
    logger.info(
        "[scheduler] Started: geoip=%s, awg_check=60s, node_check=%ds, metrics=60s",
        settings.geoip_update_cron,
        settings.node_health_check_interval,
    )
