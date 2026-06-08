"""Synthesize low / median / high rainfall scenarios from a single point forecast
(spec 4.7). Home Assistant weather integrations expose no percentiles, so the
spread is manufactured: median = point forecast; low/high = multiplier band that
widens with lead time; an optional NOAA-alert QPF sets the heavy-tail high branch."""

from __future__ import annotations

from .artifact import Artifact
from .bundle import ScenarioRain


def synthesize_scenarios(
    art: Artifact,
    point_forecast_in: list[float],
    pop_frac: list[float] | None = None,
    noaa_high_total_in: float | None = None,
) -> list[ScenarioRain]:
    """Build (low, median, high) hourly scenarios.

    - median: the point forecast as-is.
    - low/high: scaled by configured multipliers, widening with lead-time hour.
    - pop_frac (0..1 per hour): if given, scales the low branch down when rain is
      uncertain (low PoP -> low scenario approaches zero).
    - noaa_high_total_in: if given (active alert), the high branch is rescaled so
      its total at least matches this heavy-tail QPF.
    """
    u = art.uncertainty
    median = list(point_forecast_in)
    low: list[float] = []
    high: list[float] = []

    for i, p in enumerate(point_forecast_in):
        widen = 1.0 + u.lead_time_widening_per_hour * i
        lo = p * u.scenario_low_mult / widen
        hi = p * u.scenario_high_mult * widen
        if pop_frac is not None and i < len(pop_frac):
            lo *= pop_frac[i]
        low.append(max(0.0, lo))
        high.append(max(0.0, hi))

    if noaa_high_total_in is not None:
        total = sum(high)
        if total > 0 and total < noaa_high_total_in:
            scale = noaa_high_total_in / total
            high = [h * scale for h in high]
        elif total == 0 and point_forecast_in:
            # No forecast rain but an alert exists: distribute the QPF uniformly.
            per = noaa_high_total_in / len(point_forecast_in)
            high = [per] * len(point_forecast_in)

    return [
        ScenarioRain(name="low", hourly_in=low),
        ScenarioRain(name="median", hourly_in=median),
        ScenarioRain(name="high", hourly_in=high),
    ]
