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
