"""Ladder evaluation and the independent test trigger."""

from datetime import datetime, timezone

from lake_rise.alerting.rules import evaluate


def test_ladder_picks_highest_satisfied_level(art, make_prediction, make_bundle, make_alert_config):
    cfg = make_alert_config()
    bundle = make_bundle()

    # No risk -> rank 0.
    d = evaluate(make_prediction(p_ew=0.0, p_crest=0.0), bundle, art, cfg)
    assert d.active_rank == 0 and d.active_level_name is None

    # Early-warning 35% -> ADVISORY (rank 1), not WARNING (needs 60%).
    d = evaluate(make_prediction(p_ew=0.35, p_crest=0.0), bundle, art, cfg)
    assert d.active_level_name == "ADVISORY" and d.active_rank == 1

    # Crest 35% -> DANGER (rank 4: dam_crest >= 0.30); crest also implies WATCH satisfied,
    # but DANGER is higher.
    d = evaluate(make_prediction(p_ew=0.9, p_crest=0.35), bundle, art, cfg)
    assert d.active_level_name == "DANGER" and d.active_rank == 4

    # Crest 70% -> CRITICAL (rank 5).
    d = evaluate(make_prediction(p_ew=0.9, p_crest=0.70), bundle, art, cfg)
    assert d.active_level_name == "CRITICAL" and d.active_rank == 5

    # Bridge-deck overtopping likely (35%) -> EVACUATE (rank 6), the EAP imminent-failure level.
    d = evaluate(make_prediction(p_ew=0.95, p_crest=0.8, p_bridge=0.35), bundle, art, cfg)
    assert d.active_level_name == "EVACUATE" and d.active_rank == 6
    # The bridge-deck threshold rides along in the decision for the templates.
    assert d.threshold("bridge_deck").probability == 0.35


def test_crossing_and_peak_times_in_decision(art, make_prediction, make_bundle, make_alert_config):
    cfg = make_alert_config()
    start = datetime(2026, 1, 15, tzinfo=timezone.utc)
    result = make_prediction(p_ew=0.5, p_crest=0.2, start=start,
                             median_htc=30.0, high_htc=18.0, peak_hour=10, median_peak=342.5)
    d = evaluate(result, make_bundle(start=start), art, cfg)

    crest = d.threshold("dam_crest")
    assert crest.median_cross_at.hour == (start.hour + 30) % 24  # 30h after start
    assert crest.earliest_cross_at < crest.median_cross_at        # high scenario is earlier
    # Peak comes from the median trajectory (peak at hour 10).
    assert d.peak_at == start.replace(hour=10)
    assert abs(d.peak_elevation - 342.5) < 1e-6


def test_test_trigger_only_above_threshold(art, make_prediction, make_bundle, make_alert_config):
    cfg = make_alert_config(test_enabled=True, test_rain_in=0.10)
    res = make_prediction()

    assert evaluate(res, make_bundle(total_in=0.05), art, cfg).test_active is False
    assert evaluate(res, make_bundle(total_in=0.50), art, cfg).test_active is True

    # Disabled -> never active, even with lots of rain.
    off = make_alert_config(test_enabled=False)
    assert evaluate(res, make_bundle(total_in=2.0), art, off).test_active is False
