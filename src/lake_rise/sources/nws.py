"""NWS gridpoint precipitation forecast (api.weather.gov), kept beside Apple WeatherKit's in the
forecast-of-record log so the two can be scored against the gauge's rain and the better one --
or a blend -- chosen later. HA's NWS weather entity carries PoP only, so the QPF comes from the
gridpoint API directly.

NWS issues QPF in blocks (6 h) and PoP in variable intervals; both are kept in their native
blocks -- spreading a 6 h total over hourly values would invent a timing NWS never forecast.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import httpx

API_BASE = "https://api.weather.gov"
# api.weather.gov rejects requests without an identifying User-Agent.
USER_AGENT = "lake-rise-model (Crystal Lake dam early-warning)"
TIMEOUT_S = 20.0
_MM_PER_IN = 25.4

_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")
_grid_cache: dict[tuple[float, float], str] = {}


def duration_hours(iso: str) -> float:
    """Hours in an ISO-8601 duration as NWS writes them (``PT6H``, ``P1D``, ``P1DT6H``)."""
    m = _DURATION.match(iso)
    if not m or not any(m.groups()):
        raise ValueError(f"unsupported ISO-8601 duration {iso!r}")
    d, h, mins = (int(x) if x else 0 for x in m.groups())
    return d * 24 + h + mins / 60


def _blocks(layer: dict, key: str, scale: float = 1.0) -> list[dict]:
    out = []
    for v in layer.get("values", []):
        start, _, dur = v["validTime"].partition("/")
        value = v.get("value")
        out.append({"start": datetime.fromisoformat(start).isoformat(),
                    "hours": duration_hours(dur),
                    key: None if value is None else round(value * scale, 3)})
    return out


def parse_gridpoint(payload: dict) -> dict:
    """The forecast-log form of a gridpoint response: issue time, QPF blocks (inches) and PoP
    blocks (%)."""
    p = payload["properties"]
    qpf = p.get("quantitativePrecipitation", {})
    if qpf.get("uom", "wmoUnit:mm") != "wmoUnit:mm":
        raise ValueError(f"unexpected QPF unit {qpf.get('uom')!r}")
    return {
        "issued": p.get("updateTime"),
        "qpf": _blocks(qpf, "precipitation_in", 1 / _MM_PER_IN),
        "pop": _blocks(p.get("probabilityOfPrecipitation", {}), "pop_pct"),
    }


def qpf_outlook(latitude: float, longitude: float, client: httpx.Client | None = None) -> dict:
    """The current NWS precipitation forecast for the grid cell containing the point."""
    own = client is None
    client = client or httpx.Client(base_url=API_BASE, timeout=TIMEOUT_S,
                                    headers={"User-Agent": USER_AGENT,
                                             "Accept": "application/geo+json"})
    try:
        key = (round(latitude, 4), round(longitude, 4))
        if key not in _grid_cache:
            r = client.get(f"/points/{key[0]},{key[1]}")
            r.raise_for_status()
            props = r.json()["properties"]
            _grid_cache[key] = f"{props['gridId']}/{props['gridX']},{props['gridY']}"
        grid = _grid_cache[key]
        r = client.get(f"/gridpoints/{grid}")
        r.raise_for_status()
        return {"grid": grid, **parse_gridpoint(r.json())}
    finally:
        if own:
            client.close()


def total_in(outlook: dict, start: datetime, hours: float) -> float:
    """QPF over ``[start, start + hours)``, prorating blocks that straddle the window edges --
    for comparing a storm total against the gauge or Apple's forecast."""
    end = start + timedelta(hours=hours)
    total = 0.0
    for b in outlook.get("qpf", []):
        if b["precipitation_in"] is None:
            continue
        b0 = datetime.fromisoformat(b["start"])
        b1 = b0 + timedelta(hours=b["hours"])
        overlap = (min(b1, end) - max(b0, start)).total_seconds() / 3600
        if overlap > 0:
            total += b["precipitation_in"] * overlap / b["hours"]
    return round(total, 3)
