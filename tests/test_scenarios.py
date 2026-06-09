"""Scenario synthesis and the end-to-end predictor."""

from datetime import datetime

from lake_rise import sim
from lake_rise.bundle import InputBundle, ScenarioRain
from lake_rise.predict import _exceedance_probability, predict
from lake_rise.scenarios import synthesize_scenarios
from lake_rise.sources.fixture import FixtureSource
from pathlib import Path


def test_scenario_band_orders_low_median_high(art):
    point = [0.1] * 24
    low, median, high = synthesize_scenarios(art, point)
    assert low.name == "low" and high.name == "high"
    assert sum(low.hourly_in) < sum(median.hourly_in) < sum(high.hourly_in)


def test_band_widens_with_lead(art):
    """Later forecast hours get a wider high/forecast ratio (QPF skill decays)."""
    point = [0.1] * 120
    low, med, high = synthesize_scenarios(art, point, month=1)
    r_day1 = high.hourly_in[12] / point[12]
    r_day5 = high.hourly_in[100] / point[100]
    assert r_day5 > r_day1


def test_summer_band_wider_than_winter(art):
    """Convective summer is less predictable than the cool-season frontal regime."""
    point = [0.2] * 48
    _, _, h_winter = synthesize_scenarios(art, point, month=1)
    _, _, h_summer = synthesize_scenarios(art, point, month=7)
    assert sum(h_summer.hourly_in) > sum(h_winter.hourly_in)


def test_high_tail_is_fatter_than_low(art):
    """Asymmetric band: the upper tail (dangerous direction) is wider than the lower."""
    point = [0.1] * 24
    low, med, high = synthesize_scenarios(art, point, month=1)
    above = high.hourly_in[0] - med.hourly_in[0]
    below = med.hourly_in[0] - low.hourly_in[0]
    assert above > below


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


def test_exceedance_probability_interpolates_and_clamps():
    pts = [(340.0, 0.10), (341.0, 0.50), (343.0, 0.90)]  # (peak elev, cdf)
    assert _exceedance_probability(pts, 341.0) == 0.5        # at the median
    assert _exceedance_probability(pts, 339.0) == 1.0        # below all -> certain
    assert _exceedance_probability(pts, 345.0) == 0.0        # above all -> ~impossible
    assert 0.25 < _exceedance_probability(pts, 342.0) < 0.35  # smooth interior value
    # degenerate (band off, equal peaks) -> step function
    flat = [(340.0, 0.1), (340.0, 0.5), (340.0, 0.9)]
    assert _exceedance_probability(flat, 339.0) == 1.0
    assert _exceedance_probability(flat, 341.0) == 0.0


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
