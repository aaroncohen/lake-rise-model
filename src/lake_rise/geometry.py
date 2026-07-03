"""Reservoir stage-area / stage-storage geometry and stop-log control elevations
(Hydrologic Reference 3). All elevations are absolute feet.

The fits are anchored between 338.8 and 343.1 ft. Above that range (dam-crest /
bridge-deck overtopping) the curves are *extrapolated*, not clamped: the model
still produces an estimate — that regime has never been gauged, so there is
nothing to clamp to — but the predictor flags any projection that leaves the
validated band via ``in_valid_range`` so the estimate is never mistaken for a
measured-range result."""

from __future__ import annotations

from .artifact import Geometry, StopLogs


def surface_area_acres(geom: Geometry, h_abs_ft: float) -> float:
    """Surface area A(h) = slope*(h - base) + intercept: the linear fit to the
    documented stage-area hard points (Reference 3.1: 75.1 ac at base pool, 96.1 ac
    at 340.0 ft). This is the area the lake-level update needs (Δh = Q·Δt·0.0826 / A).

    Deliberately NOT dS/dh of ``stage_storage``: the documented area and storage
    tables are mutually inconsistent (dS/dh at base ≈ 69.4 ac vs the documented
    75.1 ac), so no single fit satisfies both -- a source-data inconsistency, not a
    bad fit. Above ~340 ft this is a 2-point extrapolation; ``predict`` flags any
    projection past ``valid_elev_range_ft``. See the 2026-07-03 #1b calibration-log
    entry for why the linear area fit is kept over the storage derivative."""
    x = h_abs_ft - geom.datum_base_elev_ft
    return geom.stage_area.slope * x + geom.stage_area.intercept


def storage_acft(geom: Geometry, h_abs_ft: float) -> float:
    """S(h) = a*x^2 + b*x + c acre-feet (x = h - base): the fit to the documented
    stage-storage hard points (131 / 230 / 486 ac-ft at 338.8 / 340.0 / 342.15 ft).

    Not consumed by the lake-level update (which uses ``surface_area_acres``);
    retained to validate the documented storage (test_geometry_*) and for any future
    volumetric routing. NOT derivative-consistent with ``surface_area_acres`` -- the
    source area/storage tables disagree (see the 2026-07-03 #1b log entry)."""
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
    """Date-driven default per the EAP action table: summer normal is 3 boards
    (full pool, WQ 1.30'); winter normal pulls one board to 2 (WQ 0.97'). The
    further winter drawdown to 1 board (WQ 0.65') is a reactive, lake-height-driven
    action, not the default."""
    return 3 if stop_logs_installed(stop_logs, month, day) else 2
