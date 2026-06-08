"""Unit conversions. The single home for every physical constant so no magic
numbers leak into the model code (training/serving-skew guard, spec 2).

US customary throughout: inches, cfs, acres, acre-feet, feet, hours."""

from __future__ import annotations

# 1 cfs sustained for 1 hour = 0.0826 acre-feet (Hydrologic Reference 1.2 / 3.6).
CFS_TO_ACFT_PER_HR = 0.0826

# Non-leap days per month (PET is climatological, so leap precision is irrelevant).
_DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def days_in_month(month: int) -> int:
    return _DAYS_IN_MONTH[int(month) - 1]


def depth_in_to_acft(depth_in: float, area_acres: float) -> float:
    """A uniform depth (inches) over an area (acres) as a volume (acre-feet)."""
    return depth_in / 12.0 * area_acres


def acft_to_cfs(acft: float, dt_hours: float) -> float:
    """A volume (acre-feet) delivered over dt hours as an average flow (cfs).

    Inverse of CFS_TO_ACFT_PER_HR. NOTE: the Hydrologic Reference's shorthand
    ``(in/day * acres) / 43.5`` is ~half this physically-correct value; we use the
    self-consistent 0.0826 machinery so volume-based calibration anchors (Step 6)
    reproduce, and so inflow and the lake-level update share one conversion."""
    return acft / (dt_hours * CFS_TO_ACFT_PER_HR)


def depth_in_to_cfs(depth_in: float, area_acres: float, dt_hours: float) -> float:
    """A depth (inches) deposited over an area during dt hours, as cfs."""
    return acft_to_cfs(depth_in_to_acft(depth_in, area_acres), dt_hours)


def cfs_to_dh(q_net_cfs: float, dt_hours: float, area_acres: float) -> float:
    """Lake-level change (ft): Delta_h = (Q_net * dt * 0.0826) / A(h)."""
    return q_net_cfs * dt_hours * CFS_TO_ACFT_PER_HR / area_acres
