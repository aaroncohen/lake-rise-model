"""Archive of ensemble rain forecasts for the site (NBM percentiles, ECMWF members), one small
JSON file per source and cycle under ``data/qpf/<source>/``, so they can be scored against the
gauge (``qpf_verify``) and used to calibrate the model's rain scenarios.

``update`` fills missing 00Z/12Z cycles newest-first, a few per call, back to ``since``: the
hourly job keeps up with new cycles and backfills history a little at a time; the CLI can run
the whole backfill at once. Cycles a source never published (older than ``GIVE_UP_AFTER``, long
enough to ride out an NWS outage that delays publishing) are remembered so they aren't retried
forever; a pending one costs one small index request an hour. Only one update per source runs
at a time against an archive directory (a lock file), so a manual backfill and the hourly job
never fetch the same cycle twice; a second machine must not backfill the same history -- copy
the files instead.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .fsutil import atomic_write_text

log = logging.getLogger("lake_rise.qpf")

DEFAULT_QPF_DIR = Path(__file__).resolve().parents[2] / "data" / "qpf"
CYCLE_HOURS = (0, 12)
# How long after the cycle time each source has usually published its first three days.
PUBLISH_LAG = {"nbm": timedelta(hours=8), "ecmwf_ens": timedelta(hours=9)}
GIVE_UP_AFTER = timedelta(days=7)
# Our gauge's gap-free rain record starts here; earlier forecasts have nothing to score against.
DEFAULT_SINCE = datetime(2026, 2, 21, tzinfo=timezone.utc)


def _dir(directory: str | Path | None) -> Path:
    if directory is not None:
        return Path(directory)
    env = os.getenv("LAKE_RISE_QPF_DIR")
    return Path(env) if env else DEFAULT_QPF_DIR


def _path(source: str, cycle: datetime, directory: str | Path | None) -> Path:
    return _dir(directory) / source / f"{cycle.strftime('%Y-%m-%dT%HZ')}.json"


def _missing_path(source: str, directory: str | Path | None) -> Path:
    return _dir(directory) / source / "unpublished.json"


@contextlib.contextmanager
def _exclusive(source: str, directory: str | Path | None):
    """Hold the source's update lock, or yield False at once if another update holds it."""
    lock = _dir(directory) / source / ".update.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def save(record: dict, directory: str | Path | None = None) -> Path:
    cycle = datetime.fromisoformat(record["cycle"])
    p = _path(record["source"], cycle, directory)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, json.dumps(record, separators=(",", ":")) + "\n")
    return p


def load(source: str, directory: str | Path | None = None) -> list[dict]:
    d = _dir(directory) / source
    if not d.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(d.glob("*Z.json"))]


def cycles_between(start: datetime, end: datetime) -> list[datetime]:
    """The 00Z/12Z cycles in ``[start, end]``, oldest first."""
    t = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    while t.hour not in CYCLE_HOURS:
        t += timedelta(hours=1)
    out = []
    while t <= end:
        out.append(t)
        t += timedelta(hours=12)
    return out


def update(source: str, fetch: Callable[[datetime], dict | None], *,
           since: datetime = DEFAULT_SINCE, max_cycles: int | None = None,
           budget_s: float | None = None, now: datetime | None = None,
           directory: str | Path | None = None) -> dict:
    """Fetch and store up to ``max_cycles`` missing cycles, newest first, starting no new cycle
    once ``budget_s`` seconds have passed. Returns counts, or ``{"skipped": ...}`` when another
    update of this source is already running."""
    with _exclusive(source, directory) as mine:
        if not mine:
            log.info("qpf %s: another update is running; skipped", source)
            return {"skipped": "another update of this source is running"}
        return _update(source, fetch, since=since, max_cycles=max_cycles, budget_s=budget_s,
                       now=now, directory=directory)


def _update(source: str, fetch: Callable[[datetime], dict | None], *, since: datetime,
            max_cycles: int | None, budget_s: float | None, now: datetime | None,
            directory: str | Path | None) -> dict:
    t0 = time.monotonic()
    now = now or datetime.now(timezone.utc)
    missing_file = _missing_path(source, directory)
    gave_up = set(json.loads(missing_file.read_text())) if missing_file.exists() else set()
    todo = [c for c in reversed(cycles_between(since, now - PUBLISH_LAG[source]))
            if not _path(source, c, directory).exists() and c.isoformat() not in gave_up]
    stats = {"stored": 0, "unpublished": 0, "failed": 0, "remaining": len(todo)}
    for cycle in todo[:max_cycles]:
        if budget_s is not None and time.monotonic() - t0 > budget_s:
            break
        try:
            rec = fetch(cycle)
        except Exception as exc:  # noqa: BLE001 -- one bad cycle mustn't stop the rest
            log.warning("qpf %s %s: fetch failed: %s", source, cycle.isoformat(), exc)
            stats["failed"] += 1
            continue
        if rec is None:
            stats["unpublished"] += 1
            if now - cycle > GIVE_UP_AFTER:
                gave_up.add(cycle.isoformat())
            continue
        save(rec, directory)
        stats["stored"] += 1
        stats["remaining"] -= 1
    if gave_up:
        missing_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(missing_file, json.dumps(sorted(gave_up)) + "\n")
    return stats


def fetchers(latitude: float, longitude: float) -> dict[str, Callable[[datetime], dict | None]]:
    """The sources' cycle fetchers for the site."""
    from .sources import ecmwf_ens, nbm

    return {"nbm": lambda c: nbm.fetch_cycle(c, latitude, longitude),
            "ecmwf_ens": lambda c: ecmwf_ens.fetch_cycle(c, latitude, longitude)}
