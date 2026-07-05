"""Hourly continuous-archive job: append the live HA window to the rolling record so the
signature extractors accumulate rain-free recessions and long-term continuity. Archiving
only -- the training run stays operator-invoked until storm auto-capture exists."""

from __future__ import annotations

import logging

from ..settings import ha_config_from_env
from . import archive
from .config import CalibrationConfig

logger = logging.getLogger("lake_rise.calibration")


def start_archive_scheduler(config: CalibrationConfig, art):
    """Start an hourly APScheduler job that appends the live HA window to the continuous
    record. Returns the scheduler, or None if archiving is disabled or HA isn't configured."""
    if not config.enabled:
        logger.info("calibration archiving disabled (CALIB_ENABLED not set); scheduler not started")
        return None
    ha = ha_config_from_env()
    if ha is None:
        logger.info("no HA credentials; calibration archive scheduler not started")
        return None

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from ..sources.live_ha import LiveHASource

    source = LiveHASource(art, ha)

    def _tick() -> None:
        try:
            rec = archive.append_samples(source.continuous_samples())
            logger.info("continuous archive appended; now %d samples", len(rec.samples))
        except Exception as exc:  # noqa: BLE001 -- a failed pull must never kill the schedule
            logger.warning("continuous archive append failed: %s", exc)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(_tick, "interval", minutes=60, id="lake_rise_archive",
                      next_run_time=None, coalesce=True, max_instances=1)
    scheduler.start()
    logger.info("calibration archive scheduler started (hourly)")
    return scheduler
