"""Continuous observation archive: a rolling long record of hourly observed lake
elevation + rain, so calibration signatures have data that survives HA's ~10-day
retention.

The signature extractors need what storm-window `StormRecord`s can't give: multi-day
RAIN-FREE recessions (for AGWRC) and multi-month continuity (for BFI/percolation). This
module keeps one growing, hour-keyed record; appending the latest HA window merges by hour
(idempotent), so an hourly job just calls `append_samples` with whatever it pulled.

Pure and framework-free; the live pull lives in the CLI/service (it reuses
`LiveHASource._backtest_inputs`).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

DEFAULT_CONTINUOUS_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "continuous" / "crystal_lake.json"
)


class HourSample(BaseModel):
    """One clock-hour of observation. ``elev_ft`` is absolute lake elevation (None if the
    gauge had no reading that hour); ``rain_in`` is that hour's rainfall (inches)."""
    hour: str                       # ISO-8601 hour (tz-aware), minute/second zeroed
    elev_ft: float | None = None
    rain_in: float = 0.0


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
    rain_hourly: list[float], rain_start: datetime, level_by_hour: dict[datetime, float]
) -> list[HourSample]:
    """Align a pulled rain series (hourly from ``rain_start``) and observed hourly levels
    into per-hour samples over the union of their hours."""
    rain_by_hour: dict[datetime, float] = {}
    for i, r in enumerate(rain_hourly):
        rain_by_hour[rain_start + timedelta(hours=i)] = r
    hours = sorted(set(rain_by_hour) | set(level_by_hour))
    out: list[HourSample] = []
    for h in hours:
        out.append(HourSample(
            hour=h.isoformat(),
            elev_ft=level_by_hour.get(h),
            rain_in=rain_by_hour.get(h, 0.0),
        ))
    return out


def load(path: str | Path | None = None) -> ContinuousRecord:
    p = Path(path) if path is not None else DEFAULT_CONTINUOUS_PATH
    if not p.exists():
        return ContinuousRecord()
    return ContinuousRecord.model_validate_json(p.read_text())


def append_samples(new: list[HourSample], path: str | Path | None = None) -> ContinuousRecord:
    """Merge ``new`` into the record by hour (new wins for a duplicated hour, unless it has a
    null elevation and the old one didn't), write sorted, and return the merged record. Atomic
    write. Idempotent: appending the same window twice is a no-op."""
    p = Path(path) if path is not None else DEFAULT_CONTINUOUS_PATH
    by_hour: dict[str, HourSample] = {s.hour: s for s in load(p).samples}
    for s in new:
        prev = by_hour.get(s.hour)
        if prev is not None and s.elev_ft is None and prev.elev_ft is not None:
            # keep a real prior reading rather than overwrite it with a gap
            s = HourSample(hour=s.hour, elev_ft=prev.elev_ft, rain_in=s.rain_in)
        by_hour[s.hour] = s
    merged = ContinuousRecord(samples=[by_hour[h] for h in sorted(by_hour)])
    _atomic_write(p, merged.model_dump_json(indent=2) + "\n")
    return merged


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)          # atomic on POSIX
    finally:
        Path(tmp).unlink(missing_ok=True)
