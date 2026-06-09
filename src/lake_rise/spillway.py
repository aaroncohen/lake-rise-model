"""Spillway outflow as a function of lake elevation and stop-log control elevation
(spec 4 Module 6 / Reference Module 6).

Only the single-point capacity at 342 ft is known, so the stage-discharge curve is
modeled as a weir: Q = capacity * (H / H_rated) ** weir_exponent, where H is the head
above the control elevation and H_rated is the head at 342 ft. The weir law
(exponent 1.5) is far more accurate than a linear interp at small heads — a linear
curve over-estimates outflow ~5x just above the control elevation, draining the lake
too fast in dry weather (open item #3). A small board-leakage term is added just below
the control elevation for dry-weather recession realism."""

from __future__ import annotations

from .artifact import Spillway


def _leg_flow(control_elev: float, capacity_at_342: float, rated_elev: float, h: float,
              exponent: float) -> float:
    """Weir stage-discharge: Q = capacity * (H / H_rated) ** exponent, with H the head
    above control_elev and H_rated the head at the rated (342 ft) elevation. Above the
    rated elevation it extrapolates along the same power law."""
    if h <= control_elev:
        return 0.0
    frac = (h - control_elev) / (rated_elev - control_elev)
    return capacity_at_342 * (frac ** exponent)


def spillway_outflow_cfs(sp: Spillway, h_abs_ft: float, control_elev_ft: float) -> float:
    """Total spillway outflow (cfs) at elevation h given the active control elevation.

    The primary spillway's effective control is the stop-log control elevation; the
    auxiliary spillway has its own fixed control (~340.0 ft) and only engages above it.
    Below the control elevation, return a small leakage seepage term."""
    rated = sp.rated_head_elev_ft
    n = sp.weir_exponent

    # Primary: controlled by stop-logs.
    q_primary = _leg_flow(control_elev_ft, sp.primary.capacity_cfs_at_342, rated, h_abs_ft, n)

    # Auxiliary: fixed control, independent of stop-logs.
    q_aux = _leg_flow(sp.auxiliary.control_elev_ft, sp.auxiliary.capacity_cfs_at_342, rated, h_abs_ft, n)

    q = q_primary + q_aux

    # Board leakage just below the control elevation (matters only for recession).
    lk = sp.leakage
    if q == 0.0 and control_elev_ft - lk.active_within_ft_below_control <= h_abs_ft <= control_elev_ft:
        q = lk.cfs

    return q
