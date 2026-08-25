"""Monthly pipeline-check email: fires once on/after the configured day-of-month AND
hour-of-day, local to the alert timezone -- not raw UTC (regression: UTC's day rollover
lands 7-8h before Pacific midnight, which used to send the notice the evening *before*
the 1st, misattributed to the wrong month). Must also survive an app restart without
re-firing within the same local month.

`service.run_once` always does a fresh `load_state` from disk and never keeps in-memory
state between calls, so calling it repeatedly (as these tests do) is exactly equivalent to
restarting the process between ticks -- there is no separate "restart" code path to test.
"""

from dataclasses import replace
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lake_rise.alerting import service
from lake_rise.alerting.config import Recipients
from lake_rise.alerting.state import load_state

from test_alert_state import _decision

_PACIFIC = ZoneInfo("America/Los_Angeles")


def _pacific(y, m, d, h=0, minute=0):
    """A UTC instant corresponding to this Pacific wall-clock time (DST-aware)."""
    return datetime(y, m, d, h, minute, tzinfo=_PACIFIC).astimezone(timezone.utc)


def _steady_state(generated_at):
    """No ladder activity and no rain -- isolates the monthly-test track."""
    return replace(_decision(0, None), generated_at=generated_at)


def _cfg(make_alert_config, tmp_path, *, dom=1, hour=7, audience="ops"):
    return make_alert_config(
        audiences={"ops": Recipients(emails=tuple(f"person{i}@example.org" for i in range(8)))},
        monthly_test_enabled=True, monthly_test_dom=dom, monthly_test_hour=hour,
        monthly_test_audience=audience,
        state_path=tmp_path / "alert_state.json", channels=("email",),
    )


class _RecordingNotifier:
    name = "rec"

    def __init__(self):
        self.sent = []

    def send(self, alert, recipients):
        self.sent.append((alert, recipients))


def _run(cfg, bundle, art, at):
    import lake_rise.alerting.service as svc
    notifier = _RecordingNotifier()
    orig = svc.evaluate
    svc.evaluate = lambda *a, **k: _steady_state(at)
    try:
        res = service.run_once(cfg, bundle=bundle, art=art, notifiers=[notifier])
    finally:
        svc.evaluate = orig
    return res, notifier


def test_gate_uses_pacific_local_time_not_utc(make_alert_config, make_bundle, art, tmp_path):
    """The regression this exists to catch: at this exact UTC instant it's still 4:30pm on
    Jan 31 in Pacific time. The old raw-UTC check would have read it as Feb 1 and fired
    "February's" test a day early. The fix must fire it as JANUARY's test instead (and
    still gate correctly on hour-of-day, since 4:30pm is well past the 7am send hour)."""
    cfg = _cfg(make_alert_config, tmp_path)
    bundle = make_bundle()

    utc_instant = datetime(2026, 2, 1, 0, 30, tzinfo=timezone.utc)  # Jan 31, 4:30pm PST
    res, notifier = _run(cfg, bundle, art, utc_instant)

    assert [a.kind for a in res.actions] == ["MONTHLY_TEST"]
    assert len(notifier.sent) == 1
    assert load_state(cfg.state_path).last_monthly_test_ym == "2026-01"


def test_waits_for_the_local_send_hour_on_the_send_day(make_alert_config, make_bundle, art, tmp_path):
    cfg = _cfg(make_alert_config, tmp_path, dom=1, hour=7)
    bundle = make_bundle()

    before, notifier1 = _run(cfg, bundle, art, _pacific(2026, 3, 1, 6, 30))
    assert before.actions == [] and notifier1.sent == []

    after, notifier2 = _run(cfg, bundle, art, _pacific(2026, 3, 1, 7, 5))
    assert [a.kind for a in after.actions] == ["MONTHLY_TEST"]
    assert len(notifier2.sent) == 1


