"""Forecast of record: what the hourly alert job predicted, kept so forecasts can be scored
against what happened -- the forecast rain vs the gauge's, and each scenario's trajectory vs the
lake, at every lead. Nothing else records a forecast once the next hour replaces it, and the
placeholder lead-time bands (``uncertainty.lead_ratio_by_day``) can only be fitted from this.

Append-only JSON Lines, one file per UTC day of ``generated_at`` under ``data/forecasts/`` (like
the continuous archive: completed days are immutable). One line per run, a few KB. The optional
``qpf_outlook`` is the forecast source's full hourly horizon (Apple WeatherKit: 168 h), which
reaches past the 72 h the alert job models. The optional ``nws_outlook`` is the NWS gridpoint
forecast for the same place (``sources/nws.py``: QPF in 6 h blocks, PoP intervals), recorded so
the two sources can be scored against the gauge's rain.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bundle import InputBundle
from .predict import PredictionResult

DEFAULT_FORECAST_DIR = Path(__file__).resolve().parents[2] / "data" / "forecasts"


def _dir(directory: str | Path | None) -> Path:
    if directory is not None:
        return Path(directory)
    env = os.getenv("LAKE_RISE_FORECAST_LOG_DIR")
    return Path(env) if env else DEFAULT_FORECAST_DIR


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(float(x), nd)


def forecast_record(bundle: InputBundle, result: PredictionResult, *, artifact_version: str,
                    qpf_outlook: list[dict] | None = None,
                    nws_outlook: dict | None = None) -> dict[str, Any]:
    """One forecast-of-record line: the inputs that drove the run and what it predicted."""
    rain_by_name = {s.name: s.hourly_in for s in bundle.forecast_scenarios}
    scenarios = {}
    for sc in result.scenarios:
        scenarios[sc.name] = {
            "rain_hourly_in": [_r(v, 4) for v in rain_by_name.get(sc.name, [])],
            "elev_hourly_ft": [_r(p.elevation) for p in sc.trajectory],
            "peak_elevation_ft": _r(sc.peak_elevation),
            "hours_to_early_warning": sc.hours_to_early_warning,
            "hours_to_crest": sc.hours_to_crest,
            "hours_to_bridge_deck": sc.hours_to_bridge_deck,
        }
    return {
        "generated_at": result.generated_at.isoformat(),
        "as_of": bundle.as_of.isoformat(),
        "model_version": result.model_version,
        "artifact_version": artifact_version,
        "horizon_hours": result.horizon_hours,
        "current_elevation_ft": _r(result.current_elevation),
        "stop_log_count": bundle.stop_log_count,
        "data_fresh": result.data_fresh,
        "rainfall_has_gaps": bundle.rainfall_has_gaps,
        # Where the watershed state came from (live: "archive_replay", or the fallback
        # "trailing_hindcast"); with rainfall_has_gaps it is floored at the seasonal seed (#4).
        "spinup": result.input_summary.get("spinup"),
        "spinup_history_days": result.input_summary.get("spinup_history_days"),
        "trailing_rain_hours": len(bundle.trailing_rainfall_in),
        "trailing_rain_total_in": _r(sum(bundle.trailing_rainfall_in)),
        "state_sm_in": _r(result.state_sm_in),
        "state_s_if_in": _r(result.state_s_if_in),
        "probabilities": {tp.label: tp.p_cross_within_horizon for tp in result.threshold_probabilities},
        "scenarios": scenarios,
        "qpf_outlook": qpf_outlook,
        "nws_outlook": nws_outlook,
    }


def append(record: dict[str, Any], directory: str | Path | None = None) -> Path:
    """Append ``record`` as one line to its UTC day's file; returns the file path."""
    t = datetime.fromisoformat(record["generated_at"])
    day = (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    path = _dir(directory) / f"{day:%Y-%m-%d}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    return path


def load_day(day: str, directory: str | Path | None = None) -> list[dict[str, Any]]:
    """All records for one UTC day (``YYYY-MM-DD``); empty if none were logged."""
    path = _dir(directory) / f"{day}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
