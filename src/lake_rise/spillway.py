"""Spillway outflow as a function of lake elevation and stop-log control elevation
(spec 4 Module 6 / Reference Module 6).

STOPGAP: only the single-point capacity at 342 ft is known, so outflow is linearly
interpolated from 0 cfs at the control elevation to rated capacity at 342 ft. Proper
weir curves are the highest-value accuracy item (open item #3). A small board-leakage
term is added just below the control elevation for dry-weather recession realism."""

from __future__ import annotations

from .artifact import Spillway


def _leg_flow(control_elev: float, capacity_at_342: float, rated_elev: float, h: float) -> float:
    """Linear interp from 0 at control_elev to capacity at rated_elev (342 ft).
    Above rated_elev, extrapolate along the same slope."""
    if h <= control_elev:
        return 0.0
    slope = capacity_at_342 / (rated_elev - control_elev)
    return slope * (h - control_elev)


def spillway_outflow_cfs(sp: Spillway, h_abs_ft: float, control_elev_ft: float) -> float:
    """Total spillway outflow (cfs) at elevation h given the active control elevation.

    The primary spillway's effective control is the stop-log control elevation; the
    auxiliary spillway has its own fixed control (~340.0 ft) and only engages above it.
    Below the control elevation, return a small leakage seepage term."""
    rated = sp.rated_head_elev_ft

    # Primary: controlled by stop-logs.
    q_primary = _leg_flow(control_elev_ft, sp.primary.capacity_cfs_at_342, rated, h_abs_ft)

    # Auxiliary: fixed control, independent of stop-logs.
    q_aux = _leg_flow(sp.auxiliary.control_elev_ft, sp.auxiliary.capacity_cfs_at_342, rated, h_abs_ft)

    q = q_primary + q_aux

    # Board leakage just below the control elevation (matters only for recession).
    lk = sp.leakage
    if q == 0.0 and control_elev_ft - lk.active_within_ft_below_control <= h_abs_ft <= control_elev_ft:
        q = lk.cfs

    return q
