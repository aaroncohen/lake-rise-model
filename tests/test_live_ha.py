"""Live HA REST source, with all network calls mocked via httpx.MockTransport."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from lake_rise.sources.live_ha import HAConfig, LiveConditions, LiveHASource, hourly_from_accumulator


# ---------------------------------------------------------------------------
# Backtest-specific mock handler (lake history + rain history)
# ---------------------------------------------------------------------------

def _backtest_handler(request: httpx.Request) -> httpx.Response:
    """Handler that returns lake-depth history for the lake sensor and rain
    accumulator history for the rain sensor."""
    path = request.url.path
    params = dict(request.url.params)
    if path.startswith("/api/states/"):
        entity_id = path.split("/api/states/", 1)[1]
        state_val = _BUCKET_STATES.get(entity_id, "1.36")
        return httpx.Response(200, json={
            "state": state_val,
            "attributes": {},
            "last_reported": datetime.now(timezone.utc).isoformat(),
        })
    if path.startswith("/api/history/period/"):
        entity_id = params.get("filter_entity_id", "")
        now = datetime.now(timezone.utc)
        if entity_id == "sensor.crystal_lake_depth_smoothed":
            # Return lake depth readings: a series over the past 2+ hours.
            rows = []
            for i in range(5):
                ts = (now - timedelta(hours=4 - i)).replace(minute=0, second=0, microsecond=0)
                rows.append({"state": "1.40", "last_changed": ts.isoformat()})
            return httpx.Response(200, json=[rows])
        else:
            # Return rain accumulator history.
            t = (now - timedelta(hours=3)).isoformat()
            return httpx.Response(200, json=[[
                {"state": "0.0", "last_changed": (now - timedelta(hours=5)).isoformat()},
                {"state": "0.12", "last_changed": t},
                {"state": "unknown", "last_changed": (now - timedelta(hours=2)).isoformat()},
                {"state": "0.0", "last_changed": (now - timedelta(hours=1)).isoformat()},
            ]])
    return httpx.Response(404)


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
    params = dict(request.url.params)
    if path.startswith("/api/states/"):
        entity_id = path.split("/api/states/", 1)[1]
        state_val = _BUCKET_STATES.get(entity_id, "1.36")
        return httpx.Response(200, json={"state": state_val, "attributes": {},
                                         "last_reported": datetime.now(timezone.utc).isoformat()})
    if path.startswith("/api/history/period/"):
        now = datetime.now(timezone.utc)
        if params.get("filter_entity_id") == "sensor.crystal_lake_depth_smoothed":
            # Recent lake-depth samples within the ~1 h anchor window (median -> 1.36).
            return httpx.Response(200, json=[[
                {"state": "1.36", "last_changed": (now - timedelta(minutes=m)).isoformat()}
                for m in (10, 25, 45)
            ]])
        # one small rain event a few hours ago
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


def test_as_of_is_floored_to_hour_and_aligns_trailing(live_source):
    """Regression: `as_of` must sit on an hour boundary so it aligns with the clock-hour
    trailing series. `predict` derives `hind_start = as_of - len(trailing)h` and runs the
    forecast from `as_of`; the model applies rain hour i over [start+i, start+i+1]. If `as_of`
    carried real minutes, every hindcast hour would land at the wrong sub-hour offset and
    forecast points would be stamped :mm past the hour (skew-critical). The trailing length is
    exactly trailing_days*24 so `hind_start` lands precisely on the floored rain-window start."""
    snap = live_source.fetch_snapshot()
    as_of = datetime.fromisoformat(snap.as_of)
    assert (as_of.minute, as_of.second, as_of.microsecond) == (0, 0, 0)
    assert len(snap.trailing_rainfall_in) == live_source.cfg.trailing_days * 24

    cond = live_source.fetch_conditions()
    cond_as_of = datetime.fromisoformat(cond.as_of)
    assert (cond_as_of.minute, cond_as_of.second, cond_as_of.microsecond) == (0, 0, 0)


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


def _fresh_gauge_handler(rain_response):
    """Handler with a FRESH lake gauge and lake history, but a caller-chosen rain-history
    response -- so a gap flag can only come from the rain retrieval, not staleness."""
    fresh = datetime.now(timezone.utc).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        if path.startswith("/api/states/"):
            eid = path.split("/api/states/", 1)[1]
            return httpx.Response(200, json={"state": _BUCKET_STATES.get(eid, "1.36"),
                                             "last_reported": fresh, "attributes": {}})
        if path.startswith("/api/history/period/"):
            if params.get("filter_entity_id") == "sensor.crystal_lake_depth_smoothed":
                now = datetime.now(timezone.utc)
                return httpx.Response(200, json=[[
                    {"state": "1.36", "last_changed": (now - timedelta(minutes=m)).isoformat()}
                    for m in (10, 25, 45)]])
            return rain_response()
        return httpx.Response(200, json={"service_response": {
            "weather.47_77849_122_10882": {"forecast": []}}})
    return handler


def test_rain_fetch_http_error_flags_gaps_and_does_not_crash(art):
    """#4: a real failure to RETRIEVE the rain history (HTTP error) degrades gracefully --
    it flags rainfall_has_gaps rather than crashing the prediction -- even though the lake
    gauge is reporting fine (so the trigger is the retrieval failure, not staleness)."""
    handler = _fresh_gauge_handler(lambda: httpx.Response(500))
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    snap = src.fetch_snapshot()                 # must not raise
    assert snap.rainfall_has_gaps is True
    assert src.fetch_conditions().has_gaps is True


def test_forecast_http_error_degrades_without_crashing(art):
    """The never-crash guardrail applies to the forecast fetch too: a WeatherKit/network
    failure must not kill the live prediction. It degrades to an empty forecast and flags
    degraded input (has_gaps) so the horizon isn't projected spuriously dry."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        fresh = datetime.now(timezone.utc).isoformat()
        if path.startswith("/api/states/"):
            eid = path.split("/api/states/", 1)[1]
            return httpx.Response(200, json={"state": _BUCKET_STATES.get(eid, "1.36"),
                                             "last_reported": fresh, "attributes": {}})
        if path.startswith("/api/history/period/"):
            now = datetime.now(timezone.utc)
            if params.get("filter_entity_id") == "sensor.crystal_lake_depth_smoothed":
                return httpx.Response(200, json=[[
                    {"state": "1.36", "last_changed": (now - timedelta(minutes=m)).isoformat()}
                    for m in (10, 25, 45)]])
            return httpx.Response(200, json=[[
                {"state": "0.0", "last_changed": (now - timedelta(hours=2)).isoformat()}]])
        return httpx.Response(500)                       # the forecast POST fails

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    snap = src.fetch_snapshot()                          # must not raise
    assert snap.forecast_point_in == [] and snap.forecast_pop_frac == []
    assert snap.rainfall_has_gaps is True
    assert src.fetch_conditions().has_gaps is True       # must not raise either


