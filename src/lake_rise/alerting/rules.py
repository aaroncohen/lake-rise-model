"""Pure evaluation: turn a PredictionResult into an AlertDecision.

Two independent judgements:
  * the escalation ladder  -> the highest level whose threshold probability is met;
  * the test trigger       -> whether more than a small amount of rain is forecast.

This module decides *what the situation is*. Whether that warrants a notification
(the fire-on-crossing logic) lives in ``state.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..artifact import Artifact
from ..bundle import InputBundle
from ..predict import PredictionResult
from .config import AlertConfig


@dataclass(frozen=True)
class TriggeredThreshold:
    label: str
    elevation: float
    probability: float
    median_cross_at: datetime | None    # expected crossing (median scenario)
    earliest_cross_at: datetime | None  # earliest plausible crossing (high scenario)


@dataclass(frozen=True)
class AlertDecision:
    generated_at: datetime
    horizon_hours: int
    current_elevation: float
    freeboard_ft: float
    datum_offset_ft: float      # sensor_to_absolute_offset_ft; converts abs → stick reading
    data_fresh: bool

    # Ladder outcome.
    active_rank: int                    # 0 = normal
    active_level_name: str | None

    # Probabilities (by threshold label).
    probabilities: dict[str, float]
    thresholds: tuple[TriggeredThreshold, ...]

    # Peak (median scenario headline; high scenario carried for context).
    peak_elevation: float
    peak_at: datetime | None
    peak_elevation_high: float

    # Forecast rainfall summary.
    forecast_total_in: float
    peak_rain_hour: int | None
    confidence_pct: int
    confidence_label: str

    # Independent test trigger.
    test_active: bool

    # Where the lake is forecast to be ~24 h out (median / wettest scenario). Optional
    # so older callers and synthetic decisions don't have to supply them.
    forecast_elev_24h: float | None = None
    forecast_elev_24h_high: float | None = None

    # High-water mark of the just-ended alert episode, supplied by the orchestrator from
    # persisted state when rendering an ALL_CLEAR (the decision's own forward peak is low
    # once things have calmed). None outside an all-clear.
    episode_peak_elevation: float | None = None
    episode_peak_at: datetime | None = None

    def threshold(self, label: str) -> TriggeredThreshold | None:
        return next((t for t in self.thresholds if t.label == label), None)


def _cross_at(start: datetime, hours: float | None) -> datetime | None:
    return start + timedelta(hours=hours) if hours is not None else None


def _median_rainfall(bundle: InputBundle) -> list[float]:
    for s in bundle.forecast_scenarios:
        if s.name == "median":
            return list(s.hourly_in)
    return list(bundle.forecast_scenarios[0].hourly_in) if bundle.forecast_scenarios else []


def evaluate(
    result: PredictionResult,
    bundle: InputBundle,
    art: Artifact,
    config: AlertConfig,
) -> AlertDecision:
    start = result.generated_at
    by_name = {s.name: s for s in result.scenarios}
    median = by_name.get("median")
    high = by_name.get("high")

    # P(cross) per threshold label, keyed by the artifact's labels.
    prob_by_label = {tp.label: tp.p_cross_within_horizon for tp in result.threshold_probabilities}
    elev_by_label = {tp.label: tp.threshold_abs_ft for tp in result.threshold_probabilities}

    # Crossing times: median expected vs high (earliest). hours_to_* are measured from `start`.
    def hours_for(scn, label: str) -> float | None:
        if scn is None:
            return None
        if label == "early_warning":
            return scn.hours_to_early_warning
        if label == "bridge_deck":
            return scn.hours_to_bridge_deck
        return scn.hours_to_crest

    thresholds = tuple(
        TriggeredThreshold(
            label=label,
            elevation=elev_by_label[label],
            probability=prob_by_label[label],
            median_cross_at=_cross_at(start, hours_for(median, label)),
            earliest_cross_at=_cross_at(start, hours_for(high, label)),
        )
        for label in prob_by_label
    )

    # Ladder: highest-rank level whose threshold probability meets its cutoff.
    active_rank, active_name = 0, None
    for lv in config.levels:
        p = prob_by_label.get(lv.threshold_label, 0.0)
        if p >= lv.min_prob and lv.rank > active_rank:
            active_rank, active_name = lv.rank, lv.name

    # Peak from the median scenario trajectory.
    peak_elev, peak_at = result.current_elevation, None
    if median and median.trajectory:
        top = max(median.trajectory, key=lambda p: p.elevation)
        peak_elev, peak_at = top.elevation, top.valid_at
    peak_high = high.peak_elevation if high else peak_elev

    # Forecast lake level ~24 h out (median headline + wettest scenario), for context.
    def _elev_at(scn, hours: int) -> float | None:
        if scn is None or not scn.trajectory:
            return None
        target = start + timedelta(hours=hours)
        return min(scn.trajectory, key=lambda p: abs((p.valid_at - target).total_seconds())).elevation

    forecast_elev_24h = _elev_at(median, 24)
    forecast_elev_24h_high = _elev_at(high, 24)

    # Forecast rainfall summary (median scenario).
    series = _median_rainfall(bundle)
    total_in = round(sum(series), 2)
    peak_rain_hour = (max(range(len(series)), key=lambda i: series[i]) + 1) if any(series) else None

    # Confidence from the same QPF-skill model the UI/band use, keyed to the
    # *risk-relevant* lead rather than a fixed day-1: the earliest hour the median
    # trajectory reaches a threshold if it reaches one, else the hour the heaviest
    # rain lands. So a storm whose danger is days out reads lower confidence -- it is
    # an ordinal skill score (High/Med/Low), not a calibrated event probability.
    from ..scenarios import confidence_for_lead, confidence_label
    cross_hours = [h for h in (
        median.hours_to_early_warning, median.hours_to_crest, median.hours_to_bridge_deck
    ) if median is not None and h is not None]
    lead_h = int(min(cross_hours)) if cross_hours else max(0, (peak_rain_hour or 1) - 1)
    confidence_pct, _ = confidence_for_lead(art, lead_h, start.month)

    test_active = config.test_enabled and total_in > config.test_rain_in

    return AlertDecision(
        generated_at=start,
        horizon_hours=result.horizon_hours,
        current_elevation=result.current_elevation,
        freeboard_ft=result.freeboard_ft,
        datum_offset_ft=art.datum.sensor_to_absolute_offset_ft,
        data_fresh=result.data_fresh,
        active_rank=active_rank,
        active_level_name=active_name,
        probabilities=prob_by_label,
        thresholds=thresholds,
        peak_elevation=peak_elev,
        peak_at=peak_at,
        peak_elevation_high=peak_high,
        forecast_total_in=total_in,
        peak_rain_hour=peak_rain_hour,
        confidence_pct=confidence_pct,
        confidence_label=confidence_label(confidence_pct),
        test_active=test_active,
        forecast_elev_24h=forecast_elev_24h,
        forecast_elev_24h_high=forecast_elev_24h_high,
    )
