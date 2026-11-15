"""Synthesize low / median / high rainfall scenarios from a single point forecast
(spec 4.7). Home Assistant weather integrations expose no percentiles, so the
spread is manufactured — but grounded in published QPF-skill behavior rather than
arbitrary multipliers (see docs/forecast-uncertainty.md):

  * Precip error is multiplicative (log-normal), so the band is a RATIO on the
    forecast amount, not a fixed +/- inches. median = forecast, EXCEPT a NOAA
    high-end QPF lifts the median toward it when the point forecast is materially
    lower (a dropped feed during a flood watch); see ``_median_with_noaa`` (#2).
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

This band drives the page simulator, and the live forecast when no recent ensemble run is
archived. Normally the live forecast runs the archived ECMWF ensemble's members instead,
corrected against the gauge (``calibrate_members``, ``ensemble_members``; the
2026-11-14 calibration-log entry).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import NormalDist

from .artifact import Artifact, EnsembleDayCalibration
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


def _median_with_noaa(art: Artifact, point_forecast_in: list[float],
                      noaa_high_total_in: float | None) -> list[float]:
    """The median (central) hourly series, lifted toward the NOAA high-end QPF when
    the automated point forecast is materially below it -- e.g. a dropped forecast
    feed reading ~0 during an active flood watch (#2 fix).

    median_total = max(point_total, f * noaa_high_total_in), with
    f = ``uncertainty.noaa_median_fraction``. The point forecast's temporal shape is
    preserved when it has one; a bone-dry point forecast is spread uniformly over the
    horizon (no shape information survives a dropped feed).

    Rationale for blending into the median rather than only the high tail: a scenario
    set whose median is dry but whose 90th percentile is a major storm is an
    incoherent distribution (50%+ point-mass at zero), and it caps P(crossing) at 0.5
    -- so CRITICAL/EVACUATE can never fire. During a flood watch a ~0 point forecast
    is far more likely a data artifact than a credible central estimate, so the NOAA
    signal must move the center. f<1 keeps NOAA a *high-end* number (the median sits
    below it); see the 2026-07-03 #2 calibration-log entry."""
    median = list(point_forecast_in)
    if noaa_high_total_in is None or not median:
        return median
    point_total = sum(median)
    target = max(point_total, art.uncertainty.noaa_median_fraction * noaa_high_total_in)
    if target <= point_total:              # point forecast already >= f * noaa
        return median
    if point_total > 0:
        scale = target / point_total       # keep the forecast's temporal shape
        return [p * scale for p in median]
    return [target / len(median)] * len(median)   # dropped feed -> uniform


def synthesize_scenarios(
    art: Artifact,
    point_forecast_in: list[float],
    month: int = 1,
    pop_frac: list[float] | None = None,
    noaa_high_total_in: float | None = None,
) -> list[ScenarioRain]:
    """Build (low, median, high) hourly scenarios. ``month`` selects the seasonal
    spread; the lead time for hour i is i (it is i hours past ``now``).

    The median is the point forecast, except a NOAA high-end QPF lifts it when the
    point forecast is materially lower (``_median_with_noaa``). low/high are then the
    lead-dependent band around that median; the NOAA total additionally anchors the
    high branch as an upper bound (it is a *high* estimate, so the tail never sits
    below it)."""
    median = _median_with_noaa(art, point_forecast_in, noaa_high_total_in)
    low: list[float] = []
    high: list[float] = []

    for i, m in enumerate(median):
        lo_r, hi_r = lead_ratios(art, i, month)
        lo = m * lo_r
        hi = m * hi_r
        if pop_frac is not None and i < len(pop_frac):
            lo *= pop_frac[i]          # occurrence uncertainty drives the low branch down
        low.append(max(0.0, lo))
        high.append(max(0.0, hi))

    # NOAA high-end total also anchors the tail: never let the high branch fall below it.
    if noaa_high_total_in is not None:
        total = sum(high)
        if total > 0 and total < noaa_high_total_in:
            high = [h * (noaa_high_total_in / total) for h in high]
        elif total == 0 and median:
            high = [noaa_high_total_in / len(median)] * len(median)

    return [
        ScenarioRain(name="low", hourly_in=low),
        ScenarioRain(name="median", hourly_in=median),
        ScenarioRain(name="high", hourly_in=high),
    ]


def calibrate_members(cal: EnsembleDayCalibration, totals_in: list[float]) -> list[float]:
    """One lead day's member totals corrected for the ensemble's bias and its too-narrow
    spread, as fitted against the gauge (2026-11-14 calibration-log entry). In square-root
    space the members' mean is scaled by ``mean_scale`` and their spread about it by
    ``spread_scale``, and each member moves a further ``added_spread`` times the mean by its
    rank as a standard-normal quantile: wet members get wetter, dry ones drier."""
    n = len(totals_in)
    z = [math.sqrt(max(0.0, x)) for x in totals_in]
    zbar = sum(z) / n if n else 0.0
    u = [0.0] * n
    for rank, i in enumerate(sorted(range(n), key=lambda i: z[i])):
        u[i] = NormalDist().inv_cdf((rank + 0.5) / n)
    return [max(0.0, cal.mean_scale * zbar + cal.spread_scale * (z[i] - zbar)
                + cal.added_spread * zbar * u[i]) ** 2 for i in range(n)]


def ensemble_members(art: Artifact, run: dict, as_of: datetime, horizon_hours: int,
                     shape_in: list[float] | None = None) -> list[ScenarioRain]:
    """Each member's hourly rain over the horizon from an archived ensemble run
    (``qpf_archive``), calibrated by lead day. Empty without ``art.ensemble_rain`` or when the
    run is older than its ``max_run_age_h``.

    The run's days count from its cycle time, so the first is partly past at ``as_of``: the
    part still ahead gets that share of the day's total. The point forecast (``shape_in``) times
    it: a member keeps the point forecast's hourly rain, scaled down if the member is drier,
    and rain beyond the point forecast's is spread evenly over the day's hours. A member's wet
    day therefore never piles into the one or two hours the point forecast has a shower. Hours
    past the run's last day take the point forecast for every member."""
    er = art.ensemble_rain
    if er is None or horizon_hours <= 0:
        return []
    cycle = datetime.fromisoformat(run["cycle"])
    if not timedelta(0) <= as_of - cycle <= timedelta(hours=er.max_run_age_h):
        return []
    days = sorted((p for p in run["periods"] if p["hours"] == 24), key=lambda p: p["lead_h"])
    if not days:
        return []
    shape_in = shape_in or []
    n_shape = min(len(shape_in), horizon_hours)          # hours the point forecast covers
    shape = [shape_in[i] if i < n_shape else 0.0 for i in range(horizon_hours)]
    last_fitted = max(er.calibration_by_day, key=int)
    rain = [list(shape) for _ in days[0]["members_in"]]
    for p in days:
        cal = er.calibration_by_day.get(str(p["lead_h"] // 24 + 1),
                                        er.calibration_by_day[last_fitted])
        totals = calibrate_members(cal, p["members_in"])
        first = int((cycle + timedelta(hours=p["lead_h"]) - as_of).total_seconds() // 3600)
        ahead = range(max(0, first), first + 24)
        if not ahead:
            continue
        known = [shape[i] for i in ahead if i < n_shape]
        fill = sum(known) / len(known) if known else 0.0    # hours the point forecast lacks
        point = [shape[i] if i < n_shape else fill for i in ahead]
        point_total = sum(point)
        for m, total in enumerate(totals):
            ahead_total = total * len(ahead) / 24
            scale = min(1.0, ahead_total / point_total) if point_total > 0 else 0.0
            extra = max(0.0, ahead_total - point_total) / len(ahead)
            for i, pt in zip(ahead, point):
                if i < horizon_hours:
                    rain[m][i] = pt * scale + extra
    return [ScenarioRain(name=f"member {m}", hourly_in=r) for m, r in enumerate(rain)]
