"""Continuous observation archive: a rolling long record of hourly observed lake
elevation + rain, so calibration signatures and the initial-state estimator have data that
survives HA's ~10-day retention.

Storage is **sharded by UTC day** -- one small ``YYYY-MM-DD.json`` per day under
``data/continuous/crystal_lake/`` -- so the hourly append rewrites only the current day (tiny,
atomic), completed days are immutable (trivially incremental-backup-friendly), and a reader can
pull just the window it needs (`load_window`) instead of loading the whole history into memory.

Each ``HourSample`` distinguishes *observed* from *missing*: ``elev_ft`` / ``rain_in`` are
``None`` when the gauge / rain feed had no data that hour (preserved as an explicit gap), vs a
real number (``rain_in == 0.0`` is observed-dry, not missing). Merging never overwrites a real
value with a gap, so re-pulling HA fills recoverable holes while genuinely-missing hours stay
``None``.

Pure and framework-free; the live pull lives in the CLI/service (it reuses
``LiveHASource._backtest_inputs``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from ..fsutil import atomic_write_text

_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "continuous"
# Sharded storage lives under this directory (one YYYY-MM-DD.json per day). A pre-sharding
# monolith (the ``<name>.json`` sibling) is migrated into shards on first load; see _migrate_legacy.
DEFAULT_CONTINUOUS_DIR = _DATA_DIR / "crystal_lake"


class HourSample(BaseModel):
    """One clock-hour of observation. ``elev_ft`` is absolute lake elevation (``None`` if the
    gauge had no reading that hour); ``rain_in`` is that hour's rainfall in inches (``None`` if
    the rain feed was missing that hour -- distinct from an observed ``0.0`` dry hour)."""
    hour: str                       # ISO-8601 hour (tz-aware), minute/second zeroed
    elev_ft: float | None = None
    rain_in: float | None = None

    @property
    def elev_missing(self) -> bool:
        return self.elev_ft is None

    @property
    def rain_missing(self) -> bool:
        return self.rain_in is None


class ContinuousRecord(BaseModel):
    samples: list[HourSample] = []   # sorted ascending by hour

    def times(self) -> list[datetime]:
        return [datetime.fromisoformat(s.hour) for s in self.samples]

    def span_hours(self) -> int:
        if len(self.samples) < 2:
            return len(self.samples)
        t = self.times()
        return int((t[-1] - t[0]).total_seconds() // 3600) + 1


def samples_from_backtest_inputs(
    rain_hourly: list[float | None], rain_start: datetime,
    level_by_hour: dict[datetime, float],
) -> list[HourSample]:
    """Align a pulled rain series (hourly from ``rain_start``, ``None`` for a missing hour) and
    observed hourly levels into per-hour samples over the union of their hours."""
    rain_by_hour: dict[datetime, float | None] = {}
    for i, r in enumerate(rain_hourly):
        rain_by_hour[rain_start + timedelta(hours=i)] = r
    hours = sorted(set(rain_by_hour) | set(level_by_hour))
    out: list[HourSample] = []
    for h in hours:
        out.append(HourSample(
            hour=h.isoformat(),
            elev_ft=level_by_hour.get(h),
            rain_in=rain_by_hour.get(h),
        ))
    return out


# --- day-sharded storage ----------------------------------------------------------------

def _day_of(hour_iso: str) -> str:
    return datetime.fromisoformat(hour_iso).strftime("%Y-%m-%d")


def _day_file(directory: Path, day: str) -> Path:
    return directory / f"{day}.json"


def _read_day(path: Path) -> list[HourSample]:
    if not path.exists():
        return []
    return [HourSample.model_validate(s) for s in _read_json_list(path)]


def _read_json_list(path: Path) -> list[dict]:
    import json
    data = json.loads(path.read_text())
    return data if isinstance(data, list) else data.get("samples", [])


def _write_day(path: Path, samples: list[HourSample]) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [s.model_dump() for s in sorted(samples, key=lambda s: s.hour)]
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def _merge_hour(prev: HourSample | None, new: HourSample) -> HourSample:
    """Merge two samples for the same hour: a real value never yields to a gap; when both are
    present the newer wins. So re-pulling HA fills recoverable holes and never erases data."""
    if prev is None:
        return new
    return HourSample(
        hour=new.hour,
        elev_ft=new.elev_ft if new.elev_ft is not None else prev.elev_ft,
        rain_in=new.rain_in if new.rain_in is not None else prev.rain_in,
    )


def _merge_into_shards(new: list[HourSample], directory: Path) -> list[HourSample]:
    """Group ``new`` by day, per-hour merge (gap-preserving) into each day shard, and rewrite
    only the touched files atomically. Returns the merged samples for the affected days. Does NOT
    trigger migration -- callers handle that once, up front (avoids re-entrancy)."""
    by_day: dict[str, list[HourSample]] = {}
    for s in new:
        by_day.setdefault(_day_of(s.hour), []).append(s)
    merged_all: list[HourSample] = []
    for day, day_new in by_day.items():
        path = _day_file(directory, day)
        by_hour = {s.hour: s for s in _read_day(path)}
        for s in day_new:
            by_hour[s.hour] = _merge_hour(by_hour.get(s.hour), s)
        day_samples = [by_hour[h] for h in sorted(by_hour)]
        _write_day(path, day_samples)
        merged_all.extend(day_samples)
    merged_all.sort(key=lambda s: s.hour)
    return merged_all


def _legacy_path_for(directory: Path) -> Path:
    """The pre-sharding monolith that a shard directory supersedes: its ``<name>.json`` sibling
    (so the default ``.../crystal_lake/`` migrates ``.../crystal_lake.json`` and a test's throwaway
    directory only ever migrates its own sibling, never the real archive)."""
    return directory.parent / f"{directory.name}.json"


def _migrate_legacy(directory: Path) -> None:
    """One-time: split a pre-sharding monolith into day shards, then retire it. Writes shards
    directly (not via ``append_samples``) so it can't recurse back into migration."""
    legacy = _legacy_path_for(directory)
    if not legacy.exists():
        return
    record = ContinuousRecord.model_validate_json(legacy.read_text())
    _merge_into_shards(record.samples, directory)
    legacy.rename(legacy.with_suffix(".json.migrated"))


