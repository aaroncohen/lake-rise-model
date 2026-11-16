"""Regression guard for the paused-job bug: an interval job added with
``next_run_time=None`` is *paused* in APScheduler 3.x and never fires. Both in-process
schedulers must register a job with a real ``next_run_time`` after ``start``.
"""

import asyncio

from lake_rise.alerting.scheduler import start_scheduler
from lake_rise.artifact import load_artifact
from lake_rise.calibration.config import calibration_config_from_env
from lake_rise.calibration.scheduler import start_archive_scheduler


def _with_loop(fn):
    """Run ``fn`` inside a running event loop (AsyncIOScheduler.start needs one)."""

    async def _boot():
        return fn()

    return asyncio.run(_boot())


def test_alert_scheduler_job_is_scheduled_not_paused(make_alert_config, monkeypatch):
    monkeypatch.setenv("HA_URL", "http://ha.local")
    monkeypatch.setenv("HA_TOKEN", "x")
    cfg = make_alert_config(channels=("console",))

    def _run():
        sched = start_scheduler(cfg)
        assert sched is not None, "scheduler should start with enabled config + HA creds"
        try:
            job = sched.get_job("lake_rise_alert")
            assert job is not None
            # None here means the job is paused -- the S1 bug.
            assert job.next_run_time is not None
            observed = sched.get_job("lake_rise_observed_eap")
            assert observed is not None and observed.next_run_time is not None
            assert observed.trigger.interval.total_seconds() == 5 * 60
        finally:
            sched.shutdown(wait=False)

    _with_loop(_run)


def test_archive_scheduler_job_is_scheduled_not_paused(monkeypatch):
    monkeypatch.setenv("HA_URL", "http://ha.local")
    monkeypatch.setenv("HA_TOKEN", "x")
    monkeypatch.setenv("CALIB_ENABLED", "1")
    art = load_artifact()

    def _run():
        sched = start_archive_scheduler(calibration_config_from_env(), art)
        assert sched is not None, "archive scheduler should start with CALIB_ENABLED + HA creds"
        try:
            job = sched.get_job("lake_rise_archive")
            assert job is not None
            assert job.next_run_time is not None
            # A one-shot startup backfill is queued to persist as much history as HA retains.
            assert sched.get_job("lake_rise_archive_backfill") is not None
        finally:
            sched.shutdown(wait=False)

    _with_loop(_run)


def test_alert_jobs_first_run_soon_after_startup_not_a_full_interval_later(make_alert_config,
                                                                          monkeypatch):
    """A restart must not leave the lake unwatched for an hour: the forecast and observed jobs
    first run about a minute after startup."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("HA_URL", "http://ha.local")
    monkeypatch.setenv("HA_TOKEN", "x")
    cfg = make_alert_config(channels=("console",))

    def _run():
        sched = start_scheduler(cfg)
        try:
            soon = datetime.now(timezone.utc) + timedelta(minutes=2)
            for job_id in ("lake_rise_alert", "lake_rise_observed_eap"):
                assert sched.get_job(job_id).next_run_time <= soon
        finally:
            sched.shutdown(wait=False)

    _with_loop(_run)


def test_archive_tick_still_archives_the_hour_when_the_minute_append_fails(monkeypatch):
    from datetime import datetime, timezone

    import httpx

    from lake_rise.calibration import archive
    from lake_rise.calibration.scheduler import archive_tick
    from lake_rise.sources.live_ha import HAConfig

    hour = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)

    class _Source:
        cfg = HAConfig(base_url="http://ha.local", token="x")

        def continuous_samples(self, days=None):
            return [archive.HourSample(hour=hour.isoformat(), rain_in=0.0, elev_ft=339.5)]

        def statistics_rain_samples(self, days):
            return []

        def statistics_met_samples(self, days):
            return []

        def minute_history(self, entity_ids, start, end):
            raise httpx.ConnectError("HA unreachable")

    monkeypatch.setenv("CALIB_ENABLED", "1")
    archive_tick(calibration_config_from_env(), load_artifact(), _Source)
    assert [s.elev_ft for s in archive.load_window(hour, hour).samples] == [339.5]
