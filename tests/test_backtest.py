"""Pure backtest unit tests: no network calls."""

from datetime import datetime, timedelta, timezone

import pytest

from lake_rise import model
from lake_rise.backtest import level_history_to_hourly, run_backtest, smoothed_anchor_elev


# ---------------------------------------------------------------------------
# level_history_to_hourly
# ---------------------------------------------------------------------------

def test_level_history_to_hourly_medians_each_hour():
    base = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    states = [
        {"state": "1.50", "last_changed": base.isoformat()},                            # hour 12
        {"state": "1.55", "last_changed": (base + timedelta(minutes=20)).isoformat()},  # hour 12
        {"state": "1.90", "last_changed": (base + timedelta(minutes=40)).isoformat()},  # hour 12 (spike)
        {"state": "1.60", "last_changed": (base + timedelta(hours=1)).isoformat()},     # hour 13
    ]
    result = level_history_to_hourly(states, datum_offset=340.0)
    hour12 = base.replace(minute=0, second=0, microsecond=0)
    hour13 = hour12 + timedelta(hours=1)
    # median of [1.50, 1.55, 1.90] + offset = 1.55 + 340 -> the 1.90 spike is rejected
    assert result[hour12] == pytest.approx(341.55)
    assert result[hour13] == pytest.approx(341.60)


def test_smoothed_anchor_elev_medians_window_and_rejects_spikes():
    """The anchor is the median over a trailing window: it denoises and ignores both a
    glitch spike and samples older than the window."""
    t0 = datetime(2026, 4, 10, 6, 0, tzinfo=timezone.utc)
    off = 338.375

    def row(mins_before, val):
        return {"state": str(val), "last_changed": (t0 - timedelta(minutes=mins_before)).isoformat()}

    states = [
        row(10, 1.28), row(30, 1.32), row(60, 1.30), row(90, 1.31), row(150, 1.29),
        row(20, 5.0),    # sensor glitch spike -> median ignores it
        row(400, 0.50),  # ~6.7 h old -> outside the 3 h window
    ]
    h = smoothed_anchor_elev(states, off, t0, window_hours=3.0)
    # median of in-window readings [1.28,1.29,1.30,1.31,1.32,5.0] = 1.305, + offset
    assert h == pytest.approx(1.305 + off, abs=1e-6)
    # empty / no-in-window -> None (caller falls back)
    assert smoothed_anchor_elev([], off, t0) is None
    assert smoothed_anchor_elev([row(400, 1.3)], off, t0, window_hours=3.0) is None


def test_centered_median_smooth_zero_lag_and_rejects_spikes():
    from lake_rise.backtest import _centered_median_smooth
    # interior of a linear ramp is unchanged -> zero lag, no attenuation on straight runs
    assert _centered_median_smooth([1.0, 1.1, 1.2, 1.3, 1.4], half=1)[1:4] == [1.1, 1.2, 1.3]
    # a one-hour spike is replaced by its local median
    assert _centered_median_smooth([1.0, 1.0, 5.0, 1.0, 1.0], half=1)[2] == 1.0


def test_run_backtest_honors_anchor_h0_override(art):
    """When a smoothed anchor is supplied, the predicted line starts there (not at the
    single closest-hour sample)."""
    t0 = datetime(2026, 4, 10, 6, 0, tzinfo=timezone.utc)
    now = t0 + timedelta(hours=6)
    rain_start = t0 - timedelta(hours=24)
    control = art.stop_logs.control_elev(3)
    fwd = [0.0] * 6
    rain = [0.0] * 24 + fwd

    state, _ = model.hindcast(art, [0.0] * 24, h0=339.5, start=rain_start, control_elev=control)
    state.h = 339.5
    _, recs = model.run(art, state, fwd, start=t0, control_elev=control)
    level_by_hour = _make_level_by_hour(recs, 339.5, t0)

    res = run_backtest(art, rain, rain_start, level_by_hour, t0, now, control, anchor_h0=340.10)
    assert res["predicted"][0]["elevation"] == pytest.approx(340.10, abs=1e-6)


