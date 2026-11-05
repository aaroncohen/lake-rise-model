"""NWS gridpoint QPF (sources/nws.py): parsing, the point->grid lookup, and storm totals."""

from datetime import datetime, timezone

import httpx
import pytest

from lake_rise.sources import nws

_REAL_QPF_OUTLOOK = nws.qpf_outlook     # conftest stubs the module attribute, not this binding

_GRID = {"properties": {
    "updateTime": "2026-11-05T18:26:35+00:00",
    "quantitativePrecipitation": {"uom": "wmoUnit:mm", "values": [
        {"validTime": "2026-11-06T12:00:00+00:00/PT6H", "value": 2.54},
        {"validTime": "2026-11-06T18:00:00+00:00/PT6H", "value": 25.4},
        {"validTime": "2026-11-07T00:00:00+00:00/P1DT6H", "value": None},
    ]},
    "probabilityOfPrecipitation": {"uom": "wmoUnit:percent", "values": [
        {"validTime": "2026-11-06T12:00:00+00:00/PT3H", "value": 60},
    ]},
}}


@pytest.mark.parametrize("iso, hours", [("PT6H", 6), ("PT1H", 1), ("P1D", 24), ("P1DT6H", 30),
                                        ("PT30M", 0.5)])
def test_duration_hours(iso, hours):
    assert nws.duration_hours(iso) == hours


def test_parse_keeps_native_blocks_in_inches():
    out = nws.parse_gridpoint(_GRID)
    assert out["issued"] == "2026-11-05T18:26:35+00:00"
    assert [(b["hours"], b["precipitation_in"]) for b in out["qpf"]] == [(6, 0.1), (6, 1.0),
                                                                        (30, None)]
    assert out["pop"] == [{"start": "2026-11-06T12:00:00+00:00", "hours": 3, "pop_pct": 60}]


def test_qpf_outlook_looks_up_the_grid_cell_once(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.startswith("/points/"):
            return httpx.Response(200, json={"properties": {"gridId": "SEW", "gridX": 133,
                                                            "gridY": 75}})
        assert request.url.path == "/gridpoints/SEW/133,75"
        return httpx.Response(200, json=_GRID)

    monkeypatch.setattr(nws, "_grid_cache", {})
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://nws")
    out = _REAL_QPF_OUTLOOK(47.7785, -122.1088, client=client)
    _REAL_QPF_OUTLOOK(47.7785, -122.1088, client=client)
    assert out["grid"] == "SEW/133,75" and len(out["qpf"]) == 3
    assert calls.count("/points/47.7785,-122.1088") == 1


def test_total_prorates_blocks_at_the_window_edges():
    out = nws.parse_gridpoint(_GRID)
    t = datetime(2026, 11, 6, 15, tzinfo=timezone.utc)      # halfway through the first block
    assert nws.total_in(out, t, 6) == pytest.approx(0.05 + 0.5)
