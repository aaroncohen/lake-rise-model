"""Live HA REST source, with all network calls mocked via httpx.MockTransport."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from lake_rise.sources.live_ha import HAConfig, LiveConditions, LiveHASource, hourly_from_accumulator


_BUCKET_STATES = {
    "sensor.crystal_lake_depth_smoothed": "1.36",
    "sensor.gw3000b_rain_rate_piezo": "0.04",
    "sensor.gw3000b_daily_rain_piezo": "0.22",
    "sensor.gw3000b_weekly_rain_piezo": "0.65",
    "sensor.gw3000b_monthly_rain_piezo": "2.80",
    "sensor.gw3000b_event_rain_piezo": "0.22",
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/api/states/"):
        entity_id = path.split("/api/states/", 1)[1]
        state_val = _BUCKET_STATES.get(entity_id, "1.36")
        return httpx.Response(200, json={"state": state_val, "attributes": {},
                                         "last_reported": datetime.now(timezone.utc).isoformat()})
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


def test_fetch_conditions(live_source):
    cond = live_source.fetch_conditions()
    assert isinstance(cond, LiveConditions)
    # Lake sensor
    assert cond.reading_ft == pytest.approx(1.36)
    # Bucket sensors parsed correctly
    assert cond.rate_in_per_hr == pytest.approx(0.04)
    assert cond.today_in == pytest.approx(0.22)
    assert cond.week_in == pytest.approx(0.65)
    assert cond.month_in == pytest.approx(2.80)
    assert cond.event_in == pytest.approx(0.22)
    # Trailing series: older block prepended -> length > 10*24
    assert len(cond.trailing_rainfall_in) > 10 * 24
    # older_block_in >= 0 and consistent with month total
    assert cond.older_block_in >= 0.0
    assert cond.older_block_in <= cond.month_in + 1e-6
    # Forecast parsed
    assert cond.forecast_point_in == [0.05, 0.10, 0.0]
    assert cond.forecast_pop_frac == [0.8, 0.7, 0.2]
    # Gauge reported just now -> fresh, even though most hours are dry
    assert cond.has_gaps is False


def test_state_age_and_staleness(art):
    from lake_rise.sources.live_ha import _state_age_hours
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert _state_age_hours({"last_reported": (now - timedelta(minutes=10)).isoformat()}, now) < 1
    assert _state_age_hours({"last_changed": (now - timedelta(hours=8)).isoformat()}, now) > 7
    assert _state_age_hours({}, now) > 1000  # unknown -> very stale


def test_stale_gauge_flags_not_fresh(art):
    """A dry gauge is fresh; a gauge that hasn't reported in hours is not."""
    old = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/api/states/"):
            eid = path.split("/api/states/", 1)[1]
            return httpx.Response(200, json={"state": _BUCKET_STATES.get(eid, "1.36"),
                                             "last_reported": old, "attributes": {}})
        if path.startswith("/api/history/period/"):
            return httpx.Response(200, json=[[]])
        return httpx.Response(200, json={"service_response": {
            "weather.47_77849_122_10882": {"forecast": []}}})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    assert src.fetch_conditions().has_gaps is True


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
