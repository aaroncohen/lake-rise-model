"""Core fire-on-crossing logic: escalation, no-repeat, downgrade, all-clear, test track."""

from datetime import datetime, timedelta, timezone

from lake_rise.observed import GaugeObservation
from lake_rise.alerting.rules import AlertDecision
from lake_rise.alerting.state import (
    AlertState,
    decide_notifications,
    decide_observed_notifications,
    load_state,
    save_state,
)


def _decision(rank, name, *, test_active=False, current=339.0):
    return AlertDecision(
        generated_at=datetime(2026, 1, 15, tzinfo=timezone.utc), horizon_hours=72,
        current_elevation=current, freeboard_ft=3.2, datum_offset_ft=338.375, data_fresh=True,
        active_rank=rank, active_level_name=name,
        probabilities={"early_warning": 0.0, "dam_crest": 0.0}, thresholds=(),
        peak_elevation=340.0, peak_at=None, peak_elevation_high=340.5,
        forecast_total_in=0.0, peak_rain_hour=None, confidence_pct=90,
        confidence_label="High", test_active=test_active,
    )


def test_episode_peak_tracked_and_carried_on_all_clear(make_alert_config):
    cfg = make_alert_config()
    state = AlertState()

    # Rise to DANGER, then a higher reading at EVACUATE -> high-water mark climbs.
    _, state = decide_notifications(_decision(4, "DANGER", current=341.5), state, cfg)
    assert state.peak_elevation_ft == 341.5
    _, state = decide_notifications(_decision(6, "EVACUATE", current=342.6), state, cfg)
    assert state.peak_elevation_ft == 342.6
    # Recede but still elevated -> the peak holds.
    _, state = decide_notifications(_decision(2, "WARNING", current=341.2), state, cfg)
    assert state.peak_elevation_ft == 342.6
    # Return to normal -> ALL_CLEAR carries the episode peak; stored peak resets.
    actions, state = decide_notifications(_decision(0, None, current=340.0), state, cfg)
    ac = next(a for a in actions if a.kind == "ALL_CLEAR")
    assert ac.episode_peak_ft == 342.6
    assert state.peak_elevation_ft == 0.0


def test_fires_on_initial_and_escalation_but_not_repeat(make_alert_config):
    cfg = make_alert_config()
    state = AlertState()

    # Initial crossing into ADVISORY -> fire.
    actions, state = decide_notifications(_decision(1, "ADVISORY"), state, cfg)
    assert [a.kind for a in actions] == ["LEVEL"] and actions[0].rank == 1

    # Same level again next hour -> silent.
    actions, state = decide_notifications(_decision(1, "ADVISORY"), state, cfg)
    assert actions == []

    # Escalation to DANGER -> fire, recipients resolved at the higher rank.
    actions, state = decide_notifications(_decision(4, "DANGER"), state, cfg)
    assert [a.kind for a in actions] == ["LEVEL"] and actions[0].rank == 4
    assert state.max_rank_reached == 4


def test_downgrade_is_silent_then_reescalation_fires(make_alert_config):
    cfg = make_alert_config()
    state = AlertState()
    _, state = decide_notifications(_decision(4, "DANGER"), state, cfg)        # fire to 4

    # Downgrade to WATCH(3): silent, but stored rank lowers so a re-escalation can fire.
    actions, state = decide_notifications(_decision(3, "WATCH"), state, cfg)
    assert actions == [] and state.level_rank == 3 and state.max_rank_reached == 4

    # Back up to CRITICAL(5) -> fires again.
    actions, state = decide_notifications(_decision(5, "CRITICAL"), state, cfg)
    assert [a.kind for a in actions] == ["LEVEL"] and actions[0].rank == 5


def test_all_clear_once_to_broadest_audience(make_alert_config):
    cfg = make_alert_config()
    state = AlertState()
    _, state = decide_notifications(_decision(5, "CRITICAL"), state, cfg)      # reach top
    _, state = decide_notifications(_decision(3, "WATCH"), state, cfg)         # silent downgrade

    # Return to normal -> one all-clear, addressed to the highest rank reached (5).
    actions, state = decide_notifications(_decision(0, None), state, cfg)
    assert [a.kind for a in actions] == ["ALL_CLEAR"] and actions[0].rank == 5
    assert state.level_rank == 0 and state.max_rank_reached == 0

    # Still normal next hour -> nothing.
    actions, state = decide_notifications(_decision(0, None), state, cfg)
    assert actions == []


def test_all_clear_suppressed_when_disabled(make_alert_config):
    cfg = make_alert_config(send_all_clear=False)
    state = AlertState()
    _, state = decide_notifications(_decision(2, "WARNING"), state, cfg)
    actions, state = decide_notifications(_decision(0, None), state, cfg)
    assert actions == [] and state.level_rank == 0


def test_test_track_independent_fires_once(make_alert_config):
    cfg = make_alert_config(test_enabled=True)
    state = AlertState()

    # Rain enters -> TEST fires; ladder still at 0.
    actions, state = decide_notifications(_decision(0, None, test_active=True), state, cfg)
    assert [a.kind for a in actions] == ["TEST"] and state.test_active is True

    # Still raining -> no repeat.
    actions, state = decide_notifications(_decision(0, None, test_active=True), state, cfg)
    assert actions == []

    # Rain leaves -> one TEST_CLEAR.
    actions, state = decide_notifications(_decision(0, None, test_active=False), state, cfg)
    assert [a.kind for a in actions] == ["TEST_CLEAR"] and state.test_active is False


