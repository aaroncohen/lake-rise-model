"""Storm-truth records: frozen real storm windows for OFFLINE backtesting.

The live ``/backtest`` only sees the last ~10 days of Home Assistant history, and the
historical-storm catalog has no paired observed lake levels. To score parameters against
real storms -- repeatedly, offline, without a live HA connection -- we snapshot the exact
inputs ``run_backtest`` consumes (trailing+forward rain, the observed hourly gauge, the T0
anchor, control elevation) into a ``StormRecord`` JSON. A growing ``data/backtest_storms/``
set then becomes the objective ground truth the sensitivity sweep and the auto-calibrator
optimise against.

A record freezes the OBSERVATIONS, never the model output -- scoring re-runs the model, so a
record stays valid as the model/parameters change. ``run_backtest`` is pure and
network-free, so scoring is deterministic and side-effect-free.

Data-quality caveats travel with each record (``data_fresh``, ``notes``): the gauge is
per-hour-median denoised (~0.2 in residual noise), rain is accumulator-approximate, and
retention is ~10 d (no HA recorder statistics). Treat a single storm as weak evidence --
the whole point of the dataset is to accumulate many.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from . import backtest
from .artifact import Artifact
from .fsutil import atomic_write_text

DEFAULT_STORM_DIR = Path(__file__).resolve().parents[2] / "data" / "backtest_storms"


class StormRecord(BaseModel):
    """The frozen inputs to one ``run_backtest`` call, plus capture provenance. All times are
    ISO-8601 (tz-aware). ``level_by_hour`` maps ISO hour -> observed ABSOLUTE elevation (ft)."""
    # provenance / metadata
    label: str
    captured_at: str
    source: str = "live_ha"
    notes: str = ""
    data_fresh: bool = True                     # was the gauge/feed healthy over the window?

    # frozen run_backtest inputs (the observations -- never the model output)
    rain_start: str
    rain_hourly: list[float]
    level_by_hour: dict[str, float]
    t0: str
    now: str
    control_elev: float
    stop_log_count: int | None = None
    anchor_h0: float | None = None
    sm0: float | None = None


def save(record: StormRecord, path: str | Path) -> None:
    atomic_write_text(path, record.model_dump_json(indent=2) + "\n")


def load(path: str | Path) -> StormRecord:
    return StormRecord.model_validate_json(Path(path).read_text())


def load_dataset(directory: str | Path | None = None) -> list[StormRecord]:
    """Load every ``*.json`` StormRecord in a directory (default ``data/backtest_storms``),
    sorted by capture time. Skips files that don't parse as a StormRecord."""
    d = Path(directory) if directory is not None else DEFAULT_STORM_DIR
    records: list[StormRecord] = []
    if not d.exists():
        return records
    for p in sorted(d.glob("*.json")):
        try:
            records.append(load(p))
        except Exception:  # noqa: BLE001 -- a stray/invalid file shouldn't break the dataset
            continue
    records.sort(key=lambda r: r.captured_at)
    return records


def score(art: Artifact, record: StormRecord) -> dict[str, Any]:
    """Replay one stored storm against ``art`` and return the ``run_backtest`` result
    (metrics + trajectories). Pure: no network, no mutation of ``art`` or the record."""
    level_by_hour = {datetime.fromisoformat(k): v for k, v in record.level_by_hour.items()}
    return backtest.run_backtest(
        art,
        rain_hourly=record.rain_hourly,
        rain_start=datetime.fromisoformat(record.rain_start),
        level_by_hour=level_by_hour,
        t0=datetime.fromisoformat(record.t0),
        now=datetime.fromisoformat(record.now),
        control_elev=record.control_elev,
        sm0=record.sm0,
        anchor_h0=record.anchor_h0,
    )


# error metrics we aggregate across the dataset (the objective an optimiser minimises)
_MAGNITUDE = ("rmse_ft", "mae_ft", "max_err_ft")


def score_dataset(art: Artifact, records: list[StormRecord]) -> dict[str, Any]:
    """Score ``art`` against every record and return per-storm metrics plus an aggregate
    (mean magnitude error, mean |peak| and |timing| error, and within-target counts).
    Records whose windows share no predicted/actual hours (metrics all None) are skipped in
    the aggregate but still listed."""
    per_storm: list[dict[str, Any]] = []
    scored: list[dict[str, Any]] = []
    for rec in records:
        m = score(art, rec)["metrics"]
        per_storm.append({"label": rec.label, "data_fresh": rec.data_fresh, **m})
        if m.get("rmse_ft") is not None:
            scored.append(m)

    def _mean(key: str, abs_: bool = False) -> float | None:
        vals = [abs(m[key]) if abs_ else m[key] for m in scored if m.get(key) is not None]
        return round(statistics.mean(vals), 3) if vals else None

    aggregate: dict[str, Any] = {
        "n_records": len(records),
        "n_scored": len(scored),
        "mean_rmse_ft": _mean("rmse_ft"),
        "mean_mae_ft": _mean("mae_ft"),
        "mean_abs_peak_err_ft": _mean("peak_err_ft", abs_=True),
        "mean_abs_peak_timing_err_h": _mean("peak_timing_err_h", abs_=True),
        "peak_within_target": sum(1 for m in scored if m.get("peak_within_target")),
        "timing_within_target": sum(1 for m in scored if m.get("timing_within_target")),
    }
    return {"aggregate": aggregate, "per_storm": per_storm}
