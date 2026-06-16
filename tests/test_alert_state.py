"""Core fire-on-crossing logic: escalation, no-repeat, downgrade, all-clear, test track."""

from datetime import datetime, timezone

from lake_rise.alerting.rules import AlertDecision
from lake_rise.alerting.state import AlertState, decide_notifications, load_state, save_state


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
                                test_active=True, updated_at="2026-01-15T00:00:00+00:00"))
    s = load_state(path)
    assert s.level_rank == 4 and s.max_rank_reached == 5 and s.test_active is True
    # Missing file -> zeroed default.
    assert load_state(tmp_path / "nope.json").level_rank == 0
