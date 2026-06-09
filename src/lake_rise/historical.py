"""Catalog of real Western Washington storms (DSO regional storm dataset, parsed
from the operator's spreadsheet). Each storm gives rainfall depths at fixed
durations (2/6/18/24/72-hr) by storm type (Short/Intermediate/Long); we synthesize
an hourly hyetograph that reproduces those nested depths so the storms can drive
the model and be ranked by severity.
"""

from __future__ import annotations

import json
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data" / "historical_storms.json"
_DURATION_COLS = [("2hr", 2), ("6hr", 6), ("18hr", 18), ("24hr", 24), ("72hr", 72)]


def _depths(row: dict) -> dict[int, float]:
    """Populated (duration_hours -> depth_inches) checkpoints for a storm."""
    return {hours: row[col] for col, hours in _DURATION_COLS if row.get(col) is not None}


def _load() -> tuple[list[dict], dict[str, dict]]:
    rows = json.loads(_DATA.read_text())
    catalog = []
    for i, r in enumerate(rows):
        catalog.append({**r, "id": f"h{i}", "depths": _depths(r)})
    return catalog, {e["id"]: e for e in catalog}


_CATALOG, _BY_ID = _load()


def build_hyetograph(depths: dict[int, float]) -> list[float]:
    """Hourly rainfall (inches) reproducing the nested duration-depths via the
    alternating-block method: each ring (d_{i-1}, d_i] contributes its incremental
    depth at its average intensity, and the most intense hours are placed centrally
    so the central d_i hours sum to depth d_i (a single-peaked, realistic shape)."""
    if not depths:
        return []
    pts = sorted(depths.items())                       # [(dur, depth), ...] ascending
    total = pts[-1][0]

    # Per-hour intensities, ring by ring (inner rings are more intense).
    intensities: list[float] = []
    prev_dur, prev_depth = 0, 0.0
    for dur, depth in pts:
        ring_hours = dur - prev_dur
        if ring_hours > 0:
            intensities += [(depth - prev_depth) / ring_hours] * ring_hours
        prev_dur, prev_depth = dur, depth

    # Alternating-block placement: highest intensity at the centre, decreasing outward.
    intensities.sort(reverse=True)
    mid = total // 2
    order = [mid]
    o = 1
    while len(order) < total:
        if mid + o < total:
            order.append(mid + o)
        if mid - o >= 0:
            order.append(mid - o)
        o += 1

    series = [0.0] * total
    for idx, inten in zip(order, intensities):
        series[idx] = inten
    return series


def hyetograph_for(storm_id: str) -> list[float]:
    return build_hyetograph(_BY_ID[storm_id]["depths"])


def catalog() -> list[dict]:
    """The storm catalog, each with severity metrics, sorted most-severe first.
    Severity = total depth (the longest available duration), the amount that fills
    the lake; ties broken by longer duration then peak intensity."""
    items = []
    for e in _CATALOG:
        depths = e["depths"]
        dur = max(depths)
        series = build_hyetograph(depths)
        items.append({
            "id": e["id"],
            "station": e["station"],
            "date": e["date"],
            "region": e["region"],
            "storm_type": e["storm_type"],
            "total_in": round(depths[dur], 2),
            "duration_h": dur,
            "peak_in_per_hr": round(max(series), 3) if series else 0.0,
        })
    items.sort(key=lambda x: (-x["total_in"], -x["duration_h"], -x["peak_in_per_hr"]))
    return items
