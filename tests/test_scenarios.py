"""Scenario synthesis and the end-to-end predictor."""

from datetime import datetime

import pytest

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


def test_noaa_blends_into_median_when_forecast_dry(art):
    """#2: a dropped feed (all-zero point forecast) during a NOAA flood watch must
    lift the MEDIAN, not just the high tail -- otherwise the band is incoherent
    (median dry, 90th pct a major storm) and P(crossing) is capped at 0.5."""
    f = art.uncertainty.noaa_median_fraction
    scen = {s.name: s for s in synthesize_scenarios(art, [0.0] * 24, month=1,
                                                    noaa_high_total_in=6.0)}
    lo, med, hi = (sum(scen[k].hourly_in) for k in ("low", "median", "high"))
    assert med > 0.0                          # median no longer dry
    assert lo <= med <= hi                    # coherent ordering
    assert med == pytest.approx(f * 6.0)      # seeded at f * noaa total
    assert hi >= 6.0 - 1e-6                    # NOAA still anchors the tail


def test_noaa_dry_feed_unlocks_critical_crossing(art):
    """#2 end-to-end: when the blended-median NOAA storm itself crosses the dam crest,
    P(cross) must be able to exceed the old 0.5 cap so CRITICAL (>=0.60) can fire. The
    threshold (342.2) sits ABOVE the start elevation, so this is a real crossing test,
    not trivially satisfied by starting above it. Under the old high-branch-only
    behavior median stayed dry, low==median collapsed, and P was capped at <=0.50."""
    crest = art.thresholds_abs_ft.dam_crest                 # 342.2
    scen = synthesize_scenarios(art, [0.0] * 24, month=1, noaa_high_total_in=16.0)
    bundle = InputBundle(
        as_of=datetime(2026, 1, 15), current_elevation_abs_ft=341.9, stop_log_count=0,
        forecast_scenarios=scen,
    )
    res = predict(bundle, art)
    median_peak = next(s.peak_elevation for s in res.scenarios if s.name == "median")
    assert median_peak > crest                              # central case actually crosses
    assert res.p_cross_crest > 0.60                         # CRITICAL now reachable (was <=0.5)


def test_noaa_below_forecast_leaves_median_untouched(art):
    """A NOAA total the point forecast already exceeds must not pull the median DOWN."""
    point = [0.3] * 24                        # 7.2 in, well above f * 2.0
    med_point = next(s for s in synthesize_scenarios(art, point) if s.name == "median")
    med_noaa = next(s for s in synthesize_scenarios(art, point, noaa_high_total_in=2.0)
                    if s.name == "median")
    assert med_noaa.hourly_in == med_point.hourly_in


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


def test_exceedance_probability_interior_and_tails():
    pts = [(340.0, 0.10), (341.0, 0.50), (343.0, 0.90)]  # (peak elev, cdf)
    assert _exceedance_probability(pts, 341.0) == 0.5        # at the median (interior anchor)
    assert 0.25 < _exceedance_probability(pts, 342.0) < 0.35  # smooth interior value

    # Lower tail: well below all peaks -> survival approaches (but never exceeds) 1.
    p_low = _exceedance_probability(pts, 339.0)
    assert 0.9 < p_low < 1.0

    # Upper tail: above the high peak the risk is SMALL but STRICTLY POSITIVE -- the
    # fat under-forecast tail is surfaced, not clamped to a hard zero.
    p_345 = _exceedance_probability(pts, 345.0)
    assert 0.0 < p_345 < 0.1

    # Upper tail is monotone decreasing and continuous at the q90 knot.
    knot = _exceedance_probability(pts, 343.0)
    assert knot == pytest.approx(1.0 - 0.90)             # value-continuous with interior
    assert knot > _exceedance_probability(pts, 343.5) > p_345 > _exceedance_probability(pts, 346.0) > 0.0

    # A WIDER band (high peak pushed out) gives a FATTER tail -> strictly higher risk
    # at the same above-q90 threshold (the dangerous-when-uncertain behavior).
    wide = [(340.0, 0.10), (341.0, 0.50), (344.0, 0.90)]
    assert _exceedance_probability(wide, 345.0) > p_345

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


def test_predict_flags_out_of_validated_geometry(art):
    """A calm forecast stays in the validated band (flag off); a peak driven above
    valid_elev_range_ft (into the extrapolated overtopping regime) flags it -- but
    still returns an estimate rather than clamping."""
    _, hi = art.geometry.valid_elev_range_ft

    calm = InputBundle(
        as_of=datetime(2026, 1, 15), current_elevation_abs_ft=339.0, stop_log_count=0,
        forecast_scenarios=synthesize_scenarios(art, [0.0] * 12),
    )
    calm_res = predict(calm, art)
    assert calm_res.peak_outside_validated_geometry is False

    # Start just below the crest and dump a heavy storm so the high scenario climbs
    # past the top of the validated geometry band.
    flood = InputBundle(
        as_of=datetime(2026, 1, 15), current_elevation_abs_ft=342.8, stop_log_count=0,
        forecast_scenarios=synthesize_scenarios(art, [0.5] * 24),
    )
    flood_res = predict(flood, art)
    assert flood_res.peak_outside_validated_geometry is True
    # Flagged, not clamped: an estimate above the band is still produced.
    assert max(s.peak_elevation for s in flood_res.scenarios) > hi


def test_fixture_source_roundtrip(art):
    fx = Path(__file__).resolve().parents[1] / "fixtures" / "example_snapshot.json"
    bundle = FixtureSource(art, fx).build_bundle()
    # datum applied: reading 0.5 + offset -> absolute
    assert bundle.current_elevation_abs_ft == 0.5 + art.datum.sensor_to_absolute_offset_ft
    result = predict(bundle, art)
    assert result.freeboard_ft == art.thresholds_abs_ft.dam_crest - bundle.current_elevation_abs_ft
    assert 0.0 <= result.p_cross_crest <= 1.0
