"""Hourly continuous-archive job: append the live HA window to the rolling record so the
signature extractors accumulate rain-free recessions and long-term continuity. Archiving
only -- the training run stays operator-invoked until storm auto-capture exists."""

from __future__ import annotations

import logging

from ..settings import ha_config_from_env
from . import archive
from .config import CalibrationConfig

logger = logging.getLogger("lake_rise.calibration")

# The hourly tick re-merges this much of HA's statistics: enough to cover a missed tick or a
# short outage, and to pick up each hour's statistic once HA has compiled it.
STATISTICS_TICK_DAYS = 2


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

    def _pull(days: int | None) -> None:
        samples = source.continuous_samples(days=days)
        archive.append_samples(samples)
        logger.info("continuous archive appended; %d hours merged", len(samples))

    def _statistics(days: int) -> None:
        # HA's long-term statistics: rain (repairs/extends the record, fills dry outage gaps) and
        # the weather that drives the lake-evaporation estimate. Separate from the raw pull so a
        # statistics failure never blocks level/rain archiving.
        for label, pull in (("rain", source.statistics_rain_samples),
                            ("weather", source.statistics_met_samples)):
            try:
                samples = pull(days=days)
                archive.append_samples(samples)
                logger.info("continuous archive %s merged from HA statistics; %d hours", label,
                            len(samples))
            except Exception as exc:  # noqa: BLE001
                logger.warning("continuous archive %s statistics merge failed: %s", label, exc)

    def _tick() -> None:
        try:
            _pull(None)                                     # hourly maintenance: trailing window
        except Exception as exc:  # noqa: BLE001 -- a failed pull must never kill the schedule
            logger.warning("continuous archive append failed: %s", exc)
        _statistics(STATISTICS_TICK_DAYS)

    def _startup_backfill() -> None:
        # Persist as much history as HA still retains, once, at startup. Runs off-thread so it
        # never blocks API startup; the merge is idempotent so overlapping with the hourly tick is
        # harmless. HA truncates the request to its own recorder retention.
        try:
            _pull(config.startup_backfill_days)
            logger.info("continuous archive startup backfill complete (up to %d days)",
                        config.startup_backfill_days)
        except Exception as exc:  # noqa: BLE001
            logger.warning("continuous archive startup backfill failed: %s", exc)
        # Raw history only reaches back ~10 days; HA's long-term statistics keep hourly rain and
        # weather permanently, so they repair/extend the archive over the whole backfill window.
        _statistics(config.startup_backfill_days)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(_tick, "interval", minutes=60, id="lake_rise_archive",
                      coalesce=True, max_instances=1)
    scheduler.add_job(_startup_backfill, id="lake_rise_archive_backfill")   # once, ~immediately
    scheduler.start()
    logger.info("calibration archive scheduler started (hourly; startup backfill queued)")
    return scheduler