def test_lake_history_http_error_falls_back_to_instantaneous_reading(art):
    """A transient lake-history failure must not abort the prediction: `_smoothed_reading`
    degrades to the instantaneous lake state instead of propagating the error (the same
    degrade-don't-crash rule rain history follows)."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        fresh = datetime.now(timezone.utc).isoformat()
        if path.startswith("/api/states/"):
            eid = path.split("/api/states/", 1)[1]
            return httpx.Response(200, json={"state": _BUCKET_STATES.get(eid, "1.36"),
                                             "last_reported": fresh, "attributes": {}})
        if path.startswith("/api/history/period/"):
            if params.get("filter_entity_id") == "sensor.crystal_lake_depth_smoothed":
                return httpx.Response(500)               # lake history fails
            return httpx.Response(200, json=[[]])
        return httpx.Response(200, json={"service_response": {
            "weather.47_77849_122_10882": {"forecast": []}}})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    snap = src.fetch_snapshot()                          # must not raise
    assert snap.lake_depth_reading_ft == 1.36            # fell back to the instantaneous state


def test_empty_rain_history_flags_gaps_even_when_gauge_fresh(art):
    """#4: zero usable rain records over the whole window is a retrieval failure -- a
    dry-but-healthy sensor still returns >=1 record, so this is NOT confounded with dry
    weather. The gauge is fresh, so the old staleness-only trigger would have missed it."""
    handler = _fresh_gauge_handler(lambda: httpx.Response(200, json=[[]]))
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    assert src.fetch_snapshot().rainfall_has_gaps is True


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


# ---------------------------------------------------------------------------
# fetch_backtest integration test (mocked HA)
# ---------------------------------------------------------------------------

def test_fetch_backtest_stop_log_override(art):
    """An explicit stop_log_count overrides the seasonal default."""
    client = httpx.Client(transport=httpx.MockTransport(_backtest_handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    assert src.fetch_backtest(hours_back=3, stop_log_count=0)["stop_log_count"] == 0
    assert src.fetch_backtest(hours_back=3, stop_log_count=2)["stop_log_count"] == 2


def test_fetch_backtest(art):
    """fetch_backtest returns a valid backtest result dict."""
    client = httpx.Client(
        transport=httpx.MockTransport(_backtest_handler), base_url="http://test"
    )
    cfg = HAConfig(base_url="http://test", token="x")
    src = LiveHASource(art, cfg, client=client)

    result = src.fetch_backtest(hours_back=3)

    # Top-level structure
    assert "t0" in result
    assert "now" in result
    assert "hours" in result
    assert "predicted" in result
    assert "actual" in result
    assert "rainfall_in" in result
    assert "rain_total_in" in result
    assert "metrics" in result
    assert "stop_log_count" in result
    assert "data_fresh" in result

    # predicted and actual are lists of {valid_at, elevation}
    assert isinstance(result["predicted"], list)
    assert isinstance(result["actual"], list)
    for pt in result["predicted"]:
        assert "valid_at" in pt and "elevation" in pt
    for pt in result["actual"]:
        assert "valid_at" in pt and "elevation" in pt

    # predicted[0] is the T0 anchor: elevation should equal the observed level at T0
    # (absolute = reading 1.40 + datum offset)
    expected_abs = 1.40 + art.datum.sensor_to_absolute_offset_ft
    assert result["predicted"][0]["elevation"] == pytest.approx(expected_abs, abs=0.01)

    # stop_log_count is an int, data_fresh is bool
    assert isinstance(result["stop_log_count"], int)
    assert isinstance(result["data_fresh"], bool)


def test_capture_storm_freezes_replayable_record(art):
    """capture_storm freezes the same pulled observations into a StormRecord that scores
    offline to the SAME metrics fetch_backtest computes live."""
    from lake_rise import storm_record as SR

    client = httpx.Client(transport=httpx.MockTransport(_backtest_handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)

    rec = src.capture_storm(hours_back=3, label="mock-storm", notes="from the mock transport")
    assert rec.label == "mock-storm"
    assert rec.rain_hourly and rec.level_by_hour            # real observations captured
    assert isinstance(rec.data_fresh, bool)

    # offline replay of the frozen record reproduces the live backtest's metrics
    live = src.fetch_backtest(hours_back=3)["metrics"]
    offline = SR.score(art, rec)["metrics"]
    assert offline["rmse_ft"] == live["rmse_ft"]
    assert offline["peak_err_ft"] == live["peak_err_ft"]


def test_continuous_samples_propagates_rain_history_failure(art):
    """A rain-history HTTP failure must abort the pull, not write zero-rain samples."""
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.startswith("/api/history/period/"):
            if params.get("filter_entity_id") == "sensor.gw3000b_hourly_rain_piezo":
                return httpx.Response(503)
            now = datetime.now(timezone.utc)
            rows = []
            for i in range(5):
                ts = (now - timedelta(hours=4 - i)).replace(minute=0, second=0, microsecond=0)
                rows.append({"state": "1.40", "last_changed": ts.isoformat()})
            return httpx.Response(200, json=[rows])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    with pytest.raises(httpx.HTTPError):
        src.continuous_samples()


def _lake_rows(now):
    """A handful of healthy lake-depth rows over the last few hours."""
    return [{"state": "1.40", "last_changed":
             (now - timedelta(hours=4 - i)).replace(minute=0, second=0, microsecond=0).isoformat()}
            for i in range(5)]


def test_continuous_samples_rejects_empty_rain_fetch(art):
    """A successful-but-empty rain history is data-missing, not a dry spell: it must fail loud
    rather than fabricate an all-zero rain window that poisons the archive as fake dryness."""
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.startswith("/api/history/period/"):
            if params.get("filter_entity_id") == "sensor.gw3000b_hourly_rain_piezo":
                return httpx.Response(200, json=[])          # empty, but 200
            return httpx.Response(200, json=[_lake_rows(datetime.now(timezone.utc))])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    with pytest.raises(RuntimeError, match="no usable samples"):
        src.continuous_samples()


def test_continuous_samples_archives_a_genuine_dry_spell(art):
    """A real dry-but-healthy accumulator reports flat, parseable rows: those zeros are VALID
    recession data and must still be archived (the guard rejects absence, not dryness)."""
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.startswith("/api/history/period/"):
            now = datetime.now(timezone.utc)
            if params.get("filter_entity_id") == "sensor.gw3000b_hourly_rain_piezo":
                rows = [{"state": "0.00", "last_changed":
                         (now - timedelta(hours=4 - i)).replace(minute=0, second=0,
                                                                microsecond=0).isoformat()}
                        for i in range(5)]
                return httpx.Response(200, json=[rows])
            return httpx.Response(200, json=[_lake_rows(now)])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    samples = src.continuous_samples()
    assert samples                                            # not empty
    assert all(s.rain_in == 0.0 for s in samples)            # genuine dry spell, archived
    assert any(s.elev_ft is not None for s in samples)       # with real lake readings
