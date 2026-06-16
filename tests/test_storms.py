"""The shared storm -> bundle composition layer (storms.py)."""

from datetime import datetime

import pytest

from lake_rise.predict import predict
from lake_rise.storms import bundle_for_storm, storm_series


def test_storm_series_modes_and_horizon(art):
    # preset
    s = storm_series(art, preset="heavy_storm", horizon_h=72)
    assert len(s) == 72 and sum(s) > 0

    # historical
    h = storm_series(art, historical_id="h0", horizon_h=96)
    assert len(h) == 96 and sum(h) > 0

    # custom hourly is padded/truncated to the horizon
    custom = storm_series(art, hourly_in=[0.1, 0.2, 0.3], horizon_h=5)
    assert custom == [0.1, 0.2, 0.3, 0.0, 0.0]

    # constant rate * duration, delayed by a dry lead
    rd = storm_series(art, rate_in_per_hr=0.2, duration_h=3, start_offset_h=2, horizon_h=8)
    assert rd == [0.0, 0.0, 0.2, 0.2, 0.2, 0.0, 0.0, 0.0]

    # empty spec -> all dry
    assert storm_series(art, horizon_h=4) == [0.0, 0.0, 0.0, 0.0]


def test_storm_series_unknown_keys_raise(art):
    with pytest.raises(KeyError):
        storm_series(art, preset="not_a_preset")
    with pytest.raises(KeyError):
        storm_series(art, historical_id="h999")


def test_bundle_for_storm_bands_and_predicts(art):
    series = storm_series(art, preset="heavy_storm", horizon_h=72)
    bundle = bundle_for_storm(
        art, series, current_elevation_abs_ft=341.0, stop_log_count=3,
        month=1, as_of=datetime(2026, 1, 15))
    names = [s.name for s in bundle.forecast_scenarios]
    assert names == ["low", "median", "high"]
    assert bundle.horizon_hours == 72
    # band ordering by total rainfall (low <= median <= high)
    totals = {s.name: sum(s.hourly_in) for s in bundle.forecast_scenarios}
    assert totals["low"] <= totals["median"] <= totals["high"]

    result = predict(bundle, art)
    peaks = {s.name: s.peak_elevation for s in result.scenarios}
    assert peaks["low"] <= peaks["median"] <= peaks["high"]


def test_bundle_for_storm_no_band_repeats_series(art):
    series = storm_series(art, rate_in_per_hr=0.1, duration_h=6, horizon_h=24)
    bundle = bundle_for_storm(
        art, series, current_elevation_abs_ft=340.0, stop_log_count=3,
        month=7, as_of=datetime(2026, 7, 15), band=False)
    assert all(s.hourly_in == series for s in bundle.forecast_scenarios)
