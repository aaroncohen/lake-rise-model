"""Catalog of real Western Washington storms (DSO regional storm dataset, parsed
from the operator's spreadsheet). Each storm gives rainfall depths at fixed
durations (2/6/18/24/72-hr) by storm type (Short/Intermediate/Long); we synthesize
an hourly hyetograph that reproduces those nested depths so the storms can drive
the model and be ranked by severity.
"""

from __future__ import annotations

import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data" / "historical_storms.json"
_DURATION_COLS = [("2hr", 2), ("6hr", 6), ("18hr", 18), ("24hr", 24), ("72hr", 72)]

# Crystal Lake dam outlet (Woodinville/Duvall). Storms are preferred by proximity.
_DAM = (47.776, -122.107)
_MAX_DISTANCE_MI = 40.0     # drop stations farther than this from the dam
# Approximate station coordinates (region 31, Puget lowland). Region-32 stations
# (Olympic peninsula / SW Washington / Oregon) are excluded outright as
# climatologically unlike Woodinville, so they are not listed here.
_STATION_COORDS = {
    "Carnation 4 NW": (47.69, -121.96),
    "Snoqualmie Falls": (47.54, -121.84),
    "Seattle EMSU": (47.61, -122.33),
    "Seattle WB City": (47.61, -122.33),
    "Seattle SPU RG02": (47.61, -122.32),
    "Seattle SPU RG08": (47.61, -122.32),
    "Seattle SPU RG10": (47.61, -122.32),
    "Seattle SPU RG16": (47.61, -122.32),
    "Seattle SPU RG20": (47.61, -122.32),
    "Seattle RG03": (47.61, -122.32),
    "Seattle RG12": (47.61, -122.32),
    "Seattle RG18": (47.61, -122.32),
    "King County East Pine PS": (47.62, -122.33),
    "Sea-Tac Airport": (47.45, -122.31),
    "Sea-Tac (WSO)": (47.45, -122.31),
    "Everett": (47.98, -122.20),
    "Landsburg": (47.38, -121.96),
    "Auburn": (47.31, -122.23),
    "Mc Millin Reservoir": (47.14, -122.25),
    "Burlington": (48.47, -122.33),
    "Centralia 1 W": (46.72, -122.97),
    "Longview": (46.14, -122.94),
    "Castle Rock 2 NW": (46.30, -122.91),
    "Blaine": (48.99, -122.75),
    "Blaine 1 ENE": (48.99, -122.73),
    "Yelm": (46.94, -122.61),
}


def _distance_mi(station: str) -> float:
    coord = _STATION_COORDS.get(station)
    if coord is None:
        return 9999.0
    lat1, lon1 = radians(_DAM[0]), radians(_DAM[1])
    lat2, lon2 = radians(coord[0]), radians(coord[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 3958.8 * 2 * asin(sqrt(a))


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


def _metrics(e: dict) -> dict:
    depths = e["depths"]
    dur = max(depths)
    series = build_hyetograph(depths)
    return {
        "id": e["id"],
        "station": e["station"],
        "date": e["date"],
        "region": e["region"],
        "storm_type": e["storm_type"],
        "total_in": round(depths[dur], 2),
        "duration_h": dur,
        "peak_in_per_hr": round(max(series), 3) if series else 0.0,
        "distance_mi": round(_distance_mi(e["station"]), 1),
    }


def all_storms() -> list[dict]:
    """Every storm in the source dataset (provenance), severity-sorted."""
    items = [_metrics(e) for e in _CATALOG]
    items.sort(key=lambda x: (-x["total_in"], -x["duration_h"], -x["peak_in_per_hr"]))
    return items


# Storms to keep per type, chosen to span the severity range (most-relevant Long
# storms get the most slots). Nearer stations win when totals are similar.
_SLOTS_PER_TYPE = {"Long": 5, "Intermediate": 4, "Short": 4}


def _pick_spread(items: list[dict], n: int) -> list[dict]:
    """Pick up to n storms whose totals are spread evenly across the range; for each
    target total, take the nearest station among the storms closest to it."""
    if len(items) <= n:
        return items
    totals = [it["total_in"] for it in items]
    lo, hi = min(totals), max(totals)
    chosen: dict[str, dict] = {}
    for i in range(n):
        target = lo + (hi - lo) * i / (n - 1)
        best = min(abs(it["total_in"] - target) for it in items)
        near_target = [it for it in items if abs(it["total_in"] - target) <= best + 0.1]
        pick = min(near_target, key=lambda it: it["distance_mi"])
        chosen[pick["id"]] = pick
    return list(chosen.values())


def catalog() -> list[dict]:
    """Curated catalog: region-31 storms within range of the dam, thinned to a
    severity-spread set per type, preferring stations near Woodinville. Sorted
    most-severe first."""
    near = [m for m in (_metrics(e) for e in _CATALOG)
            if m["region"] == 31 and m["distance_mi"] <= _MAX_DISTANCE_MI]
    out: list[dict] = []
    for storm_type, n in _SLOTS_PER_TYPE.items():
        out += _pick_spread([m for m in near if m["storm_type"] == storm_type], n)
    out.sort(key=lambda x: (-x["total_in"], -x["duration_h"], -x["peak_in_per_hr"]))
    return out
