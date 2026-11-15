"""Point values from public GRIB2 forecast archives (AWS open data), fetched one message at a time.

The NBM and ECMWF ensemble files are hundreds of MB to GB each, but both publish an index of
every message's byte offset, so only the rain fields are downloaded (~0.4-1.7 MB each) with
HTTP range requests. Each message is decoded with ecCodes (bundled in the ``pygrib`` wheel) and
reduced to the site's value. Network and decoding stay behind small functions the tests replace.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger("lake_rise.qpf")

TIMEOUT_S = 60.0
# The ECMWF bucket answers bursts with 503 SlowDown: space requests out, and back off and retry
# when it still pushes back (5, 10, 20, 40, 80 s).
MIN_INTERVAL_S = 0.25
RETRIES = 5
BACKOFF_S = 5.0
USER_AGENT = "lake-rise-model (Crystal Lake dam early-warning)"
_last_request = [0.0]


@dataclass(frozen=True)
class Message:
    """One GRIB message in a remote file: where it is and what it is."""
    offset: int
    length: int | None          # None = to the end of the file
    meta: dict


def client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT},
                        follow_redirects=True)


def get(c: httpx.Client, url: str, byte_range: tuple[int, int | None] | None = None
        ) -> httpx.Response | None:
    """GET with retries on throttling; ``None`` for a 404 (that cycle or step isn't published)."""
    headers = {}
    if byte_range is not None:
        a, b = byte_range
        headers["Range"] = f"bytes={a}-{'' if b is None else b}"
    for attempt in range(RETRIES + 1):
        wait = _last_request[0] + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()
        r = c.get(url, headers=headers)
        if r.status_code == 404:
            return None
        if r.status_code in (429, 500, 502, 503) and attempt < RETRIES:
            time.sleep(BACKOFF_S * 2 ** attempt)
            continue
        r.raise_for_status()
        return r
    return None


def fetch_message(c: httpx.Client, url: str, m: Message) -> bytes:
    end = None if m.length is None else m.offset + m.length - 1
    r = get(c, url, (m.offset, end))
    if r is None:
        raise FileNotFoundError(url)
    return r.content


def decode_point(grib: bytes, latitude: float, longitude: float) -> float:
    """The message's value at the site: bilinear on a regular lat/lon grid (ECMWF 0.25 deg),
    the nearest grid point otherwise (NBM's 2.5 km Lambert grid). In the message's units."""
    import numpy as np
    import pygrib                                   # heavy (bundles ecCodes); only when used

    g = pygrib.fromstring(grib)
    values = np.asarray(g.values, dtype=float)
    if g.gridType == "regular_ll":
        lats = np.asarray(g.distinctLatitudes, dtype=float)
        lons = np.asarray(g.distinctLongitudes, dtype=float)
        lon = longitude % 360 if lons.max() > 180 else longitude
        return _bilinear(values, lats, lons, latitude, lon)
    lats, lons = g.latlons()
    lons = np.where(lons > 180, lons - 360, lons)
    d = (lats - latitude) ** 2 + ((lons - longitude) * np.cos(np.radians(latitude))) ** 2
    return float(values.flat[int(np.argmin(d))])


def _bilinear(values, lats, lons, lat: float, lon: float) -> float:
    import numpy as np

    def bracket(axis, x):
        order = np.argsort(axis)
        a = axis[order]
        k = int(np.clip(np.searchsorted(a, x) - 1, 0, len(a) - 2))
        w = (x - a[k]) / (a[k + 1] - a[k])
        return order[k], order[k + 1], float(np.clip(w, 0.0, 1.0))

    i0, i1, wy = bracket(lats, lat)
    j0, j1, wx = bracket(lons, lon)
    v = values
    return float((1 - wy) * ((1 - wx) * v[i0, j0] + wx * v[i0, j1])
                 + wy * ((1 - wx) * v[i1, j0] + wx * v[i1, j1]))
