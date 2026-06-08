"""Live HA REST source, with all network calls mocked via httpx.MockTransport."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from lake_rise.sources.live_ha import HAConfig, LiveHASource, hourly_from_accumulator


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/api/states/"):
        return httpx.Response(200, json={"state": "1.36", "attributes": {}})
    if path.startswith("/api/history/period/"):
        # one small rain event a few hours ago
        now = datetime.now(timezone.utc)
        t = (now - timedelta(hours=3)).isoformat()
        return httpx.Response(200, json=[[
            {"state": "0.0", "last_changed": (now - timedelta(hours=5)).isoformat()},
            {"state": "0.12", "last_changed": t},
            {"state": "unknown", "last_changed": (now - timedelta(hours=2)).isoformat()},
            {"state": "0.0", "last_changed": (now - timedelta(hours=1)).isoformat()},
        ]])
    if path == "/api/services/weather/get_forecasts":
        return httpx.Response(200, json={"service_response": {
            "weather.47_77849_122_10882": {"forecast": [
                {"precipitation": 0.05, "precipitation_probability": 80},
                {"precipitation": 0.10, "precipitation_probability": 70},
                {"precipitation": 0.0, "precipitation_probability": 20},
            ]}
        }})
    return httpx.Response(404)


@pytest.fixture
def live_source(art):
    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test")
    cfg = HAConfig(base_url="http://test", token="x")
    return LiveHASource(art, cfg, client=client)


def test_fetch_snapshot(live_source):
    snap = live_source.fetch_snapshot()
    assert snap.lake_depth_reading_ft == 1.36
    assert snap.forecast_point_in == [0.05, 0.10, 0.0]
    assert snap.forecast_pop_frac == [0.8, 0.7, 0.2]
    assert snap.stop_log_count in (0, 3)  # date-based default
    assert any(v > 0 for v in snap.trailing_rainfall_in)  # the event made it in


def test_build_bundle_applies_datum(live_source, art):
    bundle = live_source.build_bundle()
    assert bundle.current_elevation_abs_ft == 1.36 + art.datum.sensor_to_absolute_offset_ft
    assert len(bundle.forecast_scenarios) == 3


def test_hourly_from_accumulator_buckets_by_hour():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    states = [
        (base + timedelta(minutes=10), 0.05),
        (base + timedelta(minutes=50), 0.12),   # hour 0 peak -> 0.12
        (base + timedelta(hours=1, minutes=5), 0.03),  # hour 1 -> 0.03
    ]
    series, has_gaps = hourly_from_accumulator(states, base, base + timedelta(hours=3))
    assert series == [0.12, 0.03, 0.0]
    assert has_gaps is False  # 2 of 3 hours covered (0.67) is above the 0.25 gap threshold

    # a long window with only one covered hour -> flagged as gappy
    sparse, gap2 = hourly_from_accumulator(states[:1], base, base + timedelta(hours=20))
    assert gap2 is True
