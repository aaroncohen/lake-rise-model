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


def _tick_source(minute_rows=None):
    """A fake live source: one archived hour, no statistics, and ``minute_rows(start, end)`` as
    its minute history (HA unreachable when None). Records the windows it was asked for."""
    from datetime import datetime, timezone

    import httpx

    from lake_rise.calibration import archive
    from lake_rise.sources.live_ha import HAConfig

    class _Source:
        cfg = HAConfig(base_url="http://ha.local", token="x")
        hour = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        asked: list = []

        def continuous_samples(self, days=None):
            return [archive.HourSample(hour=self.hour.isoformat(), rain_in=0.0, elev_ft=339.5)]

        def statistics_rain_samples(self, days):
            return []

        def statistics_met_samples(self, days):
            return []

        def minute_history(self, entity_ids, start, end):
            self.asked.append((start, end))
            if minute_rows is None:
                raise httpx.ConnectError("HA unreachable")
            return {e: minute_rows(start, end) for e in entity_ids}

    return _Source


def _tick(monkeypatch, source):
    from lake_rise.calibration.scheduler import archive_tick

    monkeypatch.setenv("CALIB_ENABLED", "1")
    archive_tick(calibration_config_from_env(), load_artifact(), source)


def test_archive_tick_still_archives_the_hour_when_the_minute_append_fails(monkeypatch):
    from lake_rise.calibration import archive

    source = _tick_source()
    _tick(monkeypatch, source)
    hour = source.hour
    assert [s.elev_ft for s in archive.load_window(hour, hour).samples] == [339.5]


def test_archive_tick_appends_the_minute_readings_of_every_entity(monkeypatch):
    from datetime import timedelta

    from lake_rise import gauge_minute

    source = _tick_source(lambda start, end: [(end - timedelta(minutes=1), "1.23")])
    _tick(monkeypatch, source)
    _, end = source.asked[0]
    stored = gauge_minute.load(end - timedelta(hours=1), end)
    assert set(stored) == set(gauge_minute.entities(source.cfg))
    assert all(rows == [(end - timedelta(minutes=1), "1.23")] for rows in stored.values())


def test_archive_tick_fetches_minutes_from_the_newest_stored_reading(monkeypatch):
    """After a 5 h outage the tick asks HA for those 5 h (plus a small overlap), not just 2 h."""
    from datetime import datetime, timedelta, timezone

    from lake_rise import gauge_minute

    source = _tick_source(lambda start, end: [])
    five_h_ago = datetime.now(timezone.utc) - timedelta(hours=5)
    gauge_minute.append({source.cfg.lake_raw_sensor: [(five_h_ago, "1.20")]})
    _tick(monkeypatch, source)
    start, end = source.asked[0]
    assert start == five_h_ago - timedelta(minutes=5)
    assert timedelta(hours=5) < end - start < timedelta(hours=5, minutes=6)
