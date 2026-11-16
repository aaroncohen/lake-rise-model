"""Archive of the gauge's minute-by-minute readings, one ``YYYY-MM-DD.json`` per UTC day under
``data/gauge_minute/``: ``{"<entity>": [[iso time, state], ...]}``, sorted, one row per time.

HA keeps minute history ~10 days; the hourly archive job appends the last couple of hours here so
a storm can be re-checked against the lake filter later without anyone capturing it by hand."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .fsutil import atomic_write_text

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "gauge_minute"


def _dir(directory: str | Path | None) -> Path:
    if directory is not None:
        return Path(directory)
    env = os.getenv("LAKE_RISE_GAUGE_MINUTE_DIR")
    return Path(env) if env else DEFAULT_DIR


def entities(cfg) -> list[str]:
    """The HA entities kept: raw and smoothed depth, rain, gauge freshness, stop logs."""
    return [e for e in (cfg.lake_raw_sensor, cfg.lake_sensor, cfg.rain_sensor,
                        cfg.lake_fresh_sensor, cfg.stop_log_helper) if e]


def _read(path: Path) -> dict[str, list[list[str]]]:
    return json.loads(path.read_text()) if path.exists() else {}


def append(rows: dict[str, list[tuple[datetime, str]]], directory: str | Path | None = None) -> None:
    """Merge ``rows`` into the day files they fall in; a time already stored takes the new state.
    An exclusive lock on the directory's ``.lock`` file keeps an overlapping append (a manual
    ``gauge-capture`` during the hourly job) from replacing a file the other just merged."""
    d = _dir(directory)
    by_day: dict[str, dict[str, dict[datetime, str]]] = {}
    for entity, entity_rows in rows.items():
        for t, state in entity_rows:
            t = t.astimezone(timezone.utc)
            by_day.setdefault(t.strftime("%Y-%m-%d"), {}).setdefault(entity, {})[t] = state
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)              # released when the file closes
        for day, new in by_day.items():
            path = d / f"{day}.json"
            merged = {e: {datetime.fromisoformat(t): s for t, s in r} for e, r in _read(path).items()}
            for entity, states in new.items():
                merged.setdefault(entity, {}).update(states)
            out = {e: [[t.isoformat(), s] for t, s in sorted(r.items())] for e, r in sorted(merged.items())}
            atomic_write_text(path, json.dumps(out, separators=(",", ":")) + "\n")


def load(start: datetime, end: datetime, directory: str | Path | None = None
         ) -> dict[str, list[tuple[datetime, str]]]:
    """The stored rows with ``start <= time <= end``, per entity, oldest first."""
    d = _dir(directory)
    out: dict[str, list[tuple[datetime, str]]] = {}
    day = start.astimezone(timezone.utc).date()
    while day <= end.astimezone(timezone.utc).date():
        for entity, rows in _read(d / f"{day.isoformat()}.json").items():
            for t, state in rows:
                ts = datetime.fromisoformat(t)
                if start <= ts <= end:
                    out.setdefault(entity, []).append((ts, state))
        day += timedelta(days=1)
    return out