def test_level_history_to_hourly_skips_non_floats():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    states = [
        {"state": "unknown", "last_changed": base.isoformat()},
        {"state": "unavailable", "last_changed": base.isoformat()},
        {"state": "1.80", "last_changed": (base + timedelta(hours=1)).isoformat()},
    ]
    result = level_history_to_hourly(states, datum_offset=340.0)
    hour10 = base.replace(minute=0, second=0, microsecond=0)
    hour11 = hour10 + timedelta(hours=1)
    assert hour10 not in result
    assert result[hour11] == pytest.approx(341.80)


def test_level_history_to_hourly_applies_datum_offset():
    base = datetime(2026, 4, 1, 6, 0, tzinfo=timezone.utc)
    states = [{"state": "2.00", "last_changed": base.isoformat()}]
    result = level_history_to_hourly(states, datum_offset=338.5)
    hour = base.replace(minute=0, second=0, microsecond=0)
    assert result[hour] == pytest.approx(340.50)


def test_level_history_to_hourly_floors_to_hour():
    """Timestamps anywhere within an hour map to the same hour key."""
    base = datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc)
    states = [
        {"state": "1.0", "last_changed": (base + timedelta(minutes=5)).isoformat()},
        {"state": "1.1", "last_changed": (base + timedelta(minutes=55)).isoformat()},
        {"state": "1.2", "last_changed": (base + timedelta(minutes=59, seconds=59)).isoformat()},
    ]
    result = level_history_to_hourly(states, datum_offset=0.0)
    assert len(result) == 1  # all three in the same hour
    assert result[base] == pytest.approx(1.1)  # median of [1.0, 1.1, 1.2]


def test_level_history_to_hourly_empty():
    result = level_history_to_hourly([], datum_offset=340.0)
    assert result == {}


# ---------------------------------------------------------------------------
# run_backtest: perfect match (model predicts its own truth)
# ---------------------------------------------------------------------------

def _make_level_by_hour(records: list, h0: float, t0_hour: datetime) -> dict[datetime, float]:
    """Build level_by_hour from model records (truth trajectory)."""
    by_hour: dict[datetime, float] = {t0_hour: h0}
    for rec in records:
        hour = rec.t.replace(minute=0, second=0, microsecond=0)
        by_hour[hour] = rec.h
    return by_hour


def test_run_backtest_perfect_match(art):
    """When predicted and actual come from the same model run, metrics are ~zero."""
    t0 = datetime(2026, 4, 10, 6, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 10, 18, 0, tzinfo=timezone.utc)
    rain_start = t0 - timedelta(hours=24)
    h0 = 339.5
    control_elev = art.stop_logs.control_elev(3)

    # Build a forward rain series (6h dry, then 6h of 0.2 in/hr)
    fwd_rain = [0.0] * 6 + [0.2] * 6
    pre_rain = [0.0] * 24

    # Run the model ourselves to produce the "truth" trajectory. Spin up exactly the way
    # run_backtest does (hindcast over the pre-T0 window, then re-anchor elevation to h0),
    # so the truth and the backtest share the same internal state at T0 -- otherwise the
    # seeded groundwater store would drain differently over the spin-up and the two would
    # diverge slightly.
    state, _ = model.hindcast(art, pre_rain, h0=h0, start=rain_start, control_elev=control_elev)
    state.h = h0
    _, fwd_records = model.run(art, state, fwd_rain, start=t0, control_elev=control_elev)

    level_by_hour = _make_level_by_hour(fwd_records, h0, t0)

    # Now call run_backtest with the same rain and the truth as observed levels.
    rain_hourly = pre_rain + fwd_rain
    result = run_backtest(
        art, rain_hourly, rain_start, level_by_hour, t0, now, control_elev
    )

    assert "predicted" in result
    assert "actual" in result
    assert "metrics" in result
    metrics = result["metrics"]
    # Because the same model run is used for both predicted and actual, RMSE ≈ 0.
    assert metrics["rmse_ft"] == pytest.approx(0.0, abs=0.001)
    assert metrics["mae_ft"] == pytest.approx(0.0, abs=0.001)
    assert metrics["peak_err_ft"] == pytest.approx(0.0, abs=0.001)


