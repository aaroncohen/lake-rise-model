"""NWS National Blend of Models (NBM) rain percentiles for the site, from the AWS archive
(``noaa-nbm-grib2-pds``, back to 2020-05). The QMD files carry calibrated QPF percentiles
(0-100 % in 5 % steps); we keep a few for each day of the first three and the 3-day total --
the storm-total scale that drives the lake.

Per cycle that is 28 messages (~40 MB downloaded) for a few numbers kept.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from . import grib_point

BASE = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
PERCENTILES = (5, 10, 25, 50, 75, 90, 95)
_MM_PER_IN = 25.4
# (lead start h, lead end h, forecast-hour file, the accumulation label NBM writes)
PERIODS = ((0, 24, 24, "0-1 day acc fcst"), (24, 48, 48, "1-2 day acc fcst"),
           (48, 72, 72, "2-3 day acc fcst"), (0, 72, 72, "0-3 day acc fcst"))


def file_url(cycle: datetime, fhour: int) -> str:
    d, h = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    return f"{BASE}/blend.{d}/{h}/qmd/blend.t{h}z.qmd.f{fhour:03d}.co.grib2"


def parse_idx(text: str) -> list[grib_point.Message]:
    """The wgrib2-style index: ``n:offset:d=YYYYMMDDHH:VAR:level:accumulation:kind:...``."""
    rows = [line.split(":") for line in text.splitlines() if line.strip()]
    out = []
    for k, f in enumerate(rows):
        nxt = int(rows[k + 1][1]) if k + 1 < len(rows) else None
        out.append(grib_point.Message(
            offset=int(f[1]), length=None if nxt is None else nxt - int(f[1]),
            meta={"var": f[3], "level": f[4], "acc": f[5], "kind": f[6]}))
    return out


def select(messages: list[grib_point.Message], acc: str) -> dict[int, grib_point.Message]:
    """The APCP percentile messages for one accumulation, by percentile."""
    want = {f"{p}% level": p for p in PERCENTILES}
    return {want[m.meta["kind"]]: m for m in messages
            if m.meta["var"] == "APCP" and m.meta["acc"] == acc and m.meta["kind"] in want}


def fetch_cycle(cycle: datetime, latitude: float, longitude: float,
                c: httpx.Client | None = None) -> dict | None:
    """The cycle's rain percentiles at the site, inches, or ``None`` if NBM hasn't published it
    (yet). Periods are leads from the cycle time."""
    own = c is None
    c = c or grib_point.client()
    try:
        periods = []
        idx_cache: dict[int, list[grib_point.Message]] = {}
        for lead0, lead1, fhour, acc in PERIODS:
            url = file_url(cycle, fhour)
            if fhour not in idx_cache:
                r = grib_point.get(c, url + ".idx")
                if r is None:
                    return None
                idx_cache[fhour] = parse_idx(r.text)
            msgs = select(idx_cache[fhour], acc)
            if len(msgs) != len(PERCENTILES):
                return None
            pct = {str(p): round(grib_point.decode_point(grib_point.fetch_message(c, url, m),
                                                         latitude, longitude) / _MM_PER_IN, 3)
                   for p, m in sorted(msgs.items())}
            periods.append({"start": (cycle + timedelta(hours=lead0)).isoformat(),
                            "hours": lead1 - lead0, "lead_h": lead0, "percentiles_in": pct})
        return {"source": "nbm", "cycle": cycle.isoformat(), "latitude": latitude,
                "longitude": longitude, "periods": periods}
    finally:
        if own:
            c.close()
