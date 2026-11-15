"""The field view's summary: a pure assembly of a prediction, its bundle, the alert decision
and the gauge facts into the page's JSON. Everything an operator sees is in stick height
(absolute - ``datum.sensor_to_absolute_offset_ft``); chances come from the prediction, so the
page and the emails agree."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel

from .alerting.eap import active_eap_rank, eap_level
from .alerting.rules import AlertDecision
from .artifact import Artifact
from .bundle import InputBundle
from .observed import GaugeObservation
from .predict import PredictionResult, scenario_rain

# Under this magnitude (ft/h) the lake reads "steady".
_STEADY_FT_PER_H = 0.03
_RAIN_BLOCK_HOURS = 6
# The model level each alert-ladder threshold label counts at.
_LEVEL_FOR_THRESHOLD = {"early_warning": "early_warning", "dam_crest": "overtop",
                        "bridge_deck": "bridge"}


class FieldLevel(BaseModel):
    key: Literal["early_warning", "eap_plastic", "overtop", "bridge"]
    label: str                       # "Warning" | "Plastic" | "Overtop" | "Bridge"
    stick_ft: float
    band_top_ft: float | None        # 3.90 / 4.40 for the overtopping bands, else None
    pct: int | None                  # chance of reaching it, 0-100 (None: no forecast, or Plastic in fallback)
    reached: bool                    # the lake is already at or above it (absolute frame)


class FieldStatus(BaseModel):
    level: str | None                # ladder name ("WATCH") or None at rank 0
    rank: int                        # ladder rank, 0 = normal
    headline: str | None             # the active level's threshold and its chance ("Overtop 22%")
    observed_eap: dict | None        # {"title": str, "gauge_ft": float, "color": str}


class FieldLake(BaseModel):
    now_ft: float                    # the forecast's anchor
    trend_ft_per_h: float | None
    trend: Literal["rising", "falling", "steady"] | None
    problem: str | None              # the gauge problem text when stale/invalid


class FieldTimeline(BaseModel):
    start: datetime                  # bundle.as_of; middle_ft[i] is at start + (i+1) h
    middle_ft: list[float]           # hourly, len == horizon_hours
    bands: dict[str, list[float]]    # ensemble: "10","25","75","90"; fallback: "low","high"


class FieldSummary(BaseModel):
    computed_at: datetime            # request time, UTC, whole seconds
    mode: Literal["ensemble", "fallback", "no_forecast"]
    status: FieldStatus
    lake: FieldLake
    levels: list[FieldLevel]         # always 4, ordered low to high
    peaks_ft: list[float]            # sorted; empty unless ensemble
    peak_levels: list[str | None]    # parallel to peaks_ft: highest level key reached
    cases: dict[str, float] | None   # {"dry","middle","wet"} = low/median/high peaks
    timeline: FieldTimeline | None
    rain_6h: dict[str, list[float]] | None   # {"middle","wet"} = median/high rain, in per 6 h
    ensemble_cycle: datetime | None
    stale_since: datetime | None = None      # set by /field when the forecast part is a previous result
    unavailable: str | None = None           # banner text in that case


def _round(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def summarize(result: PredictionResult, bundle: InputBundle, decision: AlertDecision,
              art: Artifact, *, observation: GaugeObservation | None,
              trend_ft_per_h: float | None, gauge_problem: str | None,
              computed_at: datetime) -> FieldSummary:
    off = art.datum.sensor_to_absolute_offset_ft

    def stick(abs_ft: float) -> float:
        # Subtract in Decimal: 342.2 - 338.375 is 3.82499... in floats, which would round to 3.82.
        return _round(float(Decimal(str(abs_ft)) - Decimal(str(off))))

    if result.ensemble_peaks_ft:
        mode = "ensemble"
    elif any(s.hourly_in for s in bundle.forecast_scenarios):
        mode = "fallback"
    else:
        mode = "no_forecast"
    forecast = mode != "no_forecast"
    peaks = result.ensemble_peaks_ft or []

    th = art.thresholds_abs_ft
    plastic, overtop_edge, bridge_edge = eap_level(1), eap_level(2), eap_level(3)
    plastic_abs = plastic.gauge_ft + off
    # (key, label, counted at (absolute), band top (stick), chance)
    rows = [
        ("early_warning", "Warning", th.early_warning, None,
         decision.probabilities["early_warning"]),
        ("eap_plastic", "Plastic", plastic_abs, None,
         sum(p >= plastic_abs for p in peaks) / len(peaks) if peaks else None),
        ("overtop", "Overtop", th.dam_crest, overtop_edge.gauge_ft,
         decision.probabilities["dam_crest"]),
        ("bridge", "Bridge", th.bridge_deck, bridge_edge.gauge_ft,
         decision.probabilities["bridge_deck"]),
    ]
    levels = [
        FieldLevel(key=key, label=label, stick_ft=stick(at),
                   band_top_ft=_round(top) if top is not None else None,
                   pct=round(100 * p) if forecast and p is not None else None,
                   reached=result.current_elevation >= at)
        for key, label, at, top, p in rows
    ]
    by_key = {lv.key: lv for lv in levels}

    def highest_reached(peak: float) -> str | None:
        return next((key for key, _, at, _, _ in reversed(rows) if peak >= at), None)

    headline = None
    if decision.active_threshold_label is not None:
        lv = by_key[_LEVEL_FOR_THRESHOLD[decision.active_threshold_label]]
        headline = f"{lv.label} {lv.pct}%" if lv.pct is not None else lv.label

    observed_eap = None
    if observation is not None and observation.confirmed and observation.gauge_ft is not None:
        rank = active_eap_rank(observation.gauge_ft)
        if rank > 0:
            seen = eap_level(rank)
            observed_eap = {"title": seen.title, "gauge_ft": _round(observation.gauge_ft),
                            "color": seen.color}

    trend = None
    if trend_ft_per_h is not None:
        trend = ("steady" if abs(trend_ft_per_h) < _STEADY_FT_PER_H
                 else "rising" if trend_ft_per_h > 0 else "falling")

    cases = timeline = rain_6h = None
    if forecast:
        by_name = {s.name: s for s in result.scenarios}
        cases = {case: stick(by_name[name].peak_elevation)
                 for case, name in (("dry", "low"), ("middle", "median"), ("wet", "high"))}

        def path(name: str) -> list[float]:
            return [stick(p.elevation) for p in by_name[name].trajectory]

        if mode == "ensemble":
            bands = {k: [stick(e) for e in result.elevation_bands_ft[k]]
                     for k in ("10", "25", "75", "90")}
        else:
            bands = {"low": path("low"), "high": path("high")}
        timeline = FieldTimeline(start=bundle.as_of, middle_ft=path("median"), bands=bands)

        rain = {s.name: s.hourly_in for s in scenario_rain(bundle, result)}
        rain_6h = {case: [round(sum(rain[name][i:i + _RAIN_BLOCK_HOURS]), 2)
                          for i in range(0, len(rain[name]), _RAIN_BLOCK_HOURS)]
                   for case, name in (("middle", "median"), ("wet", "high"))}

    return FieldSummary(
        computed_at=computed_at,
        mode=mode,
        status=FieldStatus(level=decision.active_level_name, rank=decision.active_rank,
                           headline=headline, observed_eap=observed_eap),
        lake=FieldLake(now_ft=stick(result.current_elevation), trend_ft_per_h=trend_ft_per_h,
                       trend=trend, problem=gauge_problem),
        levels=levels,
        peaks_ft=[stick(p) for p in peaks],
        peak_levels=[highest_reached(p) for p in peaks],
        cases=cases,
        timeline=timeline,
        rain_6h=rain_6h,
        ensemble_cycle=bundle.ensemble_cycle if forecast else None,
    )
