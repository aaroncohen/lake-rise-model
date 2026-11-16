"""The live lake reading: a rain-adaptive, model-assisted Kalman filter over 1-minute gauge
readings (docs/plans/lake-filter.md, P1-P10). Pure; its parameters are ``art.lake_filter``.

State ``[L, a, c, b]``: level (stick ft), the scale on the model's watershed inflow, a residual
rate (ft/min) and the gauge's persistent offset (ft). Each minute the level moves by
``a*u_in + u_rest(L) + c``, the rest (rain on the lake, spillway, evaporation) unscaled; the
gauge reads ``L + b`` plus white noise. Measured rain loosens how fast ``c`` may change.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from lake_rise import units
from lake_rise.artifact import Artifact, LakeFilter
from lake_rise.geometry import in_valid_range, surface_area_acres
from lake_rise.model import m5_lake_update, m6_spillway

DT_H = 1 / 60            # one-minute steps
ANCHOR_MINUTES = 30      # the window P5 and P9 are defined on (today's anchor is a 30-min median)
HOUR_MINUTES = 60        # P10: the trend's "an hour ago"
# The page trend uses the filter's rate only after a run this long (ruling on #69): on a flat lake
# the rate's sd is ~0.04 ft/h under 1 h of history and ~0.018 at 2 h, against the 0.03 "steady" line.
TREND_MIN_MINUTES = 120
_H = np.array([1.0, 0.0, 0.0, 1.0])   # the gauge reads L + b


@dataclass(frozen=True)
class LakeEstimate:
    level_ft: float                       # stick height at the last grid minute
    rate_ft_per_h: float                  # total rate at the last grid minute
    rate_hour_ago_ft_per_h: float | None  # the same, 60 grid minutes earlier (None: run too short)
    minutes: int | None = None            # grid minutes the run filtered, from its first reading


def _floor_minute(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


def _finite(v: float | None) -> float | None:
    return v if v is not None and math.isfinite(v) else None


def _parse(state: str) -> float | None:
    try:
        return _finite(float(state))
    except (TypeError, ValueError):
        return None


def minute_grid(rows: list[tuple[datetime, str]], now: datetime, minutes: int) -> list[float | None]:
    """P8. `rows` are HA (time, state) changes, any order; states that don't parse as floats -> None.
    Element k is the last state at or before floor_minute(now) - (minutes-1-k) min."""
    rows = sorted(rows, key=lambda r: r[0])
    times = [t for t, _ in rows]
    end = _floor_minute(now)
    out: list[float | None] = []
    for k in range(minutes):
        i = bisect.bisect_right(times, end - timedelta(minutes=minutes - 1 - k))
        out.append(_parse(rows[i - 1][1]) if i else None)
    return out


def rain_by_minute(hourly_in: list[float | None], partial_hour_in: float | None,
                   now: datetime, minutes: int) -> list[float | None]:
    """Each hour's total spread evenly over its minutes; the current hour's partial total over
    its minutes so far. Aligned with minute_grid. None = unknown (P4).
    `hourly_in` is oldest first, the last one the hour ending at the top of the current hour."""
    so_far = now.minute + 1                     # grid minutes in the current hour, incl. now's
    out: list[float | None] = []
    for k in range(minutes):
        back = minutes - 1 - k                  # minutes before now's minute
        if back < so_far:
            out.append(None if partial_hour_in is None else partial_hour_in / so_far)
            continue
        j = (back - so_far) // 60               # 0 = the hour ending at the top of this hour
        total = hourly_in[-1 - j] if j < len(hourly_in) else None
        out.append(None if total is None else total / 60)
    return out


def inflow_by_minute(hourly_cfs: list[float] | None, as_of: datetime, now: datetime,
                     minutes: int) -> list[float] | None:
    """The replay's hourly inflow per grid minute (aligned with minute_grid): the replay hour
    containing the minute; minutes from ``as_of`` on hold its last hour (the live replay ends at
    the top of the current hour). ``hourly_cfs`` is oldest first, the last the hour ending at
    ``as_of``. None without a replay, or when it is non-finite or doesn't reach back over the grid."""
    q = hourly_cfs
    if not q or not all(math.isfinite(v) for v in q):
        return None
    end = _floor_minute(now)
    out = []
    for k in range(minutes):
        t = end - timedelta(minutes=minutes - 1 - k)
        if t >= as_of:
            out.append(q[-1])
            continue
        back = (int((as_of - t).total_seconds() // 60) - 1) // 60   # 0 = the hour ending at as_of
        if back >= len(q):
            return None
        out.append(q[-1 - back])
    return out


def wetness(f: LakeFilter, rain_in_by_minute: list[float | None]) -> np.ndarray:
    """P4: min(1, rain over the trailing window / wet_full_in) per minute; a minute whose rain is
    unknown makes it and every minute after it within the window fully wet."""
    w = int(round(f.wet_window_hours * 60))
    unknown = np.array([r is None for r in rain_in_by_minute], dtype=float)
    rain = np.array([0.0 if r is None else r for r in rain_in_by_minute])

    def trailing(x):
        c = np.concatenate([[0.0], np.cumsum(x)])
        n = np.arange(1, len(x) + 1)
        return c[n] - c[np.maximum(n - w, 0)]

    wet = np.minimum(1.0, trailing(rain) / f.wet_full_in)
    return np.where(trailing(unknown) > 0, 1.0, wet)


def estimate_lake(art: Artifact, readings: list[float | None], rain_in_by_minute: list[float | None],
                  *, inflow_cfs_by_minute: list[float] | None, control_elev_abs: float, month: int
                  ) -> LakeEstimate | None:
    """readings in stick ft, control_elev_abs absolute (stick + art.datum.sensor_to_absolute_offset_ft).
    All series aligned per P8, ending at the same minute. ``rain_in_by_minute`` may be longer than
    ``readings`` (a P7 cut shortens only the run): its earlier minutes still count toward the
    wetness. inflow None -> u_in = 0. Returns None per P9."""
    f = art.lake_filter
    off = art.datum.sensor_to_absolute_offset_ft
    # A NaN would propagate into a non-None NaN level and defeat the fallback, and a reading off
    # the lake's geometry is a sensor fault: treat both as missing. Except above the top: that may
    # be a real flood, and ignoring it would hold a stale, lower level, so today's median takes over.
    readings = [_finite(r) for r in readings]
    top = art.geometry.valid_elev_range_ft[1]
    if any(r is not None and r + off > top for r in readings[-ANCHOR_MINUTES:]):
        return None
    readings = [r if r is not None and in_valid_range(art.geometry, r + off) else None
                for r in readings]
    rain_in_by_minute = [_finite(r) for r in rain_in_by_minute]
    n = len(readings)
    if f is None or n < ANCHOR_MINUTES:
        return None
    if len(rain_in_by_minute) < n or (inflow_cfs_by_minute is not None
                                      and len(inflow_cfs_by_minute) != n):
        raise ValueError("the inflow must be as long as the readings, the rain at least as long")
    if sum(r is not None for r in readings[-ANCHOR_MINUTES:]) < f.min_valid_minutes_last_30:
        return None

    wet = wetness(f, rain_in_by_minute)[-n:]
    rain_in_by_minute = rain_in_by_minute[-n:]
    phi = math.exp(-1 / f.offset_minutes)
    R = f.noise_sd_ft ** 2

    def drives(i: int, level: float) -> tuple[float, float]:
        """(u_in, u_rest) for minute i at stick level `level` (P3)."""
        h = level + off
        q_in = 0.0 if inflow_cfs_by_minute is None else inflow_cfs_by_minute[i]
        u_in = units.cfs_to_dh(q_in, DT_H, surface_area_acres(art.geometry, h))
        rain = rain_in_by_minute[i] or 0.0
        h_new = m5_lake_update(art, h, 0.0, rain, m6_spillway(art, h, control_elev_abs), DT_H, month)[0]
        return u_in, h_new - h

    def rate_ft_per_h(i: int, x: np.ndarray) -> float:
        u_in, u_rest = drives(i, x[0])
        return round(float(x[1] * u_in + u_rest + x[2]) * 60, 3)

    valid = [i for i, z in enumerate(readings) if z is not None]
    if not valid or n - valid[0] < ANCHOR_MINUTES:     # P9: under 30 minutes of history
        return None
    start = valid[0]
    x = np.array([float(np.median([readings[i] for i in valid[:ANCHOR_MINUTES]])), 1.0, 0.0, 0.0])
    P = np.diag([R + f.offset_sd_ft ** 2, f.init_scale_sd ** 2,
                 (f.init_rate_sd_ft_per_h / 60) ** 2, f.offset_sd_ft ** 2])
    hour_ago = n - 1 - HOUR_MINUTES
    rate_hour_ago = None
    for i in range(start, n):
        if i > start:
            u_in, u_rest = drives(i, x[0])
            F = np.array([[1.0, u_in, 1.0, 0.0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, phi]])
            rate_sd = f.rate_sd_dry_ft_per_min + (f.rate_sd_wet_ft_per_min - f.rate_sd_dry_ft_per_min) * wet[i]
            Q = np.diag([f.level_sd_ft ** 2, f.scale_sd ** 2, rate_sd ** 2,
                         f.offset_sd_ft ** 2 * (1 - phi ** 2)])
            x = np.array([x[0] + x[1] * u_in + u_rest + x[2], x[1], x[2], phi * x[3]])
            x[1] = min(max(x[1], 0.0), f.scale_max)
            P = F @ P @ F.T + Q
        z = readings[i]
        if z is not None:
            y = z - _H @ x
            S = _H @ P @ _H + R
            gate = f.gate_sigmas * math.sqrt(S)
            r = R if abs(y) <= gate else R * (abs(y) / gate) ** 2   # Huber: down-weight outliers
            K = P @ _H / (_H @ P @ _H + r)
            x = x + K * y
            x[1] = min(max(x[1], 0.0), f.scale_max)
            P = P - np.outer(K, _H @ P)
        if i == hour_ago:
            rate_hour_ago = rate_ft_per_h(i, x)
    est = LakeEstimate(level_ft=float(x[0]), rate_ft_per_h=rate_ft_per_h(n - 1, x),
                       rate_hour_ago_ft_per_h=rate_hour_ago, minutes=n - start)
    if not all(math.isfinite(v) for v in (est.level_ft, est.rate_ft_per_h, est.rate_hour_ago_ft_per_h)
               if v is not None):
        return None                                  # never hand on a NaN level: the median takes over
    return est
