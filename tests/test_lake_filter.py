"""The lake filter (docs/plans/lake-filter.md, T1): synthetic 12 h minute series with noise that is
not only the filter's own model, compared against the truth and today's anchor. The storm tests'
bounds apply to the mean over 20 seeded noise realizations (the ruling on #66): a single run's
error in rain is about the size of the bounds."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from lake_rise import units
from lake_rise.artifact import load_artifact
from lake_rise.geometry import surface_area_acres
from lake_rise.lake_filter import estimate_lake, minute_grid, rain_by_minute, wetness
from lake_rise.model import m5_lake_update, m6_spillway

ART = load_artifact()
OFF = ART.datum.sensor_to_absolute_offset_ft
CONTROL = ART.stop_logs.control_elev(3)
MONTH = 11
N = 12 * 60
RAMP = 120                      # the storm ramps over the last 2 h
START = N - RAMP
BASE = 1.0                      # stick ft, below the 3-log crest so the spillway stays shut
RATE = 0.1 / 60                 # ft/min
DRY = [0.0] * N
# 0.25 in/h from 2 h before the ramp until it ends (T3's storm shape)
STORM_RAIN = [0.25 / 60 if i >= START - 120 else 0.0 for i in range(N)]
FLAT = np.full(N, BASE)
STORM = BASE + np.clip(np.arange(N) - START, 0, None) * RATE


SEEDS = range(20)


def _noise(seed):
    """White noise sd 0.05 plus offset steps holding a uniform +-0.05 ft for 10-40 min."""
    rng = np.random.default_rng(seed)
    white = rng.normal(0.0, 0.05, N)
    steps = np.empty(N)
    i = 0
    while i < N:
        k = int(rng.integers(10, 41))
        steps[i:i + k] = rng.uniform(-0.05, 0.05)
        i += k
    return white + steps


NOISES = [_noise(seed) for seed in SEEDS]


def _readings(truth, noise=NOISES[0]):
    return [float(v) for v in truth + noise]


def _storm_inflow(rain):
    """The watershed inflow (cfs) that makes the model's lake rise match the storm ramp."""
    q = []
    for i in range(N):
        h = STORM[i] + OFF
        rest = m5_lake_update(ART, h, 0.0, rain[i], m6_spillway(ART, h, CONTROL), 1 / 60, MONTH)[0] - h
        need = (STORM[i] - STORM[i - 1]) - rest if i > START else 0.0
        q.append(need * surface_area_acres(ART.geometry, h) / (units.CFS_TO_ACFT_PER_HR / 60))
    return q


def _estimate(readings, rain, inflow=None, minutes=N):
    """The stateless estimate a call at the end of minute `minutes` would give."""
    return estimate_lake(ART, readings[:minutes], rain[:minutes],
                         inflow_cfs_by_minute=None if inflow is None else inflow[:minutes],
                         control_elev_abs=CONTROL, month=MONTH)


def _anchor(readings, minutes):
    """Today's anchor: the 30-min trailing median."""
    window = np.array([np.nan if r is None else r for r in readings[minutes - 30:minutes]])
    return float(np.nanmedian(window))


def _mean_behind(rain, minutes_into_ramp, inflow=None, dropout=()):
    """Mean over the realizations of (filter, today's anchor) behind the storm's truth, for a call
    `minutes_into_ramp` after the ramp starts."""
    k = START + minutes_into_ramp
    filtered, today = [], []
    for noise in NOISES:
        readings = _readings(STORM, noise)
        for i in dropout:
            readings[i] = None
        filtered.append(STORM[k - 1] - _estimate(readings, rain, inflow, k).level_ft)
        today.append(STORM[k - 1] - _anchor(readings, k))
    return float(np.mean(filtered)), float(np.mean(today))


def test_a_flat_lake_stays_within_0_02_ft():
    est = _estimate(_readings(FLAT), DRY)
    assert est.level_ft == pytest.approx(BASE, abs=0.02)


def test_dry_jitter_beats_a_30_min_median():
    readings = _readings(FLAT)
    ends = range(N - 7 * 60, N + 1, 15)
    filtered = np.array([_estimate(readings, DRY, None, k).level_ft for k in ends])
    anchor = np.array([_anchor(readings, k) for k in ends])
    assert np.std(filtered[4:] - filtered[:-4]) < np.std(anchor[4:] - anchor[:-4])


def test_a_modelled_storm_is_tracked():
    inflow = _storm_inflow(STORM_RAIN)
    for minute in (30, 60, 120):
        filtered, _ = _mean_behind(STORM_RAIN, minute, inflow)
        assert abs(filtered) <= 0.02, minute