def _dir(directory: str | Path | None) -> Path:
    return Path(directory) if directory is not None else DEFAULT_CONTINUOUS_DIR


def load_window(start: datetime, end: datetime,
                directory: str | Path | None = None) -> ContinuousRecord:
    """Load only the samples in ``[start, end]`` by reading just the day shards that cover it --
    the bounded read the hourly append and the state estimator use (never the whole history)."""
    d = _dir(directory)
    _migrate_legacy(d)
    samples: list[HourSample] = []
    day = datetime(start.year, start.month, start.day, tzinfo=start.tzinfo)
    last = datetime(end.year, end.month, end.day, tzinfo=end.tzinfo)
    while day <= last:
        for s in _read_day(_day_file(d, day.strftime("%Y-%m-%d"))):
            t = datetime.fromisoformat(s.hour)
            if start <= t <= end:
                samples.append(s)
        day += timedelta(days=1)
    samples.sort(key=lambda s: s.hour)
    return ContinuousRecord(samples=samples)


def load(directory: str | Path | None = None) -> ContinuousRecord:
    """Load the entire record across all day shards (for the occasional operator-invoked
    training/signatures pass). Prefer ``load_window`` on the hot paths."""
    d = _dir(directory)
    _migrate_legacy(d)
    samples: list[HourSample] = []
    if d.exists():
        for p in sorted(d.glob("*.json")):
            samples.extend(_read_day(p))
    samples.sort(key=lambda s: s.hour)
    return ContinuousRecord(samples=samples)


def append_samples(new: list[HourSample],
                   directory: str | Path | None = None) -> ContinuousRecord:
    """Merge ``new`` into the day shards it touches (per-hour, gap-preserving; see
    ``_merge_hour``), rewriting only those day files atomically. Idempotent. Returns the merged
    record for the affected days."""
    d = _dir(directory)
    _migrate_legacy(d)
    return ContinuousRecord(samples=_merge_into_shards(new, d))
