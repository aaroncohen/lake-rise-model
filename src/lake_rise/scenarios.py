"""Synthesize low / median / high rainfall scenarios from a single point forecast
(spec 4.7). Home Assistant weather integrations expose no percentiles, so the
spread is manufactured — but grounded in published QPF-skill behavior rather than
arbitrary multipliers (see docs/forecast-uncertainty.md):

  * Precip error is multiplicative (log-normal), so the band is a RATIO on the
    forecast amount, not a fixed +/- inches. median = forecast.
  * The 80% interval (10th/90th pct of actual/forecast) WIDENS with lead time, per
    WPC QPF threat-score decay; it is ASYMMETRIC (fatter high tail) because heavy
    events are biased low at longer leads -- the dangerous direction for EAP thresholds.
  * Cool-season PNW frontal/AR storms are the favorable baseline; summer convection
    widens the band (season_spread_factor) and lowers confidence.
  * PoP scales the low branch toward zero when occurrence is uncertain.
  * An active NOAA-alert QPF can lift the heavy-tail high branch.

KNOWN BIAS: the same lead-dependent ratio is applied to every hour at once, so low/high
are COMONOTONIC. Summing per-hour q10/q90 over a storm is the q10/q90 of the total only
under perfect hourly correlation; otherwise it OVERSTATES the total's dispersion. The
low/high are thus an upper bound on spread, and the q=0.10/0.90 labels the predictor
attaches downstream are conservative-wide. Resolve with the spec 3.5 forecast-vs-gauge fit
(see docs/forecast-uncertainty.md).

This same band feeds both the live forecast path and the page simulator, so the
displayed uncertainty matches what the warning system would actually produce.
"""

from __future__ import annotations

from .artifact import Artifact
from .bundle import ScenarioRain


def _lead_day(lead_hours: int) -> int:
    """Forecast lead day (1-based): hours 0-23 -> day 1, 24-47 -> day 2, ..."""
    return lead_hours // 24 + 1


def _season_factor(art: Artifact, month: int) -> float:
    return art.uncertainty.season_spread_factor.get(str(int(month)), 1.0)


def lead_ratios(art: Artifact, lead_hours: int, month: int) -> tuple[float, float]:
    """The (low, high) multiplicative ratios on the forecast amount at a given lead,
    widened in log space by the season factor (so summer is wider, winter tighter)."""
    u = art.uncertainty
    day = _lead_day(lead_hours)
    lo, hi = u.lead_ratio_by_day.get(str(day), tuple(u.beyond_day7_ratio))
    sf = _season_factor(art, month)
    return lo ** sf, hi ** sf


def confidence_for_lead(art: Artifact, lead_hours: int, month: int) -> tuple[int, float]:
    """Approximate forecast confidence (%, label-able) at a given lead, derived from
    the QPF-skill-by-lead table and reduced in the less-predictable summer regime."""
    u = art.uncertainty
    day = _lead_day(lead_hours)
    base = u.skill_confidence_by_day.get(str(day), u.beyond_day7_confidence)
    pct = max(5, min(99, round(base / _season_factor(art, month))))
    high_ratio = lead_ratios(art, lead_hours, month)[1]
    return pct, round(high_ratio, 2)


def confidence_label(pct: float) -> str:
    return "High" if pct >= 70 else "Medium" if pct >= 45 else "Low"


def synthesize_scenarios(
    art: Artifact,
    point_forecast_in: list[float],
    month: int = 1,
    pop_frac: list[float] | None = None,
    noaa_high_total_in: float | None = None,
) -> list[ScenarioRain]:
    """Build (low, median, high) hourly scenarios. ``month`` selects the seasonal
    spread; the lead time for hour i is i (it is i hours past ``now``)."""
    median = list(point_forecast_in)
    low: list[float] = []
    high: list[float] = []

    for i, p in enumerate(point_forecast_in):
        lo_r, hi_r = lead_ratios(art, i, month)
        lo = p * lo_r
        hi = p * hi_r
        if pop_frac is not None and i < len(pop_frac):
            lo *= pop_frac[i]          # occurrence uncertainty drives the low branch down
        low.append(max(0.0, lo))
        high.append(max(0.0, hi))

    if noaa_high_total_in is not None:
        total = sum(high)
        if total > 0 and total < noaa_high_total_in:
            scale = noaa_high_total_in / total
            high = [h * scale for h in high]
        elif total == 0 and point_forecast_in:
            per = noaa_high_total_in / len(point_forecast_in)
            high = [per] * len(point_forecast_in)

    return [
        ScenarioRain(name="low", hourly_in=low),
        ScenarioRain(name="median", hourly_in=median),
        ScenarioRain(name="high", hourly_in=high),
    ]
