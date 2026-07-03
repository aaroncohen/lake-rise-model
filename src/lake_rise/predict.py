"""The predictor: a pure function of (bundle, artifact). Spins up internal state
via a trailing-rainfall hindcast (trusting the live gauge for elevation), then
projects each scenario forward and shapes the output for Home Assistant (spec 6).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from pydantic import BaseModel

from . import model
from .artifact import Artifact
from .bundle import InputBundle
from .factors import factor_breakdown
from .geometry import control_elev_for_stop_logs, in_valid_range

# The low/median/high scenarios are the ~10th/50th/90th percentiles of the rainfall
# band; peak elevation is monotonic in rainfall, so those three peaks are the same
# quantiles of peak elevation. We interpolate a CDF through them (linear interior,
# log-linear tails) to get a smooth P(peak >= threshold) instead of crude fixed
# weights. A wider band (longer lead, summer) spreads the high peak further out,
# which fattens the upper tail and makes the risk lean wetter when the forecast is
# uncertain -- the desired behavior. NOTE: because the low/high branches are
# comonotonic per-hour ratios (see scenarios.py), summing them over a storm gives an
# *upper bound* on the dispersion of the total/peak, not the true 10th/90th of the
# total -- so these quantile labels are conservative-wide until a logged
# forecast-vs-gauge fit replaces the synthetic band (spec 3.5).
_SCENARIO_QUANTILE = {"low": 0.10, "median": 0.50, "high": 0.90}


def _exceedance_probability(points: list[tuple[float, float]], threshold: float) -> float:
    """P(peak >= threshold) given quantile points (peak_elevation, cdf).

    Piecewise-linear CDF in the *interior* (between the lowest and highest support
    elevations, where the synthetic quantiles actually anchor it), with log-linear
    (exponential-survival) *tails* outside that range. The tails decay smoothly --
    survival -> 0 above, -> 1 below -- and never hit a hard zero. Each tail's
    heaviness is set by the spacing of the nearest interior segment, so a wider
    (more uncertain) band yields a fatter upper tail and a genuinely higher chance
    of crossing a threshold that sits *above* the high scenario. That under-forecast
    upper tail is the dangerous direction for the EAP thresholds, so surfacing it --
    rather than clamping it to 0 -- is the point (see docs/forecast-uncertainty.md).
    """
    pts = sorted(points)
    # Collapse duplicate elevations (e.g. band off -> all three equal); keep higher cdf.
    dedup: list[tuple[float, float]] = []
    for e, p in pts:
        if dedup and e == dedup[-1][0]:
            dedup[-1] = (e, max(dedup[-1][1], p))
        else:
            dedup.append((e, p))
    pts = dedup
    if len(pts) == 1:                                   # no spread -> step function
        return 1.0 if threshold <= pts[0][0] else 0.0

    x_bot, p_bot = pts[0]
    x_top, p_top = pts[-1]

    if threshold >= x_top:                              # upper tail: exponential survival -> 0
        s_top = 1.0 - p_top                             # survival at the highest peak
        if s_top <= 0.0:
            return 0.0
        x_prev, p_prev = pts[-2]
        s_prev = 1.0 - p_prev
        if s_prev <= s_top:                             # degenerate -> clamped-linear fallback
            cdf = p_top + (p_top - p_prev) * (threshold - x_top) / (x_top - x_prev)
            return max(0.0, min(1.0, 1.0 - cdf))
        scale = (x_top - x_prev) / math.log(s_prev / s_top)
        return s_top * math.exp(-(threshold - x_top) / scale)

    if threshold <= x_bot:                              # lower tail: exponential cdf -> 0 (survival -> 1)
        if p_bot <= 0.0:
            return 1.0
        x_next, p_next = pts[1]
        if p_next <= p_bot:                             # degenerate -> clamped-linear fallback
            cdf = p_bot + (p_next - p_bot) * (threshold - x_bot) / (x_next - x_bot)
            return max(0.0, min(1.0, 1.0 - cdf))
        scale = (x_next - x_bot) / math.log(p_next / p_bot)
        cdf = p_bot * math.exp(-(x_bot - threshold) / scale)
        return max(0.0, min(1.0, 1.0 - cdf))

    # interior: piecewise-linear CDF through the support points
    (x0, p0), (x1, p1) = next((a, b) for a, b in zip(pts, pts[1:]) if a[0] <= threshold <= b[0])
    cdf = p0 + (p1 - p0) * (threshold - x0) / (x1 - x0)
    return max(0.0, min(1.0, 1.0 - cdf))


class TrajectoryPoint(BaseModel):
    valid_at: datetime
    elevation: float


class ScenarioResult(BaseModel):
    name: str
    trajectory: list[TrajectoryPoint]
    peak_elevation: float
    hours_to_crest: float | None
    hours_to_early_warning: float | None = None
    hours_to_bridge_deck: float | None = None


class ThresholdProbability(BaseModel):
    threshold_abs_ft: float
    label: str
    p_cross_within_horizon: float


class PredictionResult(BaseModel):
    # Flat headline fields -> one HA entity each (spec 6).
    generated_at: datetime
    model_version: str
    horizon_hours: int
    current_elevation: float
    freeboard_ft: float
    hours_to_crest_high_scenario: float | None
    p_cross_341: float
    p_cross_crest: float
    p_cross_bridge_deck: float = 0.0   # P(bridge-deck overtopping / road closure) within horizon
    data_fresh: bool
    # True when any scenario peak leaves the elevation band the geometry fits were
    # anchored to (geometry.valid_elev_range_ft). Above it the stage-area/storage
    # curves are extrapolated into the never-gauged overtopping regime, so the
    # elevation numbers are directional, not measured-range accurate (spec: flag,
    # don't clamp). Defaults False so existing constructors are unaffected.
    peak_outside_validated_geometry: bool = False

    # Hindcast end-state (soil moisture bucket and interflow storage).
    # Populated in both the hindcast and the initial-state branch.
    state_sm_in: float = 0.0
    state_s_if_in: float = 0.0

    # Nested detail for the dashboard.
    scenarios: list[ScenarioResult]
    threshold_probabilities: list[ThresholdProbability]
    input_summary: dict
    factors: dict | None = None


def _hours_to_crest(start: datetime, points: list[TrajectoryPoint], crest: float) -> float | None:
    for p in points:
        if p.elevation >= crest:
            return (p.valid_at - start).total_seconds() / 3600.0
    return None


def predict(bundle: InputBundle, art: Artifact) -> PredictionResult:
    as_of = bundle.as_of
    control_elev = control_elev_for_stop_logs(art.stop_logs, bundle.stop_log_count)
    crest = art.thresholds_abs_ft.dam_crest
    early_warning = art.thresholds_abs_ft.early_warning
    bridge_deck = art.thresholds_abs_ft.bridge_deck

    # --- spin up internal state -------------------------------------------------
    if bundle.initial_sm_in is not None or not bundle.trailing_rainfall_in:
        end_state = model.initial_state(
            art, h0=bundle.current_elevation_abs_ft,
            sm0=bundle.initial_sm_in, s_if0=bundle.initial_s_if_in, month=as_of.month,
        )
    else:
        hind_start = as_of - timedelta(hours=len(bundle.trailing_rainfall_in))
        end_state, _ = model.hindcast(
            art, bundle.trailing_rainfall_in, h0=bundle.current_elevation_abs_ft,
            start=hind_start, control_elev=control_elev, s_if0=bundle.initial_s_if_in,
        )
        # Trust the measured gauge for elevation; the hindcast only seeds SM/S_if/lag.
        end_state.h = bundle.current_elevation_abs_ft

    # --- project each scenario forward -----------------------------------------
    scenarios: list[ScenarioResult] = []
    median_records: list | None = None
    for sc in bundle.forecast_scenarios:
        records = model.forecast(art, end_state, sc.hourly_in, start=as_of, control_elev=control_elev)
        if sc.name == "median":
            median_records = records
        points = [TrajectoryPoint(valid_at=r.t, elevation=r.h) for r in records]
        peak = max((p.elevation for p in points), default=bundle.current_elevation_abs_ft)
        scenarios.append(ScenarioResult(
            name=sc.name, trajectory=points, peak_elevation=peak,
            hours_to_crest=_hours_to_crest(as_of, points, crest),
            hours_to_early_warning=_hours_to_crest(as_of, points, early_warning),
            hours_to_bridge_deck=(_hours_to_crest(as_of, points, bridge_deck)
                                  if bridge_deck is not None else None),
        ))

    # --- factor breakdown on the median scenario --------------------------------
    factors: dict | None = None
    if median_records is not None:
        factors = factor_breakdown(art, median_records, end_state.h)

    # --- threshold-crossing probabilities --------------------------------------
    by_name = {s.name: s for s in scenarios}
    quantile_points = [(by_name[n].peak_elevation, q)
                       for n, q in _SCENARIO_QUANTILE.items() if n in by_name]

    def p_cross(threshold: float) -> float:
        return round(_exceedance_probability(quantile_points, threshold), 3)

    th = art.thresholds_abs_ft
    threshold_probs = [
        ThresholdProbability(threshold_abs_ft=th.early_warning, label="early_warning",
                             p_cross_within_horizon=p_cross(th.early_warning)),
        ThresholdProbability(threshold_abs_ft=th.dam_crest, label="dam_crest",
                             p_cross_within_horizon=p_cross(th.dam_crest)),
    ]
    if th.bridge_deck is not None:
        threshold_probs.append(
            ThresholdProbability(threshold_abs_ft=th.bridge_deck, label="bridge_deck",
                                 p_cross_within_horizon=p_cross(th.bridge_deck)))

    # Flag when the projection climbs out of the geometry's validated band (into the
    # extrapolated overtopping regime). Estimates are still returned; the flag just
    # marks them as out-of-validated-range.
    peak_outside = any(
        not in_valid_range(art.geometry, s.peak_elevation) for s in scenarios
    )

    high = by_name.get("high")
    return PredictionResult(
        generated_at=as_of,
        model_version=art.version,
        horizon_hours=bundle.horizon_hours,
        current_elevation=bundle.current_elevation_abs_ft,
        freeboard_ft=crest - bundle.current_elevation_abs_ft,
        hours_to_crest_high_scenario=high.hours_to_crest if high else None,
        p_cross_341=p_cross(th.early_warning),
        p_cross_crest=p_cross(th.dam_crest),
        p_cross_bridge_deck=p_cross(th.bridge_deck) if th.bridge_deck is not None else 0.0,
        data_fresh=not bundle.rainfall_has_gaps,
        peak_outside_validated_geometry=peak_outside,
        state_sm_in=end_state.sm,
        state_s_if_in=end_state.s_if,
        scenarios=scenarios,
        threshold_probabilities=threshold_probs,
        input_summary={
            "stop_log_count": bundle.stop_log_count,
            "control_elev_ft": control_elev,
            "trailing_rain_hours": len(bundle.trailing_rainfall_in),
            "trailing_rain_total_in": round(sum(bundle.trailing_rainfall_in), 3),
        },
        factors=factors,
    )
