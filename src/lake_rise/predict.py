"""The predictor: a pure function of (bundle, artifact). Spins up internal state
via a trailing-rainfall hindcast (trusting the live gauge for elevation), then
projects each scenario forward and shapes the output for Home Assistant (spec 6).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from . import model
from .artifact import Artifact
from .bundle import InputBundle
from .geometry import control_elev_for_stop_logs

# Scenario weights for crude threshold-crossing probabilities (low/median/high band).
_SCENARIO_WEIGHTS = {"low": 0.25, "median": 0.50, "high": 0.25}


class TrajectoryPoint(BaseModel):
    valid_at: datetime
    elevation: float


class ScenarioResult(BaseModel):
    name: str
    trajectory: list[TrajectoryPoint]
    peak_elevation: float
    hours_to_crest: float | None


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
    data_fresh: bool

    # Nested detail for the dashboard.
    scenarios: list[ScenarioResult]
    threshold_probabilities: list[ThresholdProbability]
    input_summary: dict


def _hours_to_crest(start: datetime, points: list[TrajectoryPoint], crest: float) -> float | None:
    for p in points:
        if p.elevation >= crest:
            return (p.valid_at - start).total_seconds() / 3600.0
    return None


def predict(bundle: InputBundle, art: Artifact) -> PredictionResult:
    as_of = bundle.as_of
    control_elev = control_elev_for_stop_logs(art.stop_logs, bundle.stop_log_count)
    crest = art.thresholds_abs_ft.dam_crest

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
    for sc in bundle.forecast_scenarios:
        records = model.forecast(art, end_state, sc.hourly_in, start=as_of, control_elev=control_elev)
        points = [TrajectoryPoint(valid_at=r.t, elevation=r.h) for r in records]
        peak = max((p.elevation for p in points), default=bundle.current_elevation_abs_ft)
        scenarios.append(ScenarioResult(
            name=sc.name, trajectory=points, peak_elevation=peak,
            hours_to_crest=_hours_to_crest(as_of, points, crest),
        ))

    # --- threshold-crossing probabilities --------------------------------------
    by_name = {s.name: s for s in scenarios}

    def p_cross(threshold: float) -> float:
        return sum(
            w for name, w in _SCENARIO_WEIGHTS.items()
            if name in by_name and by_name[name].peak_elevation >= threshold
        )

    th = art.thresholds_abs_ft
    threshold_probs = [
        ThresholdProbability(threshold_abs_ft=th.early_warning, label="early_warning",
                             p_cross_within_horizon=p_cross(th.early_warning)),
        ThresholdProbability(threshold_abs_ft=th.dam_crest, label="dam_crest",
                             p_cross_within_horizon=p_cross(th.dam_crest)),
    ]

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
        data_fresh=not bundle.rainfall_has_gaps,
        scenarios=scenarios,
        threshold_probabilities=threshold_probs,
        input_summary={
            "stop_log_count": bundle.stop_log_count,
            "control_elev_ft": control_elev,
            "trailing_rain_hours": len(bundle.trailing_rainfall_in),
            "trailing_rain_total_in": round(sum(bundle.trailing_rainfall_in), 3),
        },
    )