@pytest.mark.parametrize("rain", [STORM_RAIN, [None] * N], ids=["in rain", "missing rain counts as wet"])
def test_a_blind_storm_stays_within_p12(rain):
    for minute, extra in ((30, 0.03), (60, 0.03), (120, 0.0)):
        filtered, today = _mean_behind(rain, minute)
        assert filtered <= today + extra, minute


def test_a_20_min_dropout_mid_storm_is_reacquired():
    # readings None for ramp minutes 60-79; the call is through the 30th minute after they return
    filtered, _ = _mean_behind(STORM_RAIN, 80 + 30 + 1, dropout=range(START + 60, START + 80))
    assert abs(filtered) <= 0.03


def test_the_rate_follows_the_storm_and_reads_zero_on_a_flat_lake():
    inflow = _storm_inflow(STORM_RAIN)
    storm = [_estimate(_readings(STORM, noise), STORM_RAIN, inflow).rate_ft_per_h for noise in NOISES]
    flat = [_estimate(_readings(FLAT, noise), DRY).rate_ft_per_h for noise in NOISES]
    assert np.mean(storm) == pytest.approx(0.1, abs=0.02)
    assert np.mean(flat) == pytest.approx(0.0, abs=0.01)


def test_the_rate_an_hour_ago_is_the_rate_a_call_then_would_have_given():
    readings = _readings(STORM)
    assert _estimate(readings, STORM_RAIN, None, 60).rate_hour_ago_ft_per_h is None
    then = _estimate(readings, STORM_RAIN, None, N - 60).rate_ft_per_h
    assert _estimate(readings, STORM_RAIN).rate_hour_ago_ft_per_h == pytest.approx(then, abs=0.001)


def test_a_modelled_storm_that_never_arrives_leaves_the_level_near_the_gauge():
    # the model over-delivers (as it did 4.6x this fall): its storm's inflow, a flat gauge, in rain
    inflow = _storm_inflow(STORM_RAIN)
    level = np.mean([_estimate(_readings(FLAT, noise), STORM_RAIN, inflow).level_ft for noise in NOISES])
    assert level == pytest.approx(BASE, abs=0.02)


def test_unknown_rain_keeps_the_filter_wet_for_the_window_after_it():
    window = int(ART.lake_filter.wet_window_hours * 60)
    rain = [0.0] * 60 + [None] * 60 + [0.0] * (window + 60)
    wet = wetness(ART.lake_filter, rain)
    assert wet[59] == 0.0
    assert wet[119 + window - 1] == 1.0       # the gap's last minute is still in the window
    assert wet[119 + window] == 0.0


def test_non_finite_values_count_as_missing():
    readings = _readings(FLAT)
    readings[-10] = float("nan")
    rain = list(DRY)
    rain[-20] = float("nan")
    est = _estimate(readings, rain)
    assert np.isfinite(est.level_ft) and np.isfinite(est.rate_ft_per_h)


def test_a_1_ft_spike_moves_the_level_by_less_than_0_005_ft():
    readings = _readings(FLAT)
    spiked = list(readings)
    spiked[-5] += 1.0
    moved = _estimate(spiked, DRY).level_ft - _estimate(readings, DRY).level_ft
    assert abs(moved) < 0.005


@pytest.mark.parametrize("valid, expected", [(19, False), (20, True)])
def test_no_estimate_without_enough_recent_readings(valid, expected):
    readings = _readings(FLAT)
    for i in range(N - 30 + valid, N):
        readings[i] = None
    assert (_estimate(readings, DRY) is not None) is expected


def test_minute_grid_holds_the_last_value_maps_unknown_to_none_and_ends_at_now():
    t = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    rows = [(t + timedelta(minutes=3), "unknown"), (t + timedelta(minutes=1), "1.20"),
            (t + timedelta(minutes=4, seconds=20), "1.25")]
    grid = minute_grid(rows, now=t + timedelta(minutes=5, seconds=30), minutes=6)
    # minutes 12:00 .. 12:05; the 12:04:20 reading is held into 12:05
    assert grid == [None, 1.20, 1.20, None, None, 1.25]


def test_rain_by_minute_spreads_hours_and_the_partial_hour_and_marks_unknown_hours_none():
    now = datetime(2026, 11, 15, 12, 9, 30, tzinfo=timezone.utc)
    rain = rain_by_minute([0.6, None], partial_hour_in=0.1, now=now, minutes=10 + 60 + 60)
    assert rain[:60] == [pytest.approx(0.01)] * 60          # 10:00-10:59
    assert rain[60:120] == [None] * 60                      # 11:00-11:59, unknown
    assert rain[120:] == [pytest.approx(0.01)] * 10         # 12:00-12:09, 0.1 in so far
