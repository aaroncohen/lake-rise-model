"""Backtest: validate the model against real observed history.

Given a time window [t0, now], anchor the model to the OBSERVED lake level at T0,
step it forward using REAL observed rainfall (not a forecast), and compare predicted
vs actual gauge levels. Isolates model error from forecast error.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta
from typing import Any

from . import model
from .artifact import Artifact
from .factors import factor_breakdown
from .hourly import floor_hour, parse_ha_rows
from .scenarios import synthesize_scenarios


def level_history_to_hourly(
    states: list[dict], datum_offset: float
) -> dict[datetime, float]:
    """Parse HA lake-depth history rows into one absolute-elevation value per clock hour,
    taking the MEDIAN of all readings in each hour.

    A noisy gauge reports ~20-30 times an hour; the per-hour median denoises the series
    and rejects glitch spikes, instead of keeping a single arbitrary last-in-hour sample
    (which made the plotted actual line look far noisier than the signal). Absolute
    elevation = reading + datum_offset; non-float rows (unknown/unavailable) are skipped.

    Returns a dict keyed by tz-aware hour datetimes.
    """
    buckets: dict[datetime, list[float]] = {}
    for ts, reading in parse_ha_rows(states):
        buckets.setdefault(floor_hour(ts), []).append(reading + datum_offset)
    return {hour: statistics.median(vals) for hour, vals in buckets.items()}


def smoothed_anchor_elev(
    states: list[dict], datum_offset: float, at: datetime,
    window_hours: float = 1.0,
) -> float | None:
    """Noise-smoothed point-in-time elevation: the MEDIAN absolute elevation of the raw
    sensor readings in the trailing window ``[at - window_hours, at]``. Used for the LIVE
    "now" anchor, where there is no hourly bucket yet to median over; it denoises a single
    instantaneous reading and rejects glitch spikes. (The backtest's hourly actual line is
    already per-hour-median, so it doesn't need this.) A trailing window lags a fast rise
    by ~window/2; keep ``window_hours`` short for the safety-critical live path. Returns
    None if the window holds no parseable readings (caller falls back to a single sample)."""
    lo = at - timedelta(hours=window_hours)
    vals = [reading + datum_offset for ts, reading in parse_ha_rows(states) if lo <= ts <= at]
    return statistics.median(vals) if vals else None


def _centered_median_smooth(values: list[float], half: int = 1) -> list[float]:
    """Zero-lag centered rolling median (window = 2*half+1). For DISPLAY of the historical
    backtest actual line only: it denoises the heavy per-hour sensor jitter without the
    trailing lag a real-time anchor would suffer (the backtest is all in the past, so we can
    use a symmetric window). NOT used for metrics -- a centered median softens true storm
    peaks, which would unfairly flatter the model on exactly the comparison the backtest
    exists to make."""
    out: list[float] = []
    for i in range(len(values)):
        lo, hi = max(0, i - half), min(len(values), i + half + 1)
        out.append(round(statistics.median(values[lo:hi]), 3))
    return out


def run_backtest(
    art: Artifact,
    rain_hourly: list[float],
    rain_start: datetime,
    level_by_hour: dict[datetime, float],
    t0: datetime,
    now: datetime,
    control_elev: float,
    sm0: float | None = None,
    anchor_h0: float | None = None,
    state0: "model.State | None" = None,
) -> dict[str, Any]:
    """Run a backtest over [t0, now] using real observed rain and lake levels.

    Args:
        art: Model artifact.
        rain_hourly: Hourly rainfall (inches) starting at rain_start, covering
            the full window from rain_start through now.
        rain_start: Start datetime for rain_hourly (tz-aware).
        level_by_hour: Dict mapping tz-aware hour datetimes to absolute elevations
            (ft). Should cover at least T0 and the forward window.
        t0: Backtest start (tz-aware). The model is anchored to the observed
            lake level here.
        now: Backtest end (tz-aware).
        control_elev: Spillway control elevation (ft, absolute).
        sm0: Optional initial soil moisture override (inches).
        anchor_h0: Optional pre-computed T0 anchor elevation (e.g. a noise-smoothed
            trailing median, see ``smoothed_anchor_elev``). When given, it is used as the
            anchor instead of the single closest-hour sample; ``level_by_hour`` is still
            used for the actual-vs-predicted comparison.
        state0: Optional full T0 model state spun up from the recorded history (see
            ``antecedent.estimate_state``). When given it replaces the seasonal/hindcast
            spin-up, so every store -- especially the slow groundwater store -- reflects the
            real antecedent state; elevation is still pinned to the gauge ``h0``. Pure: the
            caller supplies the state; this function stays network-free.

    Returns a dict with keys: t0, now, hours, predicted, actual, rainfall_in,
    rain_total_in, metrics.

    Raises ValueError if no observed lake level is available near T0.
    """
    # --- find h0: observed elevation at T0 -------------------------------------
    t0_hour = floor_hour(t0)
    if not level_by_hour:
        raise ValueError("No observed lake levels available; cannot anchor the backtest.")

    if anchor_h0 is not None:
        # Caller supplied a smoothed anchor; trust it over a single instantaneous sample.
        h0 = anchor_h0
    else:
        # Fall back to the closest available hour to t0 in level_by_hour.
        best_key = min(level_by_hour.keys(), key=lambda k: abs((k - t0_hour).total_seconds()))
        if abs((best_key - t0_hour).total_seconds()) > 4 * 3600:
            raise ValueError(
                f"No observed lake level within 4 hours of T0 ({t0_hour.isoformat()}); "
                "cannot anchor the backtest."
            )
        h0 = level_by_hour[best_key]

    # --- slice rain into pre-T0 (spin-up) and forward windows ------------------
    rain_start_hour = floor_hour(rain_start)
    t0_hour_clamped = floor_hour(t0)
    now_hour = floor_hour(now)

    idx_t0 = max(0, round((t0_hour_clamped - rain_start_hour).total_seconds() / 3600))
    idx_now = max(idx_t0, round((now_hour - rain_start_hour).total_seconds() / 3600))

    pre = rain_hourly[:idx_t0]
    fwd = rain_hourly[idx_t0:idx_now]

    # --- spin-up: establish SM/S_if/S_agw/lag state at T0 ----------------------
    # Prefer a full state assimilated from the recorded history (any weather) over the seasonal
    # spin-up; the slow store's long memory means a ~10-day hindcast can't set it, and an
    # over-high seasonal seed is what makes a dry backtest drift upward toward equilibrium.
    if state0 is not None:
        state = state0.copy()
        state.h = h0                                  # trust the gauge at T0 for elevation
    elif pre:
        state, _ = model.hindcast(
            art, pre, h0=h0, start=rain_start_hour, control_elev=control_elev, sm0=sm0
        )
        state.h = h0
    else:
        state = model.initial_state(art, h0=h0, sm0=sm0, month=t0.month)

    # --- forward run: step model from T0 to now using real rain ----------------
    _, records = model.run(art, state, fwd, start=t0_hour_clamped, control_elev=control_elev)

    # Factor breakdown aligned to the forward records (NOT including T0 anchor).
    factors = factor_breakdown(art, records, h0)

    # Build predicted list: prepend the T0 anchor so predicted[0] is exactly h0.
    predicted = [{"valid_at": t0_hour_clamped.isoformat(), "elevation": round(h0, 3)}]
    for rec in records:
        predicted.append({"valid_at": rec.t.isoformat(), "elevation": round(rec.h, 3)})

    # --- model uncertainty band: low/high rainfall scenarios run from the T0 state.
    # Treats T0 as the issue time, so the band widens with lead like a real forecast;
    # lets us see whether the actual outcome falls within the model's band.
    def _trajectory(rain_series: list[float]) -> list[dict]:
        _, recs = model.run(art, state.copy(), rain_series, start=t0_hour_clamped, control_elev=control_elev)
        traj = [{"valid_at": t0_hour_clamped.isoformat(), "elevation": round(h0, 3)}]
        traj.extend({"valid_at": r.t.isoformat(), "elevation": round(r.h, 3)} for r in recs)
        return traj

    band = {s.name: s.hourly_in for s in synthesize_scenarios(art, fwd, month=t0.month)}
    predicted_low = _trajectory(band["low"])
    predicted_high = _trajectory(band["high"])

    # --- actual: observed levels over [t0, now] --------------------------------
    actual: list[dict] = []
    hours_in_window = max(0, round((now_hour - t0_hour_clamped).total_seconds() / 3600))
    for i in range(hours_in_window + 1):
        hour_key = t0_hour_clamped + timedelta(hours=i)
        if hour_key in level_by_hour:
            actual.append({
                "valid_at": hour_key.isoformat(),
                "elevation": round(level_by_hour[hour_key], 3),
            })

    # --- metrics over hours common to both predicted and actual ----------------
    pred_by_time = {p["valid_at"]: p["elevation"] for p in predicted}
    actual_by_time = {a["valid_at"]: a["elevation"] for a in actual}
    common_times = sorted(set(pred_by_time) & set(actual_by_time))

    metrics: dict[str, Any] = {}
    if common_times:
        errors = [pred_by_time[t] - actual_by_time[t] for t in common_times]
        sq_errors = [e * e for e in errors]
        abs_errors = [abs(e) for e in errors]

        rmse = round(math.sqrt(sum(sq_errors) / len(sq_errors)), 3)
        mae = round(sum(abs_errors) / len(abs_errors), 3)
        max_err = round(max(abs_errors), 3)
        final_err = round(errors[-1], 3)

        # Peak elevation and timing in predicted and actual.
        pred_peak_elev = max(p["elevation"] for p in predicted)
        pred_peak_time = next(p["valid_at"] for p in predicted if p["elevation"] == pred_peak_elev)
        actual_elevs = [a["elevation"] for a in actual]
        actual_peak_elev = max(actual_elevs) if actual_elevs else h0
        actual_peak_time = next(a["valid_at"] for a in actual if a["elevation"] == actual_peak_elev)

        peak_err = round(pred_peak_elev - actual_peak_elev, 3)
        pred_peak_dt = datetime.fromisoformat(pred_peak_time)
        actual_peak_dt = datetime.fromisoformat(actual_peak_time)
        peak_timing_err_h = round(
            (pred_peak_dt - actual_peak_dt).total_seconds() / 3600.0, 2
        )

        tol = art.validation_targets
        metrics = {
            "rmse_ft": rmse,
            "mae_ft": mae,
            "max_err_ft": max_err,
            "final_err_ft": final_err,
            "pred_peak_elev_ft": round(pred_peak_elev, 3),
            "pred_peak_time": pred_peak_time,
            "actual_peak_elev_ft": round(actual_peak_elev, 3),
            "actual_peak_time": actual_peak_time,
            "peak_err_ft": peak_err,
            "peak_timing_err_h": peak_timing_err_h,
            "peak_within_target": abs(peak_err) <= tol.storm_peak_tolerance_ft,
            "timing_within_target": abs(peak_timing_err_h) <= tol.storm_timing_tolerance_hr,
        }
    else:
        metrics = {
            "rmse_ft": None,
            "mae_ft": None,
            "max_err_ft": None,
            "final_err_ft": None,
            "pred_peak_elev_ft": None,
            "pred_peak_time": None,
            "actual_peak_elev_ft": None,
            "actual_peak_time": None,
            "peak_err_ft": None,
            "peak_timing_err_h": None,
            "peak_within_target": None,
            "timing_within_target": None,
        }

    # Display-only: a zero-lag centered-median smooth of the (heavily noisy) gauge line.
    # Metrics above use the raw per-hour-median `actual`; this is purely for a cleaner plot.
    actual_smoothed = [
        {"valid_at": a["valid_at"], "elevation": e}
        for a, e in zip(actual, _centered_median_smooth([a["elevation"] for a in actual]))
    ]

    return {
        "t0": t0_hour_clamped.isoformat(),
        "now": now_hour.isoformat(),
        "hours": hours_in_window,
        "predicted": predicted,
        "predicted_low": predicted_low,
        "predicted_high": predicted_high,
        "actual": actual,
        "actual_smoothed": actual_smoothed,
        "rainfall_in": list(fwd),
        "rain_total_in": round(sum(fwd), 2),
        "metrics": metrics,
        "factors": factors,
        "seed_s_agw_in": round(state.s_agw, 4),
        "gw_seed_source": "assimilated" if state0 is not None else "seasonal_default",
    }
