"""Shared raw→hourly primitives: parse Home Assistant history rows and lay them on a
clock-hour grid.

Both the live source and the backtest build hourly rain/level series from the same raw HA
history. Keeping the parse and the hour-grid in one tested place is the training/serving-skew
guard the README calls for: the series served live and the series backtested/archived come out
of bit-identical transforms, so a fix (or a bug) can't drift between the two paths.
"""

from __future__ import annotations

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
