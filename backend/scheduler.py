"""
APScheduler — фоновые задачи:
  - GeoIP обновление по cron
  - AWG health-check каждые 60с
  - Node health-check + failover каждые NODE_HEALTH_CHECK_INTERVAL с
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.config import settings

logger = logging.getLogger(__name__)

# Счётчики неудач для failover (node_id → count)
_health_fail_counts: dict[int, int] = {}

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


# ── Node health-check + failover ─────────────────────────────────────────

async def _node_health_check() -> None:
    """
    Проверяет доступность всех нод со статусом online/degraded.
    При превышении NODE_FAILOVER_THRESHOLD неудач → failover.
    """
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.upstream_node import NodeStatus, UpstreamNode
    from backend.services.node_deployer import deployer

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(UpstreamNode).where(
                    UpstreamNode.status.in_([NodeStatus.online, NodeStatus.degraded])
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

    # Node health-check
    scheduler.add_job(
        _node_health_check,
        trigger=IntervalTrigger(seconds=settings.node_health_check_interval),
        id="node_health_check",
        replace_existing=True,
        name="Upstream node health check",
    )

    scheduler.start()
    logger.info(
        "[scheduler] Started: geoip=%s, awg_check=60s, node_check=%ds",
        settings.geoip_update_cron,
        settings.node_health_check_interval,
    )
