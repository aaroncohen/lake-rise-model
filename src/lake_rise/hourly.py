"""Shared raw→hourly primitives: parse Home Assistant history rows and lay them on a
clock-hour grid.

Both the live source and the backtest build hourly rain/level series from the same raw HA
history. Keeping the parse and the hour-grid in one tested place is the training/serving-skew
guard the README calls for: the series served live and the series backtested/archived come out
of bit-identical transforms, so a fix (or a bug) can't drift between the two paths.
"""

from __future__ import annotations

import bisect
import statistics
from collections.abc import Iterable
from datetime import datetime, timedelta


def floor_hour(ts: datetime) -> datetime:
    """Truncate a timestamp to the top of its clock hour (minute/second/microsecond zeroed)."""
    return ts.replace(minute=0, second=0, microsecond=0)


def parse_ha_rows(rows: Iterable[dict], ts_key: str = "last_changed") -> list[tuple[datetime, float]]:
    """Parse Home Assistant history/state rows into ``(tz-aware timestamp, float value)`` pairs,
    preserving input order. Rows whose value is unknown/unavailable or whose timestamp is missing
    or unparseable are skipped (never raised) -- HA emits such rows routinely."""
    out: list[tuple[datetime, float]] = []
    for row in rows:
        try:
            value = float(row["state"])
            ts = datetime.fromisoformat(str(row.get(ts_key, "")).replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            continue  # skip unknown/unavailable/unparseable
        out.append((ts, value))
    return out


def parse_ha_states(rows: Iterable[dict], ts_key: str = "last_changed") -> list[tuple[datetime, float | None]]:
    """Like ``parse_ha_rows`` but keeps unknown/unavailable rows as ``None`` instead of dropping
    them, sorted by time -- so a consumer can tell "the sensor went unavailable" (a real gap) from
    "the sensor held its value" (no row written). Rows with no parseable timestamp are skipped."""
    out: list[tuple[datetime, float | None]] = []
    for row in rows:
        try:
            ts = datetime.fromisoformat(str(row.get(ts_key, "")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        try:
            value: float | None = float(row["state"])
        except (KeyError, ValueError, TypeError):
            value = None
        out.append((ts, value))
    out.sort(key=lambda p: p[0])
    return out


def hour_grid(start: datetime, end: datetime) -> list[datetime]:
    """The contiguous clock hours ``[floor_hour(start), floor_hour(end))`` -- one datetime per
    hour, empty if ``end <= start``. This is the canonical hourly index a series is laid onto."""
    h0 = floor_hour(start)
    h1 = floor_hour(end)
    n = max(0, int((h1 - h0).total_seconds() // 3600))
    return [h0 + timedelta(hours=i) for i in range(n)]


def _covered(times: list[datetime], hour: datetime, staleness_h: int) -> bool:
    """True if a raw record landed within the trailing ``staleness_h`` hours ending at the end of
    ``hour`` -- i.e. the feed was recently alive, so an empty hour is real (dry/steady), not a gap.
    Beyond the horizon (no record for > staleness_h) the hour is genuinely missing."""
    h_end = hour + timedelta(hours=1)
    i = bisect.bisect_right(times, h_end) - 1
    return i >= 0 and (h_end - times[i]) <= timedelta(hours=staleness_h)


# A decrease of the daily accumulator counts as its midnight reset only if it drops by more than
# this fraction -- Home Assistant's own rule for ``total_increasing`` sensors, so raw-history
# rain and HA's long-term ``change`` statistic agree. A smaller dip is jitter, not a reset.
RESET_DROP_FRACTION = 0.10


def rain_hourly_from_daily_accumulator(states: list[tuple[datetime, float | None]], start: datetime,
                                       end: datetime) -> list[float | None]:
    """Per-clock-hour rainfall as the increase of a since-midnight accumulator
    (``sensor.gw3000b_daily_rain_piezo``) over each hour -- the same quantity as HA's long-term
    hourly statistic ``change`` for that sensor, so archived and backfilled rain agree.

    ``states`` come from ``parse_ha_states`` (``None`` = unknown/unavailable). HA writes a row only
    when the value changes, so a flat accumulator with no rows is observed-dry (``0.0``), never a
    gap; an hour is ``None`` only when the sensor was unavailable at some point in it, or there is no
    reading yet to measure from. A decrease is the midnight reset: the new value is that step's rain.

    Not ``hourly_rain_piezo``: that sensor is a *rolling* 60-min total, so its max per clock hour
    counts every burst in two adjacent hours (~1.6x high; see the 2026-11-04 calibration-log entry)."""
    pts = sorted(states, key=lambda p: p[0])
    out: list[float | None] = []
    i = 0
    cur: float | None = None
    grid = hour_grid(start, end)
    if grid:
        while i < len(pts) and pts[i][0] <= grid[0]:      # the value held at the first hour's start
            cur = pts[i][1]
            i += 1
    for h in grid:
        h_end = h + timedelta(hours=1)
        missing = cur is None
        total = 0.0
        while i < len(pts) and pts[i][0] < h_end:
            v = pts[i][1]
            if v is None:
                missing = True
                cur = None
            elif cur is None:
                cur = v                              # a new baseline (start, or after a gap)
            elif v >= cur:
                total += v - cur
                cur = v
            elif v < (1.0 - RESET_DROP_FRACTION) * cur:
                total += v                           # the midnight reset: the new value is rain
                cur = v
            # else: a small dip is sensor jitter -- ignore it and keep the high-water baseline
            i += 1
        out.append(None if missing else round(total, 4))
    return out


def level_hourly_gapaware(states: list[tuple[datetime, float | None]], offset: float,
                          start: datetime, end: datetime,
                          staleness_h: int = 6) -> list[float | None]:
    """Per-clock-hour absolute elevation (raw reading + ``offset``), median within the hour and
    gap-aware: an hour with no reading carries the last known level forward when a reading landed
    within the trailing ``staleness_h`` hours (a calm gauge holds steady), and is ``None`` when the
    gauge has been silent longer than that (a genuine gap).

    ``states`` may include ``None`` rows (``parse_ha_states``: the sensor went unknown /
    unavailable, e.g. the ultrasonic lost the surface echo). A ``None`` row ends the carry-forward:
    the level is not held through an invalid stretch, so a frozen value is never archived as data."""
    by_hour: dict[datetime, float] = {}
    buckets: dict[datetime, list[float]] = {}
    for ts, reading in states:
        if reading is not None:
            buckets.setdefault(floor_hour(ts), []).append(reading + offset)
    for h, vals in buckets.items():
        by_hour[h] = statistics.median(vals)
    times = sorted(ts for ts, v in states if v is not None)
    invalid_since = sorted(ts for ts, v in states if v is None)
    out: list[float | None] = []
    last: float | None = None
    last_ts: datetime | None = None
    for h in hour_grid(start, end):
        if h in by_hour:
            last = by_hour[h]
            last_ts = max(ts for ts, v in states if v is not None and floor_hour(ts) == h)
            out.append(last)
        elif (last is not None and _covered(times, h, staleness_h)
              and not any(last_ts < t < h + timedelta(hours=1) for t in invalid_since)):
            out.append(last)                       # carry the last reading through a short gap
        else:
            out.append(None)
    return out
