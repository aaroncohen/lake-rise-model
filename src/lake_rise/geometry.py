"""Reservoir stage-area / stage-storage geometry and the stop-log setting
(Hydrologic Reference 3). All elevations are absolute feet.

The fits are anchored between 338.8 and 343.1 ft. Above that range (dam-crest /
bridge-deck overtopping) the curves are *extrapolated*, not clamped: the model
still produces an estimate — that regime has never been gauged, so there is
nothing to clamp to — but the predictor flags any projection that leaves the
validated band via ``in_valid_range`` so the estimate is never mistaken for a
measured-range result."""

from __future__ import annotations

from datetime import datetime

from .artifact import Artifact, Geometry, SpillwayControl, StopLogs


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


def control_elev_for_stop_logs(art: Artifact, count: int) -> SpillwayControl:
    """Both spillway legs' stop-log crests (ft, absolute) for a stop-log count (0 to max)."""
    return art.spillway_control(count)


def stop_logs_installed(stop_logs: StopLogs, month: int, day: int) -> bool:
    """Whether the summer stop logs are seasonally in place on a given month/day
    (default Mar 15 - Sep 15)."""
    start_m, start_d = (int(x) for x in stop_logs.season_installed.start.split("-"))
    end_m, end_d = (int(x) for x in stop_logs.season_installed.end.split("-"))
    md = (month, day)
    return (start_m, start_d) <= md <= (end_m, end_d)


def default_stop_log_count(stop_logs: StopLogs, month: int, day: int) -> int:
    """Date-driven default per the EAP action table: summer normal stop logs in season
    (``summer_count``, 2 logs), winter normal off season (``winter_count``, 1 log, the EAP's
    0.97 ft winter pool). Pulling the aux log (0 logs, the EAP's 0.65 ft row) is a reactive,
    lake-height-driven action, not the default."""
    return stop_logs.summer_count if stop_logs_installed(stop_logs, month, day) else stop_logs.winter_count


def recorded_stop_log_count(art: Artifact, count: int | None, at: datetime) -> int:
    """The stop-log count in logs for a count recorded at ``at`` (helper history, archive hour,
    storm record): boards recorded before the switch to logs convert, and a missing count is the
    seasonal default for that date."""
    n = art.stop_logs.logs_from_record(count, at)
    return n if n is not None else default_stop_log_count(art.stop_logs, at.month, at.day)
