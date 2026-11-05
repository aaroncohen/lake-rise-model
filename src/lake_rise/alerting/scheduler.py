"""In-process hourly scheduler (APScheduler) that drives the alert evaluation.

Wired into the FastAPI lifespan. A failing run is caught and logged so a transient
HA outage never kills the schedule.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..settings import ha_config_from_env
from .config import AlertConfig
from .drill import run_drill, should_run_drill
from .service import run_observed_once, run_once
from .state import load_state

log = logging.getLogger("lake_rise.alerting")

# Delay before the first forecast / observed run after startup (lets the app finish starting).
FIRST_RUN_DELAY_SECONDS = 60
# A run delayed past its slot (a busy event loop, a long previous run) still runs if it's within
# this window, instead of APScheduler's default 1 s grace silently skipping it.
MISFIRE_GRACE_SECONDS = 240


def _tick(config: AlertConfig) -> None:
    try:
        run_once(config)
    except Exception:  # noqa: BLE001 - keep the scheduler alive across transient failures
        log.exception("scheduled alert run failed")

    if config.drill_enabled:
        try:
            state = load_state(config.state_path)
            if should_run_drill(state, config):
                run_drill(config)
        except Exception:  # noqa: BLE001
            log.exception("monthly drill failed")


def _observed_tick(config: AlertConfig) -> None:
    try:
        run_observed_once(config)
    except Exception:  # noqa: BLE001 - keep the scheduler alive across transient failures
        log.exception("scheduled observed EAP check failed")


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

    # First runs shortly after startup, not one full interval later: otherwise every restart
    # leaves the lake unwatched for an hour. Fire-on-crossing state is persisted, so an early
    # run can't re-send a notice that was already delivered.
    first = datetime.now(timezone.utc) + timedelta(seconds=FIRST_RUN_DELAY_SECONDS)
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _tick, "interval", minutes=config.interval_minutes, args=[config],
        id="lake_rise_alert", coalesce=True, max_instances=1, next_run_time=first,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
    )
    scheduler.add_job(
        _observed_tick, "interval", minutes=config.observed_interval_minutes, args=[config],
        id="lake_rise_observed_eap", coalesce=True, max_instances=1, next_run_time=first,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
    )
    scheduler.start()
    log.info(
        "alert scheduler started: forecast every %d min, observed EAP every %d min,"
        " channels=%s, levels=%d",
        config.interval_minutes, config.observed_interval_minutes,
        ",".join(config.channels), len(config.levels),
    )
    return scheduler