def test_state_round_trips(tmp_path, make_alert_config):
    path = tmp_path / "state.json"
    save_state(path, AlertState(level_rank=4, level_name="DANGER", max_rank_reached=5,
                                test_active=True, updated_at="2026-01-15T00:00:00+00:00",
                                observed_eap_rank=2,
                                observed_clear_since="2026-01-15T01:00:00+00:00"))
    s = load_state(path)
    assert s.level_rank == 4 and s.max_rank_reached == 5 and s.test_active is True
    assert s.observed_eap_rank == 2 and s.observed_clear_since is not None
    # Missing file -> zeroed default.
    assert load_state(tmp_path / "nope.json").level_rank == 0

    # A pre-observed-alert state file is a schema-compatible migration: new fields default.
    old = tmp_path / "old_state.json"
    old.write_text('{"level_rank": 2, "level_name": "WARNING"}')
    loaded_old = load_state(old)
    assert loaded_old.level_rank == 2 and loaded_old.observed_eap_rank == 0
    assert loaded_old.observed_clear_since is None


def _observed(at, gauge, *, confirmed=True):
    return GaugeObservation(at, gauge, confirmed, 3,
                            None if confirmed else "history unavailable")


def test_observed_eap_escalates_once_and_consolidates_jumps():
    start = datetime(2026, 1, 15, tzinfo=timezone.utc)
    state = AlertState()

    actions, state = decide_observed_notifications(_observed(start, 3.29), state)
    assert actions == [] and state.observed_eap_rank == 0

    actions, state = decide_observed_notifications(_observed(start, 3.30), state)
    assert len(actions) == 1 and actions[0].rank == 1
    # Holding or receding within the event is silent and stays latched.
    actions, state = decide_observed_notifications(_observed(start, 3.50), state)
    assert actions == [] and state.observed_eap_rank == 1

    # A jump over both remaining thresholds is one consolidated top-rank action.
    actions, state = decide_observed_notifications(_observed(start, 4.40), state)
    assert len(actions) == 1 and actions[0].rank == 3
    assert actions[0].observed_previous_rank == 1


def test_observed_eap_rearms_only_after_hysteresis_and_30_minutes():
    start = datetime(2026, 1, 15, tzinfo=timezone.utc)
    state = AlertState(observed_eap_rank=2)

    # Between 3.25 and 3.30 is below alert but not below the reset threshold.
    _, state = decide_observed_notifications(_observed(start, 3.26), state)
    assert state.observed_clear_since is None and state.observed_eap_rank == 2

    _, state = decide_observed_notifications(_observed(start + timedelta(minutes=5), 3.24), state)
    assert state.observed_clear_since is not None
    # A bounce to the hysteresis band interrupts the timer.
    _, state = decide_observed_notifications(_observed(start + timedelta(minutes=25), 3.25), state)
    assert state.observed_clear_since is None and state.observed_eap_rank == 2

    below = start + timedelta(minutes=30)
    _, state = decide_observed_notifications(_observed(below, 3.24), state)
    _, state = decide_observed_notifications(_observed(below + timedelta(minutes=29), 3.20), state)
    assert state.observed_eap_rank == 2
    _, state = decide_observed_notifications(_observed(below + timedelta(minutes=30), 3.20), state)
    assert state.observed_eap_rank == 0 and state.observed_clear_since is None

    actions, state = decide_observed_notifications(
        _observed(below + timedelta(minutes=35), 3.30), state)
    assert len(actions) == 1 and actions[0].rank == 1


def test_invalid_observation_does_not_change_observed_state():
    at = datetime(2026, 1, 15, tzinfo=timezone.utc)
    prior = AlertState(observed_eap_rank=1, observed_clear_since=at.isoformat())
    invalid = GaugeObservation(at + timedelta(minutes=5), None, False, 0, "stale")
    actions, state = decide_observed_notifications(invalid, prior)
    assert actions == [] and state is prior


def test_decide_notifications_preserves_last_drill_ym(make_alert_config):
    # F state.py:130-139 regression: the rebuilt state must carry the monthly-drill marker, else
    # every live run_once resets it and the scheduler re-runs the full drill every tick.
    cfg = make_alert_config()
    state = AlertState(last_drill_ym="2026-07")
    _, new = decide_notifications(_decision(0, None), state, cfg)
    assert new.last_drill_ym == "2026-07"


def test_save_state_is_atomic_and_leaves_no_tmp(tmp_path):
    # F state.py:64-76 regression: atomic write (temp + os.replace), no truncated file, no *.tmp.
    path = tmp_path / "alert_state.json"
    save_state(path, AlertState(level_rank=2, level_name="WATCH", last_drill_ym="2026-07"))
    s = load_state(path)
    assert s.level_rank == 2 and s.last_drill_ym == "2026-07"
    assert list(tmp_path.glob("*.tmp")) == []
