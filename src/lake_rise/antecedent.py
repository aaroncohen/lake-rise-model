"""Infer antecedent subsurface state from RECORDED observations, so a prediction seeds the
slow groundwater store from real data instead of the seasonal climatological default.

The active-groundwater store ``S_agw`` has a long memory (AGWRC=0.97), so a short
trailing-rainfall hindcast can't charge it -- which is why ``model.initial_state`` falls
back to ``seasonal_agw_default``. But once the continuous archive has recorded a real
rain-free recession leading up to the anchor time, we can do better: read the standing
baseflow straight off the observed recession and invert it to ``S_agw``. This is the exact
inverse of how the seasonal table was *built* -- ``S_agw = Q / (1 - AGWRC**(1/24))`` at
AGWRC=0.97 (see the artifact's ``seasonal_agw_default_in`` comment) -- so it swaps a
seasonal *assumption* about baseflow for the *measured* one, with no change of units.

During a rain-free, quiescent spell the lake's hourly water balance is

    dh/dt * A(h) / 0.0826  =  q_agw + q_if - q_out(h)

with interflow ``q_if ~ 0`` after a few dry days (IRC drains it in days). Solve for the
standing baseflow ``q_agw``, then invert the linear AGW recession to the storage that
produces it. Returns ``None`` when the record lacks a long-enough clean recession -> the
caller keeps the seasonal seed (fall back to the default only when we have no better data).

Pure and framework-free; the caller adapts whatever it has (the continuous archive, a live
HA lake-history window) into ``(hour, elev_ft, rain_in)`` tuples.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Iterable

from . import model, units
from .artifact import Artifact
from .calibration.archive import ContinuousRecord
from .geometry import surface_area_acres
from .spillway import spillway_outflow_cfs


@dataclass
class GwSeed:
    """An inferred active-groundwater seed and the evidence behind it."""
    s_agw_in: float          # active-groundwater storage, inches over the watershed
    baseflow_cfs: float      # the standing baseflow it was inverted from
    evidence: dict


def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Ordinary least squares. Returns (slope, intercept, r_squared)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    slope = sxy / sxx if sxx > 0 else 0.0
    intercept = my - slope * mx
    r2 = (sxy * sxy) / (sxx * syy) if sxx > 0 and syy > 0 else 0.0
    return slope, intercept, r2


def _recession_rate_at_anchor(run: list[tuple[datetime, float]]) -> tuple[float, float, float]:
    """Local recession rate (ft/hr) and elevation *at the anchor* (the last point), plus a
    fit r-squared. Fits a quadratic in x = (t - anchor) hours (so x=0 at the anchor): the
    groundwater store's curved recession makes a straight-line slope over the window steeper
    than the true rate at the anchor, biasing the baseflow low. With x centred at the anchor
    the derivative there is just the linear coefficient and the level is the constant term --
    well-conditioned, no external solver. Falls back to a line if the fit is degenerate."""
    xs = [(t - run[-1][0]).total_seconds() / 3600.0 for t, _ in run]
    ys = [e for _, e in run]
    # Quadratic OLS y = a x^2 + b x + c via the centred normal equations (Cramer's rule).
    s0 = float(len(xs))
    s1 = sum(xs); s2 = sum(x * x for x in xs)
    s3 = sum(x ** 3 for x in xs); s4 = sum(x ** 4 for x in xs)
    ty = sum(ys); txy = sum(x * y for x, y in zip(xs, ys))
    tx2y = sum(x * x * y for x, y in zip(xs, ys))
    det = (s4 * (s2 * s0 - s1 * s1) - s3 * (s3 * s0 - s1 * s2)
           + s2 * (s3 * s1 - s2 * s2))
    if len(xs) >= 3 and abs(det) > 1e-9:
        b = (s4 * (txy * s0 - ty * s1) - s3 * (tx2y * s0 - ty * s2)
             + s2 * (tx2y * s1 - txy * s2)) / det
        c = (s4 * (s2 * ty - s1 * txy) - s3 * (s3 * ty - s1 * tx2y)
             + s2 * (s3 * txy - s2 * tx2y)) / det
        a = (tx2y * (s2 * s0 - s1 * s1) - txy * (s3 * s0 - s1 * s2)
             + ty * (s3 * s1 - s2 * s2)) / det
        ss_res = sum((y - (a * x * x + b * x + c)) ** 2 for x, y in zip(xs, ys))
        my = ty / s0
        ss_tot = sum((y - my) ** 2 for y in ys)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        return b, c, r2                                  # slope & level at x = 0 (the anchor)
    slope, intercept, r2 = _linreg(xs, ys)
    return slope, intercept + slope * xs[-1], r2


def hourly_drain(agwrc_per_day: float) -> float:
    """Fraction of the linear active-groundwater store released per hour (matches
    ``model.m7_groundwater``'s ``1 - AGWRC**(1/24)``)."""
    return 1.0 - agwrc_per_day ** (1.0 / 24.0)


def infer_s_agw(
    art: Artifact,
    observations: Iterable[tuple[datetime, float | None, float]],
    at: datetime,
    control_elev: float,
    *,
    min_recession_hours: int = 96,
    rate_window_hours: int = 72,
    max_window_hours: int = 240,
    rain_break_in: float = 0.05,
    rain_budget_in: float = 0.25,
    min_r2: float = 0.6,
) -> GwSeed | None:
    """Infer the active-groundwater storage at ``at`` from the trailing rain-free lake
    recession in ``observations`` (each an ``(hour, elev_ft | None, rain_in)`` tuple).

    Uses only the contiguous, gauge-complete run ending at ``at`` (so it never peeks past
    the anchor and interflow has had time to drain). The run extends back through trace
    rain -- summer drizzle a few hundredths of an inch per hour barely generates interflow
    -- but ends at the first *significant* hour (>= ``rain_break_in``) or once cumulative
    rain over the run exceeds ``rain_budget_in`` (beyond which interflow contamination is no
    longer negligible). The run must be at least ``min_recession_hours`` long -- otherwise
    returns ``None`` and the caller keeps the seasonal default -- but the recession *rate*
    is fit only over the trailing ``rate_window_hours``, because the groundwater store's
    ~23-day half-life curves the recession enough that a whole-window average slope would
    misstate the rate at the anchor. ``control_elev`` is the spillway control over the
    recession (for the outflow rating)."""
    lo = at - timedelta(hours=max_window_hours)
    obs = sorted((t, e, r) for t, e, r in observations if lo <= t <= at)
    if not obs:
        return None

    # Walk backwards from the anchor, keeping the contiguous, gauge-complete run. A gauge gap
    # or a significant rain hour ends it; trace drizzle is tolerated until the cumulative rain
    # over the run exceeds the budget (past which interflow is no longer negligible).
    run: list[tuple[datetime, float]] = []
    cum_rain = 0.0
    for t, e, r in reversed(obs):
        if e is None or r >= rain_break_in:
            break
        cum_rain += r
        if cum_rain > rain_budget_in:
            break
        run.append((t, e))
    run.reverse()
    if len(run) < min_recession_hours:
        return None
    span_h = (run[-1][0] - run[0][0]).total_seconds() / 3600.0
    if span_h < min_recession_hours - 1:            # a couple of missing interior hours is ok
        return None

    # Fit the recession *rate* over the trailing rate-window only, evaluated at the anchor.
    anchor_t = run[-1][0]
    rate_run = [(t, e) for t, e in run if (anchor_t - t).total_seconds() / 3600.0 <= rate_window_hours]
    if len(rate_run) < 3:
        rate_run = run
    slope, h_rep, r2 = _recession_rate_at_anchor(rate_run)

    # Noise gate: the gauge carries ~0.05 ft residual noise, comparable to a multi-day
    # recession, so a short/noisy window can't resolve the rate. Require the fit to explain
    # most of the variance; otherwise return None -> the caller keeps the seasonal seed. Only
    # a genuinely clean, well-resolved recession (accumulated over a long dry spell) trusts
    # the observed baseflow over the climatological default.
    if r2 < min_r2:
        return None

    # Water balance at the anchor: standing baseflow = observed net rise + spillway outflow,
    # with interflow taken as drained over the multi-day dry window.
    area = surface_area_acres(art.geometry, h_rep)
    q_out = spillway_outflow_cfs(art.spillway, h_rep, control_elev)
    q_net = slope * area / units.CFS_TO_ACFT_PER_HR       # inverse of cfs_to_dh at dt = 1 h
    q_agw = q_net + q_out

    if q_agw <= 0.0:
        # Lake falling faster than leakage alone -> no positive baseflow signal; seed the
        # slow store empty (the driest, safe reading) rather than invert a negative flow.
        s_agw = 0.0
    else:
        released_in = q_agw / units.depth_in_to_cfs(1.0, art.watershed.drainage_area_acres, 1.0)
        s_agw = released_in / hourly_drain(art.hspf.AGWRC_per_day)   # KVARY=0: linear inverse

    return GwSeed(
        s_agw_in=round(s_agw, 4),
        baseflow_cfs=round(max(q_agw, 0.0), 3),
        evidence={
            "recession_hours": len(run),
            "h_rep_ft": round(h_rep, 3),
            "recession_ft_per_day": round(slope * 24.0, 4),
            "r2": round(r2, 3),
            "q_out_cfs": round(q_out, 3),
        },
    )


# --- full-state assimilation: replay the recorded history, fit the groundwater store -----

@dataclass
class EstimatedState:
    """A model state spun up from the recorded history at the anchor time, plus provenance.
    ``s_agw_constrained`` is False when the level history didn't pin the groundwater store (then
    ``state.s_agw`` fell back to the seasonal seed while the fast stores are still data-driven)."""
    state: model.State
    s_agw_constrained: bool
    evidence: dict


def _interp_none(vals: list[float | None]) -> list[float | None]:
    """Linearly interpolate ``None`` gaps between known values (leading/trailing None untouched)."""
    out = list(vals)
    known = [i for i, v in enumerate(out) if v is not None]
    for a, b in zip(known, known[1:]):
        if b > a + 1:
            va, vb = out[a], out[b]  # type: ignore[assignment]
            for k in range(a + 1, b):
                out[k] = va + (vb - va) * (k - a) / (b - a)
    return out


def usable_window(record: ContinuousRecord, at: datetime,
                  gap_tol_h: int = 6) -> tuple[datetime, list[float], list[float | None]] | None:
    """The longest contiguous hourly stretch ending at ``at`` with no ≥ ``gap_tol_h`` run of
    missing rain (can't drive the model across a real gap). Small (< gap_tol_h) holes are filled:
    rain → 0.0, level → linearly interpolated. Returns ``(start, rain, level)`` (level entries may
    still be ``None`` only if a whole edge is missing), or ``None`` if nothing usable remains."""
    at_h = at.replace(minute=0, second=0, microsecond=0)
    by_hour = {datetime.fromisoformat(s.hour): s for s in record.samples
               if datetime.fromisoformat(s.hour) <= at_h}
    if not by_hour:
        return None
    earliest = min(by_hour)

    hours: list[datetime] = []
    gap_run = 0
    h = at_h
    while h >= earliest:
        s = by_hour.get(h)
        if s is None or s.rain_in is None:               # rain missing -> can't drive this hour
            gap_run += 1
            if gap_run >= gap_tol_h:
                break
        else:
            gap_run = 0
        hours.append(h)
        h -= timedelta(hours=1)
    hours.reverse()

    def _rain_missing(hr: datetime) -> bool:
        s = by_hour.get(hr)
        return s is None or s.rain_in is None

    while hours and _rain_missing(hours[0]):             # trim edges to real driving data
        hours.pop(0)
    while hours and _rain_missing(hours[-1]):
        hours.pop()
    if len(hours) < 2:
        return None

    start, end = hours[0], hours[-1]
    n = int((end - start).total_seconds() // 3600) + 1
    grid = [start + timedelta(hours=i) for i in range(n)]
    rain = [(0.0 if _rain_missing(g) else by_hour[g].rain_in) for g in grid]
    level_raw = [(None if (by_hour.get(g) is None or by_hour[g].elev_ft is None)
                  else by_hour[g].elev_ft) for g in grid]
    return start, rain, _interp_none(level_raw)


def groundwater_half_life_days(art: Artifact) -> float:
    """Groundwater recession half-life in days (from AGWRC): the timescale a seeded store, and the
    pulse the seasonal soil-moisture seed percolates into it, decay by."""
    return math.log(0.5) / math.log(art.hspf.AGWRC_per_day)


# Lookback cap for the state spin-up, in groundwater half-lives. At 8 half-lives (~182 d) the
# seasonal seed -- including the pulse the seasonal soil-moisture seed percolates into groundwater
# -- has drained to ~1 % of the seasonal baseflow target, so at/past the cap we effectively don't
# rely on the seasonal prior. Tunable (see the 2026-07-08 residual-vs-cap table): raise it to lean
# even less on the seed (10 half-lives → ~0 %), lower it to engage on less archive (5 → ~10 %).
DEFAULT_CAP_HALF_LIVES = 8.0


def estimate_state(art: Artifact, record: ContinuousRecord, at: datetime, control_elev: float, *,
                   cap_half_lives: float = DEFAULT_CAP_HALF_LIVES,
                   rmse_tol_ft: float = 0.5) -> EstimatedState | None:
    """Spin up the full model state at ``at`` by seeding seasonally and **replaying** the recorded
    rain forward -- a *smooth blend* of the seasonal prior and the observed history, not an on/off
    switch (any weather, not just dry spells).

    The lookback is capped at ``cap_half_lives`` × the groundwater half-life (AGWRC=0.97 → ~23 d,
    so ~182 d at the default 8), seeded seasonally at ``max(record-start, T0 − cap)``. Seeding
    seasonal at the window start assumes the seasonal average held in the unrecorded period before
    it; that seed then decays through the observed hours. So the estimate transitions *continuously*:
    with a few days of history it leans on the seasonal prior; as the record lengthens the seed
    drains and the state becomes historical. The cap is set where the seed -- including the pulse
    the seasonal soil-moisture seed percolates into groundwater, which peaks ~2 weeks in and *then*
    decays -- has drained to a negligible residual (~1 % of the seasonal baseflow target at 8
    half-lives), so at/past the cap we effectively don't rely on the seasonal prior at all; older
    history is redundant and not read.

    Returns ``None`` -- the caller keeps the seasonal spin-up -- only when there is no usable
    (gap-bounded) history at all, or the replay is grossly wrong over its drained tail (last
    ~2 half-lives; RMSE > ``rmse_tol_ft``), which flags broken data rather than a seed transient."""
    hl_days = groundwater_half_life_days(art)
    cap_h = int(round(cap_half_lives * hl_days * 24))
    win = usable_window(record, at)
    if win is None:
        return None
    start_all, rain_all, level_all = win
    off = max(0, len(rain_all) - cap_h)                   # cap the lookback; use all history if shorter
    start = start_all + timedelta(hours=off)
    rain, level = rain_all[off:], level_all[off:]
    h0 = next((v for v in level if v is not None), None)
    if h0 is None:
        return None
    month = start.month
    seed = model.initial_state(art, h0=h0, sm0=art.seasonal_sm_default(month),
                               s_agw0=art.seasonal_agw_default(month), month=month)
    end_state, recs = model.run(art, seed, rain[:-1], start, control_elev)  # end lands on the last hour

    tail = max(0, len(recs) - 2 * int(hl_days * 24))      # score only the drained tail
    errs = [recs[i].h - level[i + 1] for i in range(tail, len(recs)) if level[i + 1] is not None]
    rmse = (sum(e * e for e in errs) / len(errs)) ** 0.5 if errs else 0.0
    if rmse > rmse_tol_ft:                                 # gross model/data mismatch -> distrust
        return None

    end_state = replace(end_state, h=level[-1] if level[-1] is not None else end_state.h)
    seasonal_residual = round(art.hspf.AGWRC_per_day ** (len(rain) / 24.0), 3)  # decay of the S_agw seed
    return EstimatedState(
        state=end_state, s_agw_constrained=True,
        evidence={"history_days": round(len(rain) / 24, 1), "cap_days": round(cap_h / 24, 1),
                  "cap_half_lives": cap_half_lives, "seasonal_seed_residual": seasonal_residual,
                  "tail_rmse_ft": round(rmse, 4), "s_agw_at_t0": round(end_state.s_agw, 4)})
