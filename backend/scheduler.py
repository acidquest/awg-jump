"""
APScheduler — фоновые задачи:
  - GeoIP обновление по cron
  - AWG health-check каждые 60с
  - Node health-check каждые NODE_HEALTH_CHECK_INTERVAL с (заглушка до этапа 6)
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.config import settings

logger = logging.getLogger(__name__)

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


# ── Node health-check (заглушка для этапа 6) ─────────────────────────────

async def _node_health_check() -> None:
    """
    Проверка доступности upstream нод и failover.
    Полная реализация — в этапе 6 (node_deployer.py).
    """
    pass


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
