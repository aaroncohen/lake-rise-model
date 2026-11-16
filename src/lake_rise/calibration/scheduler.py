"""Hourly continuous-archive job: append the live HA window to the rolling record so the
signature extractors accumulate rain-free recessions and long-term continuity. Archiving
only -- the training run stays operator-invoked until storm auto-capture exists."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .. import gauge_minute
from ..settings import ha_config_from_env
from . import archive
from .config import CalibrationConfig

logger = logging.getLogger("lake_rise.calibration")

# The hourly tick re-merges this much of HA's statistics: enough to cover a missed tick or a
# short outage, and to pick up each hour's statistic once HA has compiled it.
STATISTICS_TICK_DAYS = 2
# Each hourly ensemble-forecast tick works newest-first for at most this long per source: new
# cycles land within the hour, and the history backfill (~1 min a cycle) finishes in about a day.
QPF_TICK_BUDGET_S = 20 * 60
# The hourly tick fetches minute history from just before the newest stored raw-depth row, so a
# missed tick or an outage loses nothing HA still holds (the archive keeps one row per time):
# 2 h when nothing is stored, never more than HA's ~10 days.
GAUGE_MINUTE_DEFAULT_HOURS = 2
GAUGE_MINUTE_OVERLAP_MINUTES = 5
GAUGE_MINUTE_MAX_DAYS = 10


def _pull(source, days: int | None) -> None:
    samples = source.continuous_samples(days=days)
    archive.append_samples(samples)
    logger.info("continuous archive appended; %d hours merged", len(samples))


def _statistics(source, days: int) -> None:
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


def archive_tick(config: CalibrationConfig, art, source_factory) -> None:
    """The hourly job: append the trailing HA window and HA's recent statistics to the continuous
    record, then the minute readings since the minute archive's newest to that archive.
    ``source_factory()`` returns the live source."""
    source = source_factory()
    try:
        _pull(source, None)                                 # hourly maintenance: trailing window
    except Exception as exc:  # noqa: BLE001 -- a failed pull must never kill the schedule
        logger.warning("continuous archive append failed: %s", exc)
    _statistics(source, STATISTICS_TICK_DAYS)
    try:
        now = datetime.now(timezone.utc)
        oldest = now - timedelta(days=GAUGE_MINUTE_MAX_DAYS)
        stored = gauge_minute.load(oldest, now).get(source.cfg.lake_raw_sensor)
        start = (max(oldest, stored[-1][0] - timedelta(minutes=GAUGE_MINUTE_OVERLAP_MINUTES))
                 if stored else now - timedelta(hours=GAUGE_MINUTE_DEFAULT_HOURS))
        gauge_minute.append(source.minute_history(gauge_minute.entities(source.cfg), start, now))
    except Exception as exc:  # noqa: BLE001 -- the minute archive must never stop the hourly one
        logger.warning("gauge minute archive append failed: %s", exc)


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

    def _startup_backfill() -> None:
        # Persist as much history as HA still retains, once, at startup. Runs off-thread so it
        # never blocks API startup; the merge is idempotent so overlapping with the hourly tick is
        # harmless. HA truncates the request to its own recorder retention.
        try:
            _pull(source, config.startup_backfill_days)
            logger.info("continuous archive startup backfill complete (up to %d days)",
                        config.startup_backfill_days)
        except Exception as exc:  # noqa: BLE001
            logger.warning("continuous archive startup backfill failed: %s", exc)
        # Raw history only reaches back ~10 days; HA's long-term statistics keep hourly rain and
        # weather permanently, so they repair/extend the archive over the whole backfill window.
        _statistics(source, config.startup_backfill_days)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(archive_tick, "interval", args=(config, art, lambda: source), minutes=60,
                      id="lake_rise_archive", coalesce=True, max_instances=1)
    scheduler.add_job(_startup_backfill, id="lake_rise_archive_backfill")   # once, ~immediately
    site = art.lake_evaporation_model
    if config.qpf_archive_enabled and site is not None:
        from .. import qpf_archive

        fetchers = qpf_archive.fetchers(site.latitude_deg, site.longitude_deg)

        def _qpf() -> None:
            for name, fetch in fetchers.items():
                try:
                    stats = qpf_archive.update(name, fetch, budget_s=QPF_TICK_BUDGET_S)
                    logger.info("qpf archive %s: %s", name, stats)
                except Exception as exc:  # noqa: BLE001 -- never kill the schedule
                    logger.warning("qpf archive %s update failed: %s", name, exc)

        scheduler.add_job(_qpf, "interval", minutes=60, id="lake_rise_qpf", coalesce=True,
                          max_instances=1,
                          next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2))
    scheduler.start()
    logger.info("calibration archive scheduler started (hourly; startup backfill queued)")
    return scheduler
