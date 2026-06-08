"""Reservoir stage-area / stage-storage geometry and stop-log control elevations
(Hydrologic Reference 3). All elevations are absolute feet.

Valid only between 338.8 and 343.1 ft; values are clamped with a warning flag
rather than silently extrapolated."""

from __future__ import annotations

from .artifact import Geometry, StopLogs


def surface_area_acres(geom: Geometry, h_abs_ft: float) -> float:
    """A(h) = slope * (h - base) + intercept.  Also equals dS/dh."""
    x = h_abs_ft - geom.datum_base_elev_ft
    return geom.stage_area.slope * x + geom.stage_area.intercept


def storage_acft(geom: Geometry, h_abs_ft: float) -> float:
    """S(h) = a*x^2 + b*x + c  acre-feet, x = h - base."""
    x = h_abs_ft - geom.datum_base_elev_ft
    s = geom.stage_storage
    return s.a * x * x + s.b * x + s.c


def in_valid_range(geom: Geometry, h_abs_ft: float) -> bool:
    lo, hi = geom.valid_elev_range_ft
    return lo <= h_abs_ft <= hi


def control_elev_for_stop_logs(stop_logs: StopLogs, count: int) -> float:
    """Control elevation (ft, absolute) for a given stop-log count (0-3)."""
    return stop_logs.control_elev(count)


def stop_logs_installed(stop_logs: StopLogs, month: int, day: int) -> bool:
    """Whether boards are seasonally in place on a given month/day
    (default Mar 15 - Sep 15)."""
    start_m, start_d = (int(x) for x in stop_logs.season_installed.start.split("-"))
    end_m, end_d = (int(x) for x in stop_logs.season_installed.end.split("-"))
    md = (month, day)
    return (start_m, start_d) <= md <= (end_m, end_d)


def default_stop_log_count(stop_logs: StopLogs, month: int, day: int) -> int:
    """Date-driven default: 3 boards in season, 0 out of season."""
    return 3 if stop_logs_installed(stop_logs, month, day) else 0
