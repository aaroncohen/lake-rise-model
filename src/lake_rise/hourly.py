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


def rain_hourly_gapaware(states: list[tuple[datetime, float]], start: datetime, end: datetime,
                         staleness_h: int = 6) -> list[float | None]:
    """Per-clock-hour rainfall from the within-hour accumulator peak, but gap-aware: an hour with
    no record is ``0.0`` when a record landed within the trailing ``staleness_h`` hours (a healthy
    accumulator sits flat when dry), and ``None`` when the feed has been silent longer than that (a
    genuine gap -- recorded as missing, never a fake dry hour). See the 2026-07-03 #4 finding."""
    by_hour: dict[datetime, float] = {}
    for ts, val in states:
        by_hour[floor_hour(ts)] = max(by_hour.get(floor_hour(ts), 0.0), val)
    times = sorted(ts for ts, _ in states)
    out: list[float | None] = []
    for h in hour_grid(start, end):
        if h in by_hour:
            out.append(by_hour[h])
        else:
            out.append(0.0 if _covered(times, h, staleness_h) else None)
    return out


def level_hourly_gapaware(states: list[tuple[datetime, float]], offset: float,
                          start: datetime, end: datetime,
                          staleness_h: int = 6) -> list[float | None]:
    """Per-clock-hour absolute elevation (raw reading + ``offset``), median within the hour and
    gap-aware: an hour with no reading carries the last known level forward when a reading landed
    within the trailing ``staleness_h`` hours (a calm gauge holds steady), and is ``None`` when the
    gauge has been silent longer than that (a genuine gap)."""
    by_hour: dict[datetime, float] = {}
    buckets: dict[datetime, list[float]] = {}
    for ts, reading in states:
        buckets.setdefault(floor_hour(ts), []).append(reading + offset)
    for h, vals in buckets.items():
        by_hour[h] = statistics.median(vals)
    times = sorted(ts for ts, _ in states)
    out: list[float | None] = []
    last: float | None = None
    for h in hour_grid(start, end):
        if h in by_hour:
            last = by_hour[h]
            out.append(last)
        elif last is not None and _covered(times, h, staleness_h):
            out.append(last)                       # carry the last reading through a short gap
        else:
            out.append(None)
    return out