def test_waits_for_the_local_send_day_independent_of_hour(make_alert_config, make_bundle, art, tmp_path):
    cfg = _cfg(make_alert_config, tmp_path, dom=5, hour=7)
    bundle = make_bundle()

    too_early_day, n1 = _run(cfg, bundle, art, _pacific(2026, 3, 3, 10, 0))   # day 3 < dom 5
    assert too_early_day.actions == [] and n1.sent == []

    day_ok_hour_early, n2 = _run(cfg, bundle, art, _pacific(2026, 3, 5, 6, 0))  # day ok, hour < 7
    assert day_ok_hour_early.actions == [] and n2.sent == []

    both_ok, n3 = _run(cfg, bundle, art, _pacific(2026, 3, 5, 7, 0))
    assert [a.kind for a in both_ok.actions] == ["MONTHLY_TEST"]
    assert len(n3.sent) == 1


def test_fires_and_routes_to_the_full_configured_audience(make_alert_config, make_bundle, art, tmp_path):
    cfg = _cfg(make_alert_config, tmp_path)
    bundle = make_bundle()

    res, notifier = _run(cfg, bundle, art, _pacific(2026, 3, 1, 7, 30))

    assert [a.kind for a in res.actions] == ["MONTHLY_TEST"]
    assert len(notifier.sent) == 1
    alert, recipients = notifier.sent[0]
    assert "[TEST]" in alert.subject                 # renders as a normal TEST notice
    assert set(recipients.emails) == {f"person{i}@example.org" for i in range(8)}
    assert load_state(cfg.state_path).last_monthly_test_ym == "2026-03"


def test_does_not_refire_on_restart_within_the_same_local_month(make_alert_config, make_bundle, art, tmp_path):
    cfg = _cfg(make_alert_config, tmp_path)
    bundle = make_bundle()

    first, _ = _run(cfg, bundle, art, _pacific(2026, 3, 1, 7, 5))
    assert [a.kind for a in first.actions] == ["MONTHLY_TEST"]

    # Simulates an app restart mid-month: a brand-new run_once call, no in-memory state
    # carried over -- decide_notifications only has what load_state reads back from disk.
    second, notifier2 = _run(cfg, bundle, art, _pacific(2026, 3, 15, 9, 0))
    assert second.actions == [] and notifier2.sent == []
    assert load_state(cfg.state_path).last_monthly_test_ym == "2026-03"


def test_fires_again_the_following_month(make_alert_config, make_bundle, art, tmp_path):
    cfg = _cfg(make_alert_config, tmp_path)
    bundle = make_bundle()
    _run(cfg, bundle, art, _pacific(2026, 3, 1, 7, 5))

    res, notifier = _run(cfg, bundle, art, _pacific(2026, 4, 1, 7, 5))

    assert [a.kind for a in res.actions] == ["MONTHLY_TEST"]
    assert len(notifier.sent) == 1
    assert load_state(cfg.state_path).last_monthly_test_ym == "2026-04"


def test_undelivered_monthly_test_holds_state_and_retries(make_alert_config, make_bundle, art, tmp_path):
    """Same fail-loud contract as the ladder/observed tracks: a delivery failure must not
    be recorded as sent, or the pipeline check silently stops checking anything."""
    cfg = _cfg(make_alert_config, tmp_path)
    bundle = make_bundle()

    import lake_rise.alerting.service as svc
    orig = svc.evaluate
    svc.evaluate = lambda *a, **k: _steady_state(_pacific(2026, 3, 1, 7, 5))
    try:
        first = service.run_once(cfg, bundle=bundle, art=art, notifiers=[])  # no channels
    finally:
        svc.evaluate = orig
    assert [a.kind for a in first.actions] == ["MONTHLY_TEST"]
    assert first.sent is False
    assert load_state(cfg.state_path).last_monthly_test_ym is None      # held, not advanced

    res, notifier = _run(cfg, bundle, art, _pacific(2026, 3, 1, 7, 10))
    assert [a.kind for a in res.actions] == ["MONTHLY_TEST"]            # retried
    assert len(notifier.sent) == 1
    assert load_state(cfg.state_path).last_monthly_test_ym == "2026-03"
