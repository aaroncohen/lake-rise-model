"""In-process hourly scheduler (APScheduler) that drives the alert evaluation.

Wired into the FastAPI lifespan. A failing run is caught and logged so a transient
HA outage never kills the schedule.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..settings import ha_config_from_env
from .config import AlertConfig
from .service import run_once

log = logging.getLogger("lake_rise.alerting")


def _tick(config: AlertConfig) -> None:
    try:
        run_once(config)
    except Exception:  # noqa: BLE001 - keep the scheduler alive across transient failures
        log.exception("scheduled alert run failed")


def start_scheduler(config: AlertConfig) -> AsyncIOScheduler | None:
    """Start the hourly job if alerting is enabled and prerequisites are present.

    Returns the scheduler (so the caller can shut it down) or None if not started.
    """
    if not config.enabled:
        log.info("alerting disabled (ALERT_ENABLED not set); scheduler not started")
        return None
    if ha_config_from_env() is None:
        log.warning("ALERT_ENABLED set but no HA source (HA_URL/HA_TOKEN); scheduler not started")
        return None
    if not config.channels:
        log.warning("ALERT_ENABLED set but ALERT_CHANNELS is empty; scheduler not started")
        return None

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _tick, "interval", minutes=config.interval_minutes, args=[config],
        id="lake_rise_alert", next_run_time=None, coalesce=True, max_instances=1,
    )
    scheduler.start()
    log.info(
        "alert scheduler started: every %d min, channels=%s, levels=%d",
        config.interval_minutes, ",".join(config.channels), len(config.levels),
    )
    return scheduler
