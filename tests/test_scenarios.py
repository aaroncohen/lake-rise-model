"""Scenario synthesis and the end-to-end predictor."""

from datetime import datetime

from lake_rise import sim
from lake_rise.bundle import InputBundle, ScenarioRain
from lake_rise.predict import predict
from lake_rise.scenarios import synthesize_scenarios
from lake_rise.sources.fixture import FixtureSource
from pathlib import Path


def test_scenario_band_orders_low_median_high(art):
    point = [0.1] * 24
    low, median, high = synthesize_scenarios(art, point)
    assert low.name == "low" and high.name == "high"
    assert sum(low.hourly_in) < sum(median.hourly_in) < sum(high.hourly_in)


def test_noaa_alert_lifts_high_branch(art):
    point = [0.05] * 24
    scen = synthesize_scenarios(art, point, noaa_high_total_in=4.0)
    high = next(s for s in scen if s.name == "high")
    assert sum(high.hourly_in) >= 4.0 - 1e-6


def test_predict_trajectories_are_monotone_in_scenario(art):
    """high scenario peak >= median >= low (same start state)."""
    storm = sim.constant_storm(0.15, 48)
    point_scen = synthesize_scenarios(art, storm)
    bundle = InputBundle(
        as_of=datetime(2026, 1, 15),
        current_elevation_abs_ft=339.0,
        stop_log_count=0,
        forecast_scenarios=point_scen,
        initial_sm_in=art.hspf.LZSN_in,
    )
    result = predict(bundle, art)
    peaks = {s.name: s.peak_elevation for s in result.scenarios}
    assert peaks["low"] <= peaks["median"] <= peaks["high"]


def test_predict_flags_stale_data(art):
    bundle = InputBundle(
        as_of=datetime(2026, 1, 15),
        current_elevation_abs_ft=339.0,
        stop_log_count=0,
        forecast_scenarios=synthesize_scenarios(art, [0.0] * 12),
        rainfall_has_gaps=True,
    )
    assert predict(bundle, art).data_fresh is False


def test_fixture_source_roundtrip(art):
    fx = Path(__file__).resolve().parents[1] / "fixtures" / "example_snapshot.json"
    bundle = FixtureSource(art, fx).build_bundle()
    # datum applied: reading 0.5 + offset -> absolute
    assert bundle.current_elevation_abs_ft == 0.5 + art.datum.sensor_to_absolute_offset_ft
    result = predict(bundle, art)
    assert result.freeboard_ft == art.thresholds_abs_ft.dam_crest - bundle.current_elevation_abs_ft
    assert 0.0 <= result.p_cross_crest <= 1.0
