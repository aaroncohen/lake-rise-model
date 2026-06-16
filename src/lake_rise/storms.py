"""Compose a storm hyetograph and a predictor-ready :class:`InputBundle` from a
what-if / preset / historical / custom storm spec.

This is the framework-free composition layer shared by the HTTP API (``/simulate``,
``/live/predict`` what-if path) and the local CLI alert-preview tooling, so there is one
storm builder rather than the HTTP layer owning it. It reuses the existing pieces:
``presets.build_storm``, ``historical.hyetograph_for``, and ``scenarios.synthesize_scenarios``.
"""

from __future__ import annotations

from datetime import datetime

from . import historical
from .artifact import Artifact
from .bundle import InputBundle, ScenarioRain
from .presets import build_storm
from .scenarios import synthesize_scenarios


def storm_series(
    art: Artifact,
    *,
    preset: str | None = None,
    historical_id: str | None = None,
    hourly_in: list[float] | None = None,
    rate_in_per_hr: float | None = None,
    duration_h: int | None = None,
    start_offset_h: int = 0,
    horizon_h: int = 72,
) -> list[float]:
    """Build the median hourly-rainfall series (inches) for a storm, then delay it by
    ``start_offset_h`` dry-lead hours and pad/truncate to ``horizon_h``.

    Exactly one of ``preset`` / ``historical_id`` / ``hourly_in`` / (``rate_in_per_hr`` +
    ``duration_h``) selects the storm shape; an all-empty spec yields all-zeros (dry).
    Raises ``KeyError`` for an unknown preset or historical id (callers map to a 400)."""
    if preset is not None:
        series = build_storm(art, preset)
    elif historical_id is not None:
        series = historical.hyetograph_for(historical_id)
    elif hourly_in is not None:
        series = list(hourly_in)
    elif rate_in_per_hr is not None and duration_h is not None:
        series = [rate_in_per_hr] * duration_h
    else:
        series = []
    series = [0.0] * start_offset_h + series
    return (series + [0.0] * horizon_h)[:horizon_h]


def bundle_for_storm(
    art: Artifact,
    series: list[float],
    *,
    current_elevation_abs_ft: float,
    stop_log_count: int,
    month: int,
    as_of: datetime,
    initial_sm_in: float | None = None,
    initial_s_if_in: float = 0.0,
    band: bool = True,
) -> InputBundle:
    """Wrap a storm series in a predictor-ready bundle. With ``band`` the low/median/high
    uncertainty band is synthesized (seasonal/lead-time spread); otherwise all three
    scenarios are the same series. Mirrors the bundle the HTTP ``/simulate`` route builds."""
    if band:
        scenarios = synthesize_scenarios(art, series, month=month)
    else:
        scenarios = [ScenarioRain(name=n, hourly_in=series) for n in ("low", "median", "high")]
    return InputBundle(
        as_of=as_of,
        current_elevation_abs_ft=current_elevation_abs_ft,
        stop_log_count=stop_log_count,
        forecast_scenarios=scenarios,
        initial_sm_in=initial_sm_in,
        initial_s_if_in=initial_s_if_in,
    )
