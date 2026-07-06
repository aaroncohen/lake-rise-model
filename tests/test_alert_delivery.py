"""S3 regression: alerting must fail *loud*, not silently swallow an undelivered escalation.

`run_once` gates the ladder/test/monthly state advance on a delivery receipt: if a notice was not
actually delivered (no channels, no recipients, or every channel raised), the affected track's state
is held so the fire-on-crossing rule re-fires next tick instead of losing the alert.
"""

from lake_rise.alerting import service
from lake_rise.alerting.config import Recipients
from lake_rise.alerting.state import AlertState, hold_undelivered, load_state

# Reuse the synthetic-decision builder from the state tests.
from test_alert_state import _decision


class _RecordingNotifier:
    name = "rec"

    def __init__(self):
        self.sent = []

    def send(self, alert, recipients):
        self.sent.append(alert.subject)


class _RaisingNotifier:
    name = "boom"

    def send(self, alert, recipients):
        raise RuntimeError("channel down")


def _cfg(make_alert_config, tmp_path):
    # ADVISORY(rank 1) resolves to the "ops" audience; give it one email so delivery can succeed.
    return make_alert_config(
        audiences={"ops": Recipients(emails=("ops@example.com",))},
        state_path=tmp_path / "alert_state.json", channels=("email",),
    )


def _force_advisory(monkeypatch):
    """Bypass the hydrology: make evaluate() report a rank-1 ADVISORY crossing."""
    monkeypatch.setattr(service, "evaluate", lambda *a, **k: _decision(1, "ADVISORY"))


def test_undelivered_level_holds_state_and_retries(art, make_bundle, make_alert_config,
                                                   tmp_path, monkeypatch):
    cfg = _cfg(make_alert_config, tmp_path)
    _force_advisory(monkeypatch)
    bundle = make_bundle()

    # No channels -> nothing delivered. The crossing must NOT advance the ladder on disk.
    res = service.run_once(cfg, bundle=bundle, art=art, notifiers=[])
    assert [a.kind for a in res.actions] == ["LEVEL"]
    assert res.sent is False
    assert load_state(cfg.state_path).level_rank == 0        # held, not advanced

    # Next tick re-fires the same LEVEL (the alert wasn't silently swallowed).
    res2 = service.run_once(cfg, bundle=bundle, art=art, notifiers=[])
    assert [a.kind for a in res2.actions] == ["LEVEL"]
    assert load_state(cfg.state_path).level_rank == 0


def test_raising_notifier_holds_state(art, make_bundle, make_alert_config, tmp_path, monkeypatch):
    cfg = _cfg(make_alert_config, tmp_path)
    _force_advisory(monkeypatch)

    res = service.run_once(cfg, bundle=make_bundle(), art=art, notifiers=[_RaisingNotifier()])
    assert res.sent is False
    assert load_state(cfg.state_path).level_rank == 0        # every channel raised -> not advanced


def test_delivered_level_advances_then_silent(art, make_bundle, make_alert_config,
                                              tmp_path, monkeypatch):
    cfg = _cfg(make_alert_config, tmp_path)
    _force_advisory(monkeypatch)
    rec = _RecordingNotifier()
    bundle = make_bundle()

    res = service.run_once(cfg, bundle=bundle, art=art, notifiers=[rec])
    assert res.sent is True
    assert len(rec.sent) == 1
    assert load_state(cfg.state_path).level_rank == 1        # delivered -> advanced

    # Same level next tick -> no repeat (happy path unchanged), state stays at 1.
    res2 = service.run_once(cfg, bundle=bundle, art=art, notifiers=[rec])
    assert res2.actions == []
    assert len(rec.sent) == 1
    assert load_state(cfg.state_path).level_rank == 1


def test_hold_undelivered_reverts_only_affected_track():
    prior = AlertState(level_rank=0, test_active=False, last_monthly_test_ym="2026-06")
    new = AlertState(level_rank=4, level_name="DANGER", max_rank_reached=4,
                     test_active=True, peak_elevation_ft=342.0, last_monthly_test_ym="2026-06")

    # Ladder notice failed but the TEST notice was delivered: revert only the ladder fields.
    held = hold_undelivered(new, prior, {"LEVEL"})
    assert held.level_rank == 0 and held.max_rank_reached == 0 and held.peak_elevation_ft == 0.0
    assert held.test_active is True

    # Nothing undelivered -> the advanced state passes through untouched.
    assert hold_undelivered(new, prior, set()) is new
