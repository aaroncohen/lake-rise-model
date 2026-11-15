"""ECMWF IFS ensemble (50 perturbed members) rain for the site, from ECMWF's open data
(0.25 deg since 2024; CC-BY-4.0) on its Google Cloud mirror, falling back to AWS
(``ecmwf-forecasts``), whose bucket throttles a single client hard. Total precipitation is
accumulated from the cycle start, so the members' 24/48/72 h values give each day's and the
3-day total.

Per cycle that is 150 messages (~60 MB downloaded); ``grib_point.get`` paces and backs off.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx

from . import grib_point

MIRRORS = ("https://storage.googleapis.com/ecmwf-open-data",
           "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com")
STEPS = (24, 48, 72)
_IN_PER_M = 39.3701


def file_url(cycle: datetime, step: int, base: str = MIRRORS[0]) -> str:
    d, h = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    return f"{base}/{d}/{h}z/ifs/0p25/enfo/{d}{h}0000-{step}h-enfo-ef.grib2"


def parse_index(text: str) -> list[grib_point.Message]:
    """ECMWF's JSON-lines index: one object per message with ``_offset``/``_length``."""
    out = []
    for line in text.splitlines():
        if line.strip():
            d = json.loads(line)
            out.append(grib_point.Message(offset=d["_offset"], length=d["_length"], meta=d))
    return out


def select(messages: list[grib_point.Message]) -> dict[int, grib_point.Message]:
    """The perturbed members' total-precipitation messages, by member number."""
    return {int(m.meta["number"]): m for m in messages
            if m.meta.get("param") == "tp" and m.meta.get("type") == "pf"}


def fetch_cycle(cycle: datetime, latitude: float, longitude: float,
                c: httpx.Client | None = None) -> dict | None:
    """Each member's rain at the site (inches) for days 1-3 and the 3-day total, or ``None`` if
    the cycle isn't (fully) published."""
    own = c is None
    c = c or grib_point.client()
    try:
        base = _first_mirror_with(c, cycle)
        if base is None:
            return None
        cum: dict[int, dict[int, float]] = {}            # step -> member -> metres since start
        for step in STEPS:
            url = file_url(cycle, step, base)
            r = grib_point.get(c, url.replace(".grib2", ".index"))
            if r is None:
                return None
            msgs = select(parse_index(r.text))
            if not msgs:
                return None
            cum[step] = {n: grib_point.decode_point(grib_point.fetch_message(c, url, m),
                                                    latitude, longitude)
                         for n, m in sorted(msgs.items())}
        members = sorted(set.intersection(*(set(v) for v in cum.values())))
        prev = {n: 0.0 for n in members}
        periods = []
        for lead0, step in zip((0, 24, 48), STEPS):
            periods.append(_period(cycle, lead0, 24, [cum[step][n] - prev[n] for n in members]))
            prev = cum[step]
        periods.append(_period(cycle, 0, 72, [cum[72][n] for n in members]))
        return {"source": "ecmwf_ens", "cycle": cycle.isoformat(), "latitude": latitude,
                "longitude": longitude, "members": members, "periods": periods}
    finally:
        if own:
            c.close()


def _first_mirror_with(c: httpx.Client, cycle: datetime) -> str | None:
    """The first mirror that has the cycle's last step published (or that answers at all)."""
    for base in MIRRORS:
        try:
            if grib_point.get(c, file_url(cycle, STEPS[-1], base).replace(".grib2", ".index"),
                              (0, 0)) is not None:
                return base
        except httpx.HTTPError as exc:
            grib_point.log.warning("ecmwf mirror %s unavailable: %s", base, exc)
    return None


def _period(cycle: datetime, lead0: int, hours: int, metres: list[float]) -> dict:
    return {"start": (cycle + timedelta(hours=lead0)).isoformat(), "hours": hours,
            "lead_h": lead0, "members_in": [round(max(0.0, v) * _IN_PER_M, 3) for v in metres]}