def test_run_backtest_offset_actual_reflects_in_metrics(art):
    """Shift actual elevations (post-T0 hours only) to introduce known error."""
    t0 = datetime(2026, 4, 15, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
    rain_start = t0 - timedelta(hours=24)
    h0 = 339.6
    control_elev = art.stop_logs.control_elev(3)

    fwd_rain = [0.1] * 12
    pre_rain = [0.0] * 24
    rain_hourly = pre_rain + fwd_rain

    # Build truth trajectory. Spin up exactly the way run_backtest does (hindcast over
    # the pre-T0 window, re-anchor elevation to h0) so truth and backtest share the same
    # internal state at T0 and the only divergence is the injected offset below.
    state, _ = model.hindcast(art, pre_rain, h0=h0, start=rain_start, control_elev=control_elev)
    state.h = h0
    _, records = model.run(art, state, fwd_rain, start=t0, control_elev=control_elev)
    level_by_hour = _make_level_by_hour(records, h0, t0)

    # Shift only the post-T0 observed levels up by a constant (keep T0 anchor the same
    # so both predicted and actual start from h0, then diverge by the offset).
    offset = 0.05
    t0_hour = t0.replace(minute=0, second=0, microsecond=0)
    shifted = {
        k: (v + offset if k != t0_hour else v) for k, v in level_by_hour.items()
    }

    result = run_backtest(
        art, rain_hourly, rain_start, shifted, t0, now, control_elev
    )
    metrics = result["metrics"]
    # predicted is lower than actual by the offset -> negative final error and RMSE > 0
    assert metrics["final_err_ft"] < 0.0       # predicted ended below shifted actual
    assert metrics["rmse_ft"] > 0.0
    assert abs(metrics["final_err_ft"]) == pytest.approx(offset, abs=0.01)


def test_run_backtest_structure(art):
    """Verify the returned dict has all expected top-level keys."""
    t0 = datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 20, 6, 0, tzinfo=timezone.utc)
    rain_start = t0 - timedelta(hours=12)
    h0 = 339.5
    control_elev = art.stop_logs.control_elev(3)
    rain_hourly = [0.0] * 12 + [0.05] * 6
    level_by_hour = {t0: h0}  # only anchor point
    for i in range(1, 7):
        level_by_hour[t0 + timedelta(hours=i)] = h0 + 0.01 * i

    result = run_backtest(art, rain_hourly, rain_start, level_by_hour, t0, now, control_elev)

    required_keys = {"t0", "now", "hours", "predicted", "actual", "rainfall_in",
                     "rain_total_in", "metrics"}
    assert required_keys <= set(result.keys())

    # predicted[0] is the T0 anchor
    assert result["predicted"][0]["valid_at"] == t0.replace(minute=0, second=0, microsecond=0).isoformat()
    assert result["predicted"][0]["elevation"] == pytest.approx(h0)

    # predicted and actual are lists of {valid_at, elevation}
    for pt in result["predicted"]:
        assert "valid_at" in pt and "elevation" in pt
    for pt in result["actual"]:
        assert "valid_at" in pt and "elevation" in pt

    # display-only smoothed actual is aligned 1:1 with the raw actual
    assert len(result["actual_smoothed"]) == len(result["actual"])
    assert ([p["valid_at"] for p in result["actual_smoothed"]]
            == [p["valid_at"] for p in result["actual"]])

    metric_keys = {
        "rmse_ft", "mae_ft", "max_err_ft", "final_err_ft",
        "pred_peak_elev_ft", "pred_peak_time", "actual_peak_elev_ft", "actual_peak_time",
        "peak_err_ft", "peak_timing_err_h", "peak_within_target", "timing_within_target",
    }
    assert metric_keys <= set(result["metrics"].keys())


