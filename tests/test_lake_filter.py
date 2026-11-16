"""The lake filter (docs/plans/lake-filter.md, T1): synthetic 12 h minute series with noise that is
not only the filter's own model, compared against the truth and today's anchor."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from lake_rise import units
from lake_rise.artifact import load_artifact
from lake_rise.geometry import surface_area_acres
from lake_rise.lake_filter import estimate_lake, minute_grid, rain_by_minute
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
STORM_RAIN = [0.25 / 60 if i >= START else 0.0 for i in range(N)]
FLAT = np.full(N, BASE)
STORM = BASE + np.clip(np.arange(N) - START, 0, None) * RATE


def _noise():
    """White noise sd 0.05 plus offset steps holding a uniform +-0.05 ft for 10-40 min."""
    rng = np.random.default_rng(0)
    white = rng.normal(0.0, 0.05, N)
    steps = np.empty(N)
    i = 0
    while i < N:
        k = int(rng.integers(10, 41))
        steps[i:i + k] = rng.uniform(-0.05, 0.05)
        i += k
    return white + steps


NOISE = _noise()


def _readings(truth):
    return [float(v) for v in truth + NOISE]


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


def _behind(readings, rain, minute):
    """(filter, today's anchor) behind the truth, `minute` into the ramp, model blind."""
    k = START + minute
    est = _estimate(readings, rain, None, k)
    return STORM[k - 1] - est.level_ft, STORM[k - 1] - _anchor(readings, k)


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
    readings = _readings(STORM)
    inflow = _storm_inflow(STORM_RAIN)
    for minute in (30, 60, 120):
        est = _estimate(readings, STORM_RAIN, inflow, START + minute)
        assert est.level_ft == pytest.approx(STORM[START + minute - 1], abs=0.02)


@pytest.mark.parametrize("rain", [STORM_RAIN, [None] * N], ids=["in rain", "missing rain counts as wet"])
def test_a_blind_storm_stays_within_p12(rain):
    readings = _readings(STORM)
    for minute, extra in ((30, 0.03), (60, 0.03), (120, 0.0)):
        filtered, today = _behind(readings, rain, minute)
        assert filtered <= today + extra, minute


def test_a_20_min_dropout_mid_storm_is_reacquired():
    readings = _readings(STORM)
    for i in range(START + 60, START + 80):
        readings[i] = None
    k = START + 80 + 30 + 1         # through the 30th minute after readings return
    est = _estimate(readings, STORM_RAIN, None, k)
    assert est.level_ft == pytest.approx(STORM[k - 1], abs=0.03)


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