def test_run_backtest_includes_ordered_band(art):
    """predicted_low / predicted_high bracket the predicted line (more rain -> higher)."""
    t0 = datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    rain_start = t0 - timedelta(hours=12)
    h0 = 339.5
    control_elev = art.stop_logs.control_elev(3)
    rain_hourly = [0.0] * 12 + [0.1] * 12   # real rain over the forward window
    level_by_hour = {t0 + timedelta(hours=i): h0 for i in range(0, 13)}

    r = run_backtest(art, rain_hourly, rain_start, level_by_hour, t0, now, control_elev)
    assert len(r["predicted_low"]) == len(r["predicted"]) == len(r["predicted_high"])
    # all three start exactly on the gauge at T0
    assert r["predicted_low"][0]["elevation"] == pytest.approx(h0)
    assert r["predicted_high"][0]["elevation"] == pytest.approx(h0)

    # The band must bracket the median at EVERY hour, not just the endpoints -- the low/median/high
    # trajectories all integrate from the one T0 spin-up state (model.run returns a fresh State and
    # does not mutate its input). If the band ever re-started from the median's post-forward end
    # state, the lines would cross mid-window; asserting per-hour containment locks that out.
    by_time = lambda pts: {p["valid_at"]: p["elevation"] for p in pts}
    lo_t, mid_t, hi_t = by_time(r["predicted_low"]), by_time(r["predicted"]), by_time(r["predicted_high"])
    assert set(lo_t) == set(mid_t) == set(hi_t)               # same hour grid, aligned
    for t, mid in mid_t.items():
        assert lo_t[t] - 1e-6 <= mid <= hi_t[t] + 1e-6, f"band does not bracket median at {t}"
    assert hi_t[max(hi_t)] > lo_t[max(lo_t)]                  # a real rain event -> non-degenerate band


def test_run_backtest_no_level_raises():
    """No observed levels -> ValueError."""
    from lake_rise.artifact import load_artifact
    art = load_artifact()
    t0 = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    now = t0 + timedelta(hours=6)
    with pytest.raises(ValueError, match="No observed lake"):
        run_backtest(art, [0.0] * 6, t0 - timedelta(hours=6), {}, t0, now, 339.675)


def test_run_backtest_predicted_starts_at_h0(art):
    """predicted[0] elevation always equals observed h0 (T0 anchor)."""
    t0 = datetime(2026, 3, 5, 0, 0, tzinfo=timezone.utc)
    now = t0 + timedelta(hours=8)
    rain_start = t0 - timedelta(hours=6)
    h0 = 340.123
    control_elev = art.stop_logs.control_elev(0)
    rain_hourly = [0.0] * 6 + [0.0] * 8
    level_by_hour = {t0: h0}

    result = run_backtest(art, rain_hourly, rain_start, level_by_hour, t0, now, control_elev)
    assert result["predicted"][0]["elevation"] == pytest.approx(h0, abs=1e-6)


def test_run_backtest_includes_factors(art):
    """run_backtest result includes a 'factors' dict aligned to the forward records."""
    t0 = datetime(2026, 4, 10, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc)
    rain_start = t0 - timedelta(hours=12)
    h0 = 339.5
    control_elev = art.stop_logs.control_elev(3)
    # 12h spin-up + 2h dry + 4h rain + 2h dry
    rain_hourly = [0.0] * 12 + [0.0] * 2 + [0.15] * 4 + [0.0] * 2
    level_by_hour = {t0 + timedelta(hours=i): h0 for i in range(9)}

    result = run_backtest(art, rain_hourly, rain_start, level_by_hour, t0, now, control_elev)

    assert "factors" in result, "'factors' key missing from run_backtest result"
    fb = result["factors"]
    assert fb is not None

    required_keys = {"valid_at", "per_hour_ft", "cumulative_ft", "net_ft",
                     "net_cumulative_ft", "state", "totals_ft"}
    assert required_keys <= set(fb.keys())

    # factors arrays should have one entry per FORWARD step (not including the T0 anchor)
    # forward window = 8 hours (t0 -> now)
    assert len(fb["valid_at"]) == 8
    assert len(fb["net_ft"]) == 8
    assert len(fb["per_hour_ft"]["watershed_runoff"]) == 8
    assert len(fb["per_hour_ft"]["spillway"]) == 8

    # Sign conventions hold
    for i in range(8):
        assert fb["per_hour_ft"]["watershed_runoff"][i] >= 0.0
        assert fb["per_hour_ft"]["direct_rain"][i] >= 0.0
        assert fb["per_hour_ft"]["spillway"][i] <= 0.0

    # Totals dict has the right keys
    totals_keys = {"watershed_runoff", "baseflow", "direct_rain", "spillway", "net"}
    assert totals_keys <= set(fb["totals_ft"].keys())
