"""Live HA REST source, with all network calls mocked via httpx.MockTransport."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from lake_rise.sources.snapshot import bundle_from_snapshot
from lake_rise.sources.live_ha import (
    HAConfig,
    LiveConditions,
    LiveHASource,
    time_weighted_gauge_average,
)


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
        if entity_id.startswith("input_number.") and entity_id not in _BUCKET_STATES:
            return httpx.Response(404)     # a helper that doesn't exist (yet)
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


_FLAT_MINUTE_HISTORY = {"sensor.crystal_lake_depth": "1.36",
                        "input_number.crystal_lake_stop_logs": "3.0"}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    params = dict(request.url.params)
    if path.startswith("/api/states/"):
        entity_id = path.split("/api/states/", 1)[1]
        if entity_id.startswith("input_number.") and entity_id not in _BUCKET_STATES:
            return httpx.Response(404)     # a helper that doesn't exist (yet)
        state_val = _BUCKET_STATES.get(entity_id, "1.36")
        return httpx.Response(200, json={"state": state_val, "attributes": {},
                                         "last_reported": datetime.now(timezone.utc).isoformat()})
    if path.startswith("/api/history/period/"):
        now = datetime.now(timezone.utc)
        window_start = path.split("/api/history/period/", 1)[1]
        if params.get("filter_entity_id") in _FLAT_MINUTE_HISTORY:
            # The raw depth and the stop-log helper: flat since the window start, so the lake
            # filter runs (on a flat 1.36) with no stop-log cut.
            return httpx.Response(200, json=[[{
                "state": _FLAT_MINUTE_HISTORY[params["filter_entity_id"]],
                "last_changed": window_start}]])
        if params.get("filter_entity_id") == "sensor.crystal_lake_depth_smoothed":
            # Recent lake-depth samples within the ~1 h anchor window (median -> 1.36).
            return httpx.Response(200, json=[[
                {"state": "1.36", "last_changed": (now - timedelta(minutes=m)).isoformat()}
                for m in (10, 25, 45)
            ]])
        # one small rain event a few hours ago. Like real HA, the history opens with the state
        # held at the window start (the accumulator's baseline).
        t = (now - timedelta(hours=3)).isoformat()
        return httpx.Response(200, json=[[
            {"state": "0.0", "last_changed": window_start},
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
    assert snap.stop_log_count in (2, 3)  # date-based default: summer 3, winter 2
    assert any(v > 0 for v in snap.trailing_rainfall_in)  # the event made it in


@pytest.mark.parametrize("helper_state, expected", [("1.0", 1), ("0", 0), ("7", None),
                                                    ("unavailable", None)])
def test_stop_log_helper_overrides_the_seasonal_default(art, monkeypatch, helper_state, expected):
    """The operator's board-count helper wins; a missing or out-of-range value falls back to
    the date-based default rather than failing the forecast."""
    monkeypatch.setitem(_BUCKET_STATES, "input_number.crystal_lake_stop_logs", helper_state)
    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test")
    cfg = HAConfig(base_url="http://test", token="x",
                   stop_log_helper="input_number.crystal_lake_stop_logs")
    count = LiveHASource(art, cfg, client=client).fetch_snapshot().stop_log_count
    assert count == expected if expected is not None else count in (2, 3)


def _stop_log_source(art, monkeypatch, helper_state):
    monkeypatch.setitem(_BUCKET_STATES, "input_number.crystal_lake_stop_logs", helper_state)
    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test")
    cfg = HAConfig(base_url="http://test", token="x",
                   stop_log_helper="input_number.crystal_lake_stop_logs")
    return LiveHASource(art, cfg, client=client)


def test_stop_log_count_from_the_helper_is_not_a_default(art, monkeypatch):
    src = _stop_log_source(art, monkeypatch, "1.0")
    snap = src.fetch_snapshot()
    assert (snap.stop_log_count, snap.stop_log_count_is_default) == (1, False)
    assert bundle_from_snapshot(art, snap).stop_log_count_is_default is False


def test_missing_helper_gives_the_seasonal_default_marked_default(art, monkeypatch):
    src = _stop_log_source(art, monkeypatch, "unavailable")
    snap = src.fetch_snapshot()
    assert snap.stop_log_count in (2, 3)
    assert snap.stop_log_count_is_default is True
    assert bundle_from_snapshot(art, snap).stop_log_count_is_default is True


def test_build_bundle_applies_datum(live_source, art):
    bundle = live_source.build_bundle()
    assert bundle.current_elevation_abs_ft == pytest.approx(
        1.36 + art.datum.sensor_to_absolute_offset_ft, abs=0.01)
    assert len(bundle.forecast_scenarios) == 3


def test_observed_gauge_uses_fresh_15_minute_average(live_source):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    obs = live_source.fetch_gauge_observation(now)
    # Handler contributes the 10-minute sample and the current 1.36 state; older samples
    # are outside this dedicated 15-minute window.
    assert obs.confirmed is True and obs.sample_count >= 2
    assert obs.gauge_ft == pytest.approx(1.36)


def test_observed_average_time_weights_short_spike_and_carries_boundary():
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=15)
    samples = [
        (start - timedelta(minutes=5), 3.20),  # carried to the window boundary
        (now - timedelta(minutes=1), 3.80),   # short spike, not one-third of the window
        (now, 3.20),
    ]
    average, count = time_weighted_gauge_average(samples, start, now)
    assert count >= 3
    assert average == pytest.approx(3.24)      # below the 3.30 EAP threshold


def test_observed_average_confirms_steady_value_from_pre_window_boundary():
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=15)
    average, count = time_weighted_gauge_average(
        [(start - timedelta(minutes=10), 3.35), (now, 3.35)], start, now)
    assert average == pytest.approx(3.35) and count >= 2


def test_observed_gauge_degrades_to_fresh_instantaneous_when_history_fails(art):
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/states/"):
            return httpx.Response(200, json={
                "state": "3.35", "last_reported": now.isoformat(), "attributes": {}})
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    obs = src.fetch_gauge_observation(now)
    assert obs.confirmed is False and obs.gauge_ft == pytest.approx(3.35)
    assert "history" in obs.degraded_reason


def test_observed_gauge_rejects_stale_current_data(art):
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(hours=1)).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "state": "4.50", "last_reported": old, "attributes": {}})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    obs = src.fetch_gauge_observation(now)
    assert obs.gauge_ft is None and obs.confirmed is False
    assert "stale" in obs.degraded_reason


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


def test_gauge_age_in_minutes_from_the_liveness_sensor(live_source):
    now = datetime.now(timezone.utc) + timedelta(minutes=7)    # the mock reports "just now"
    assert live_source.gauge_age_minutes(now) in (7, 8)


def test_gauge_age_none_when_ha_fails_or_there_is_no_timestamp(art):
    cfg = HAConfig(base_url="http://test", token="x")
    now = datetime.now(timezone.utc)
    for handler in (lambda r: httpx.Response(500),
                    lambda r: httpx.Response(200, json={"state": "1", "attributes": {}})):
        client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
        assert LiveHASource(art, cfg, client=client).gauge_age_minutes(now) is None


def _rain_rate(art, now, *, state="0.12", reported=None, status=200):
    reported = reported or now
    handler = lambda r: httpx.Response(status, json={
        "state": state, "last_reported": reported.isoformat(), "attributes": {}})
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    cfg = HAConfig(base_url="http://test", token="x")
    return LiveHASource(art, cfg, client=client).rain_rate_in_per_h(now), cfg


def test_rain_rate_reads_the_sensor(art):
    now = datetime.now(timezone.utc)
    assert _rain_rate(art, now)[0] == pytest.approx(0.12)


def test_rain_rate_is_none_on_an_http_error_or_a_non_numeric_state(art):
    now = datetime.now(timezone.utc)
    assert _rain_rate(art, now, status=500)[0] is None
    for state in ("unavailable", "unknown", "nan", "inf", "-0.5"):
        assert _rain_rate(art, now, state=state)[0] is None


def test_rain_rate_is_none_when_older_than_the_gauge_stale_limit(art):
    now = datetime.now(timezone.utc)
    limit = HAConfig(base_url="http://test", token="x").lake_stale_minutes
    assert _rain_rate(art, now, reported=now - timedelta(minutes=limit - 1))[0] == pytest.approx(0.12)
    assert _rain_rate(art, now, reported=now - timedelta(minutes=limit + 1))[0] is None


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


_DAILY_RAIN = "sensor.gw3000b_daily_rain_piezo"


_STOP_LOGS = "input_number.crystal_lake_stop_logs"


def _rain_source(art, rain_response, stop_log_rows=lambda now: []):
    """A LiveHASource whose daily-rain history is ``rain_response(now)`` (an httpx.Response),
    whose lake history is a handful of healthy rows, and whose stop-log helper history is
    ``stop_log_rows(now)`` (none by default)."""
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.startswith("/api/history/period/"):
            now = datetime.now(timezone.utc)
            if params.get("filter_entity_id") == _DAILY_RAIN:
                return rain_response(now)
            if params.get("filter_entity_id") == _STOP_LOGS:
                return httpx.Response(200, json=[stop_log_rows(now)])
            return httpx.Response(200, json=[_lake_rows(now)])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    return LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)


def _lake_rows(now):
    """A handful of healthy lake-depth rows over the last few hours."""
    return [{"state": "1.40", "last_changed":
             (now - timedelta(hours=4 - i)).replace(minute=0, second=0, microsecond=0).isoformat()}
            for i in range(5)]


def _window_start(now, art_days=10):
    return (now - timedelta(days=art_days)).replace(minute=0, second=0, microsecond=0)


def test_continuous_samples_propagates_rain_history_failure(art):
    """A rain-history HTTP failure must abort the pull, not write zero-rain samples."""
    src = _rain_source(art, lambda now: httpx.Response(503))
    with pytest.raises(httpx.HTTPError):
        src.continuous_samples()


def test_continuous_samples_rejects_empty_rain_fetch(art):
    """A successful-but-empty rain history is data-missing, not a dry spell: it must fail loud
    rather than fabricate an all-zero rain window that poisons the archive as fake dryness."""
    src = _rain_source(art, lambda now: httpx.Response(200, json=[]))
    with pytest.raises(RuntimeError, match="no usable samples"):
        src.continuous_samples()


def test_continuous_samples_archives_a_genuine_dry_spell(art):
    """HA writes no rows while the accumulator holds its value, so a dry spell is just the state
    held at the window start. Those hours are VALID observed-dry data: archived as 0.0, not missing
    (the old 6 h staleness rule marked them missing -- see the 2026-11-04 log entry)."""
    src = _rain_source(art, lambda now: httpx.Response(200, json=[[
        {"state": "0.0", "last_changed": _window_start(now).isoformat()}]]))
    samples = src.continuous_samples()
    assert samples
    assert all(s.rain_in == 0.0 for s in samples)


def test_continuous_samples_record_the_stop_log_count(art):
    """The archive keeps the operator's board count per hour (HA keeps the helper's history only
    ~10 days), so a later backtest or re-fit of that hour uses the true spillway crest."""
    def logs(now):
        h = now.replace(minute=0, second=0, microsecond=0)
        return [{"state": "3.0", "last_changed": _window_start(now).isoformat()},
                {"state": "2.0", "last_changed": (h - timedelta(hours=2, minutes=40)).isoformat()}]

    dry = lambda now: httpx.Response(200, json=[[  # noqa: E731
        {"state": "0.0", "last_changed": _window_start(now).isoformat()}]])
    samples = _rain_source(art, dry, logs).continuous_samples()
    counts = [s.stop_log_count for s in samples]
    assert counts[0] == 3 and counts[-1] == 2
    assert counts.count(2) == 3                   # the hour it changed, and the two after
    assert all(s.stop_log_count is None for s in _rain_source(art, dry).continuous_samples())


def test_continuous_samples_rain_is_the_hourly_accumulator_increase(art):
    """Rain is the daily accumulator's rise within each clock hour, including across the
    midnight reset -- not the rolling hourly sensor's per-hour max (which double-counts)."""
    def rows(now):
        h = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
        return httpx.Response(200, json=[[
            {"state": "0.30", "last_changed": _window_start(now).isoformat()},
            {"state": "0.40", "last_changed": (h + timedelta(minutes=10)).isoformat()},
            {"state": "0.55", "last_changed": (h + timedelta(minutes=50)).isoformat()},
            {"state": "0.0", "last_changed": (h + timedelta(hours=1, minutes=5)).isoformat()},
            {"state": "0.05", "last_changed": (h + timedelta(hours=1, minutes=30)).isoformat()},
        ]])

    samples = _rain_source(art, rows).continuous_samples()
    wet = [s.rain_in for s in samples if s.rain_in]
    assert wet == [pytest.approx(0.25), pytest.approx(0.05)]
    assert sum(s.rain_in for s in samples) == pytest.approx(0.30)


def test_continuous_samples_marks_unavailable_hours_missing(art):
    """An unavailable accumulator is a real gap: those hours are archived as missing (None), and
    the value it comes back with is a new baseline, not rain."""
    def rows(now):
        t = now.replace(minute=0, second=0, microsecond=0)
        return httpx.Response(200, json=[[
            {"state": "0.10", "last_changed": _window_start(now).isoformat()},
            {"state": "unavailable", "last_changed": (t - timedelta(hours=30, minutes=-20)).isoformat()},
            {"state": "0.60", "last_changed": (t - timedelta(hours=20, minutes=-20)).isoformat()},
        ]])

    samples = _rain_source(art, rows).continuous_samples()
    missing = [s for s in samples if s.rain_missing]
    assert len(missing) == 11                                 # the unavailable stretch, inclusive
    assert all(s.rain_in in (None, 0.0) for s in samples)    # the 0.10 -> 0.60 jump is not rain


def test_statistics_rain_samples_backfill_from_long_term_statistics(art, monkeypatch):
    """The statistics backfill lays each hour's long-term ``change`` on the grid as rain-only
    samples; an hour with no statistic stays missing, never dry."""
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"),
                       client=httpx.Client(base_url="http://test"))
    seen = {}

    def fake_stats(statistic_id, start, end):
        seen["id"] = statistic_id
        return [{"start": start.timestamp() * 1000, "change": 0.12},
                {"start": (start + timedelta(hours=1)).timestamp() * 1000, "change": 0.0}]

    monkeypatch.setattr(src, "_get_statistics", fake_stats)
    samples = src.statistics_rain_samples(days=1)
    assert seen["id"] == _DAILY_RAIN
    assert len(samples) == 24
    assert [s.rain_in for s in samples[:3]] == [0.12, 0.0, None]
    assert all(s.elev_ft is None for s in samples)            # rain-only: never touches levels


def _snapshot_source(art, rain_rows):
    """The standard live mock, but the daily-accumulator history is ``rain_rows(now, start_iso)``."""
    def handler(request: httpx.Request) -> httpx.Response:
        if (request.url.path.startswith("/api/history/period/")
                and dict(request.url.params).get("filter_entity_id") == _DAILY_RAIN):
            start = request.url.path.split("/api/history/period/", 1)[1]
            return httpx.Response(200, json=[rain_rows(datetime.now(timezone.utc), start)])
        return _handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    return LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)


def test_snapshot_hindcast_rain_is_the_accumulator_rise(art):
    """The live hindcast reads the same true hourly rain as the archive: one 0.25 in hour, not
    the rolling hourly sensor's double count."""
    def rows(now, start):
        h = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
        return [{"state": "0.0", "last_changed": start},
                {"state": "0.10", "last_changed": (h + timedelta(minutes=10)).isoformat()},
                {"state": "0.25", "last_changed": (h + timedelta(minutes=50)).isoformat()}]

    snap = _snapshot_source(art, rows).fetch_snapshot()
    assert len(snap.trailing_rainfall_in) == 10 * 24
    assert sum(snap.trailing_rainfall_in) == pytest.approx(0.25)
    assert max(snap.trailing_rainfall_in) == pytest.approx(0.25)
    assert snap.rainfall_has_gaps is False


@pytest.mark.parametrize("outage_h, degraded", [(2, False), (10, True)])
def test_snapshot_flags_a_real_accumulator_outage(art, outage_h, degraded):
    """An accumulator unavailable for a real stretch degrades the hindcast (-> seasonal floor);
    a brief blip does not."""
    def rows(now, start):
        t = now.replace(minute=0, second=0, microsecond=0)
        return [{"state": "0.0", "last_changed": start},
                {"state": "unavailable", "last_changed": (t - timedelta(hours=30)).isoformat()},
                {"state": "0.0", "last_changed": (t - timedelta(hours=30 - outage_h)).isoformat()}]

    snap = _snapshot_source(art, rows).fetch_snapshot()
    assert snap.rainfall_has_gaps is degraded
    assert all(v == 0.0 for v in snap.trailing_rainfall_in)        # gap hours drive as 0.0


def test_snapshot_retention_edge_is_not_an_outage(art):
    """HA purged the start-state row (a dry spell at the window's start, older than its raw
    retention): the hours before the oldest retained row are not an accumulator outage."""
    def rows(now, start):
        t = now.replace(minute=0, second=0, microsecond=0)
        return [{"state": "0.0", "last_changed": (t - timedelta(days=9)).isoformat()},
                {"state": "0.20", "last_changed": (t - timedelta(hours=5, minutes=30)).isoformat()}]

    snap = _snapshot_source(art, rows).fetch_snapshot()
    assert snap.rainfall_has_gaps is False
    assert sum(snap.trailing_rainfall_in) == pytest.approx(0.20)


def _write_archive_rain(start, hours, rain=0.0):
    from lake_rise.calibration import archive
    archive.append_samples([archive.HourSample(hour=(start + timedelta(hours=i)).isoformat(),
                                               rain_in=rain, elev_ft=None) for i in range(hours)])


def test_build_bundle_spins_up_from_the_archive_when_it_covers_the_window(art):
    """With enough archived true rain, the live bundle carries the replayed state and the
    predictor uses it instead of the 10-day seasonal-seed hindcast."""
    from lake_rise.predict import predict

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    _write_archive_rain(now - timedelta(days=60), 60 * 24)              # 60 dry days, up to now
    src = _snapshot_source(art, lambda now_, start: [{"state": "0.0", "last_changed": start}])
    bundle = src.build_bundle()
    assert bundle.spinup_state is not None
    assert bundle.spinup_state.history_days >= 59
    assert predict(bundle, art).input_summary["spinup"] == "archive_replay"


def test_build_bundle_keeps_the_hindcast_without_archive_or_when_degraded(art):
    from lake_rise.predict import predict

    # No archive at all (the hermetic test archive dir is empty): only the 10 live days exist,
    # which still meets the minimum, so make the live rain degraded to force the fallback too.
    def outage(now_, start):
        t = now_.replace(minute=0, second=0, microsecond=0)
        return [{"state": "0.0", "last_changed": start},
                {"state": "unavailable", "last_changed": (t - timedelta(hours=30)).isoformat()},
                {"state": "0.0", "last_changed": (t - timedelta(hours=20)).isoformat()}]

    bundle = _snapshot_source(art, outage).build_bundle()
    assert bundle.rainfall_has_gaps is True and bundle.spinup_state is None
    assert predict(bundle, art).input_summary["spinup"] == "trailing_hindcast"


def test_backtest_spins_up_from_the_archive_like_the_live_forecast(art):
    """The backtest's T0 state is the same archive replay the live forecast uses, and a captured
    storm freezes it, so scoring the record offline reproduces the live backtest exactly."""
    from lake_rise import storm_record as SR

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    _write_archive_rain(now - timedelta(days=40), 40 * 24)
    src = _snapshot_source(art, lambda now_, start: [{"state": "0.0", "last_changed": start}])
    live = src.fetch_backtest(hours_back=3)
    assert live["gw_seed_source"] == "assimilated"

    rec = src.capture_storm(hours_back=3, label="replayed")
    assert rec.spinup_state is not None and rec.spinup_state.history_days >= 39
    assert SR.score(art, rec)["metrics"] == live["metrics"]
    # The frozen state round-trips through the record's JSON.
    assert SR.StormRecord.model_validate_json(rec.model_dump_json()).spinup_state == rec.spinup_state


def test_backtest_falls_back_to_the_seasonal_hindcast_without_archive_coverage(art):
    # Empty archive and only ~10 d of live rain before T0 (< the 10 d minimum once T0 is 3 h back).
    src = _snapshot_source(art, lambda now_, start: [{"state": "0.0", "last_changed": start}])
    assert src.fetch_backtest(hours_back=3)["gw_seed_source"] == "seasonal_default"
    assert src.capture_storm(hours_back=3, label="short").spinup_state is None


def test_statistics_met_samples_archives_weather_and_drops_sentinels(art, monkeypatch):
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"),
                       client=httpx.Client(base_url="http://test"))

    def fake_many(ids, start, end, types):
        assert types == ("mean",)
        row = lambda v: [{"start": start.timestamp() * 1000, "mean": v}]
        return {"sensor.carport_temp_corrected": row(41.3), "sensor.carport_temp": row(48.1),
                "sensor.carport_humidity": row(57.0), "sensor.gw3000b_wind_speed": row(1.5),
                "sensor.lake_temperature": row(-40.0)}                     # old probe's sentinel

    monkeypatch.setattr(src, "_get_statistics_many", fake_many)
    s = src.statistics_met_samples(days=1)[0]
    assert (s.air_temp_f, s.air_temp_raw_f, s.rh_pct, s.wind_mph) == (41.3, 48.1, 57.0, 1.5)
    assert s.water_temp_f is None and s.elev_ft is None and s.rain_in is None


def test_an_invalid_gauge_reading_is_degraded_even_while_the_sensor_reports(art):
    """2026-11-04: the laser read the lake bottom instead of the surface; the depth template went
    unknown and the smoothed sensor held its last sample with source_value_valid=false while the
    raw air-gap sensor kept reporting. That must read as a degraded gauge everywhere, not a flat lake."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/states/sensor.crystal_lake_depth_smoothed":
            return httpx.Response(200, json={
                "state": "0.54", "last_reported": datetime.now(timezone.utc).isoformat(),
                "attributes": {"source_value_valid": False, "age_coverage_ratio": 0}})
        return _handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    assert "invalid" in src.gauge_problem(now)
    obs = src.fetch_gauge_observation(now)
    assert obs.gauge_ft is None and not obs.confirmed
    assert src.fetch_snapshot().rainfall_has_gaps is True


def _invalid_source(art, lost_hours_ago):
    """A live sensor whose depth lost the water surface ``lost_hours_ago`` hours ago."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    lost = (now - timedelta(hours=lost_hours_ago)).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/states/sensor.crystal_lake_depth_smoothed":
            return httpx.Response(200, json={
                "state": "0.9", "last_changed": lost, "last_reported": now.isoformat(),
                "attributes": {"source_value_valid": False}})
        if request.url.path == "/api/states/sensor.crystal_lake_depth":
            return httpx.Response(200, json={"state": "unknown", "last_changed": lost})
        return _handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    return LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client), now


def test_the_field_flag_waits_out_routine_far_echo_gaps(art):
    """2026-11-27: the laser's far echo blanks the surface for ~3 h routinely (4.7 h in the
    11-26 storm) while the sensor reports. The field flag waits 6 h; the model doesn't wait."""
    src, now = _invalid_source(art, 4.7)
    assert "invalid" in src.gauge_problem(now)            # still degraded for the model
    assert src.gauge_alert(now) is None                   # but not flagged on the page
    assert src.surface_age_minutes(now) == 282

    src, now = _invalid_source(art, 6.5)
    assert src.gauge_alert(now) == "no valid water-surface reading for 6 h"


def test_a_valid_reading_has_no_surface_age(live_source):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    assert live_source.surface_age_minutes(now) is None


def test_archive_spinup_keeps_archived_rain_over_live_zero_filled_hours(art):
    """Review finding: the live trailing series drives unavailable hours as 0.0; topping up the
    archive must not overwrite real (e.g. statistics-repaired) archived rain with those zeros."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    _write_archive_rain(now - timedelta(days=20), 20 * 24, rain=0.01)    # real archived rain
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"),
                       client=httpx.Client(base_url="http://test"))
    with_archive = src.archive_spinup(now, [0.0] * 240)                 # live: all "unavailable"
    wet = src.archive_spinup(now, [0.01] * 240)
    assert with_archive.sm_in == pytest.approx(wet.sm_in)              # archive values won


def _trend_source(art, rows):
    """A source whose lake history serves ``rows`` (or HTTP 500 when None)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if rows is None:
            return httpx.Response(500)
        return httpx.Response(200, json=[rows])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    return LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)


def _rows_at(now, minutes_ago_and_depth):
    return [{"state": str(d), "last_changed": (now - timedelta(minutes=m)).isoformat()}
            for m, d in minutes_ago_and_depth]


def test_lake_trend_compares_the_last_half_hour_with_an_hour_earlier(art):
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    rows = _rows_at(now, [(85, 1.00), (75, 1.00), (65, 1.00), (25, 1.10), (15, 1.10), (5, 1.10)])
    assert _trend_source(art, rows).lake_trend_ft_per_h(now) == pytest.approx(0.100, abs=0.001)


def test_lake_trend_is_none_without_older_samples(art):
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    rows = _rows_at(now, [(25, 1.10), (15, 1.10), (5, 1.10)])
    assert _trend_source(art, rows).lake_trend_ft_per_h(now) is None


def test_lake_trend_is_none_when_history_fails(art):
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    assert _trend_source(art, None).lake_trend_ft_per_h(now) is None


# --- the filtered lake anchor (docs/plans/lake-filter.md) -------------------------------------

_RAW_DEPTH = "sensor.crystal_lake_depth"
_HELPER = "input_number.crystal_lake_stop_logs"


def _filter_source(art, *, depth=None, helper=None, smoothed=None, seen=None, rain=None,
                   rain_reported_minutes_ago=0):
    """The standard live mock, dry unless ``rain(now, start)`` gives the daily accumulator's rows,
    with the raw depth's and the stop-log helper's history as ``depth(now, start)`` /
    ``helper(now, start)`` rows (None -> the flat default; an int -> that HTTP status) and the
    smoothed depth's (today's median's) as ``smoothed(now)``. The rain gauge last reported
    ``rain_reported_minutes_ago``. Every history request's entity is appended to ``seen``."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/states/{_DAILY_RAIN}":
            reported = datetime.now(timezone.utc) - timedelta(minutes=rain_reported_minutes_ago)
            return httpx.Response(200, json={"state": "0.0", "attributes": {},
                                             "last_reported": reported.isoformat()})
        if request.url.path.startswith("/api/history/period/"):
            entity = dict(request.url.params).get("filter_entity_id")
            start = request.url.path.split("/api/history/period/", 1)[1]
            now = datetime.now(timezone.utc)
            if seen is not None:
                seen.append(entity)
            rows = {_RAW_DEPTH: depth, _HELPER: helper, _DAILY_RAIN: rain or (lambda now_, s: [
                {"state": "0.0", "last_changed": s}])}.get(entity)
            if entity == "sensor.crystal_lake_depth_smoothed" and smoothed is not None:
                return httpx.Response(200, json=[smoothed(now)])
            if isinstance(rows, int):
                return httpx.Response(rows)
            if rows is not None:
                return httpx.Response(200, json=[rows(now, start)])
        return _handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    return LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)


def _at(now, minutes_ago, state):
    return {"state": state, "last_changed": (now - timedelta(minutes=minutes_ago)).isoformat()}


def test_build_bundle_starts_the_forecast_at_the_filtered_level(art):
    """Flat 1.00 ft, then 1.10 for the last 30 min, dry: today's median already reads 1.10; the
    filter treats the step as the gauge's offset and stays nearer 1.00."""
    src = _filter_source(
        art, depth=lambda now, start: [{"state": "1.00", "last_changed": start}, _at(now, 30, "1.10")],
        smoothed=lambda now: [_at(now, m, "1.10") for m in (5, 15, 25)])
    assert src.fetch_snapshot().lake_depth_reading_ft == 1.10           # today's anchor
    bundle = src.build_bundle()
    assert src.lake_estimate is not None
    assert bundle.current_elevation_abs_ft - art.datum.sensor_to_absolute_offset_ft < 1.05


def test_a_stop_log_change_cuts_the_filters_history(art, monkeypatch):
    """The helper went 3 -> 2 two hours ago: the filter runs on those 2 h only (P7), so the
    1.50 ft before the change doesn't drag the level."""
    from lake_rise.geometry import control_elev_for_stop_logs
    from lake_rise.lake_filter import estimate_lake

    monkeypatch.setitem(_BUCKET_STATES, _HELPER, "2.0")
    src = _filter_source(
        art,
        depth=lambda now, start: [{"state": "1.50", "last_changed": start}, _at(now, 120, "1.00")],
        helper=lambda now, start: [{"state": "3.0", "last_changed": start}, _at(now, 120, "2.0")])
    monkeypatch.setattr(src, "archive_replay", lambda as_of, rain: None)   # no inflow: u_in = 0
    bundle = src.build_bundle()
    est = src.lake_estimate
    # 119-121 minutes: the mock stamps the change from each request's clock, which can cross a
    # minute boundary after build_bundle read its own.
    assert bundle.stop_log_count == 2 and 119 <= est.minutes <= 121
    expected = estimate_lake(art, [1.00] * est.minutes, [0.0] * est.minutes,
                             inflow_cfs_by_minute=None,
                             control_elev_abs=control_elev_for_stop_logs(art.stop_logs, 2),
                             month=bundle.as_of.month)
    assert est.level_ft == pytest.approx(expected.level_ft, abs=1e-3)
    assert est.rate_ft_per_h == pytest.approx(expected.rate_ft_per_h, abs=1e-3)


def test_build_bundle_falls_back_to_the_30_min_median_when_the_minute_history_fails(art):
    src = _filter_source(art, depth=500)
    bundle = src.build_bundle()
    assert src.lake_estimate is None
    assert bundle.current_elevation_abs_ft == 1.36 + art.datum.sensor_to_absolute_offset_ft


def test_a_non_finite_filter_result_keeps_the_30_min_median(art, monkeypatch):
    import lake_rise.lake_filter as lf
    monkeypatch.setattr(lf, "m5_lake_update", lambda *a, **k: (float("nan"),) * 4)
    src = _filter_source(art)
    bundle = src.build_bundle()
    assert src.lake_estimate is None
    assert bundle.current_elevation_abs_ft == 1.36 + art.datum.sensor_to_absolute_offset_ft


def test_a_lake_rising_past_the_top_of_the_geometry_keeps_the_30_min_median(art):
    top = art.geometry.valid_elev_range_ft[1] - art.datum.sensor_to_absolute_offset_ft
    src = _filter_source(art, depth=lambda now, start: [
        {"state": f"{top - 0.3:.2f}", "last_changed": start},
        _at(now, 8, f"{top - 0.02:.2f}"), _at(now, 4, f"{top + 0.05:.2f}")])
    bundle = src.build_bundle()
    assert src.lake_estimate is None
    assert bundle.current_elevation_abs_ft == 1.36 + art.datum.sensor_to_absolute_offset_ft


def test_no_lake_filter_in_the_artifact_keeps_todays_anchor(art):
    seen = []
    src = _filter_source(art.model_copy(update={"lake_filter": None}), seen=seen)
    bundle = src.build_bundle()
    assert src.lake_estimate is None and _RAW_DEPTH not in seen
    assert bundle.current_elevation_abs_ft == 1.36 + art.datum.sensor_to_absolute_offset_ft


def test_a_callers_lake_reading_is_not_filtered(art):
    """The observed-EAP path passes the gauge's 15-min average (P11): it wins."""
    src = _filter_source(art)
    bundle = src.build_bundle(lake_reading_ft=1.50)
    assert src.lake_estimate is None
    assert bundle.current_elevation_abs_ft == 1.50 + art.datum.sensor_to_absolute_offset_ft


def _filter_inputs(monkeypatch):
    """Record what build_bundle hands the filter: [(readings, rain, inflow)]."""
    import lake_rise.sources.live_ha as live_ha
    calls, real = [], live_ha.estimate_lake

    def spy(art, readings, rain, **kwargs):
        calls.append((readings, rain, kwargs["inflow_cfs_by_minute"]))
        return real(art, readings, rain, **kwargs)
    monkeypatch.setattr(live_ha, "estimate_lake", spy)
    return calls


def _step(now, start):
    """Flat 1.00 ft, then 1.10 for the last 30 min."""
    return [{"state": "1.00", "last_changed": start}, _at(now, 30, "1.10")]


def _smoothed_110(now):
    return [_at(now, m, "1.10") for m in (5, 15, 25)]


def test_build_bundle_in_rain_keeps_the_30_min_median(art):
    """The step that the dry filter holds near 1.00 (above), with 0.2 in of rain an hour ago."""
    src = _filter_source(art, depth=_step, smoothed=_smoothed_110, rain=lambda now, start: [
        {"state": "0.0", "last_changed": start}, _at(now, 60, "0.2")])
    bundle = src.build_bundle()
    assert src.lake_estimate is None
    assert bundle.current_elevation_abs_ft == pytest.approx(1.10 + art.datum.sensor_to_absolute_offset_ft)


def test_a_stop_log_change_doesnt_hide_the_rain_before_it(art, monkeypatch):
    """Rain in the clock hour holding 2.5 h ago (over before the cut), the stop logs pulled 90 min
    ago: the rain is still within the wet window, so today's median takes over."""
    monkeypatch.setitem(_BUCKET_STATES, _HELPER, "2.0")

    def estimate(rain):
        src = _filter_source(art, rain=rain, helper=lambda now, start: [
            {"state": "3.0", "last_changed": start}, _at(now, 90, "2.0")])
        src.build_bundle()
        return src.lake_estimate

    assert estimate(None).minutes <= 91                                       # dry: the cut run
    assert estimate(lambda now, start: [{"state": "0.0", "last_changed": start},
                                        _at(now, 150, "1.0")]) is None


def test_a_rain_gauge_that_stopped_reporting_counts_the_current_hour_as_unknown(art, monkeypatch):
    calls = _filter_inputs(monkeypatch)
    limit = HAConfig(base_url="", token="").lake_stale_minutes
    _filter_source(art, rain_reported_minutes_ago=limit + 5).build_bundle()
    _filter_source(art, rain_reported_minutes_ago=limit - 5).build_bundle()
    (_, stale, _), (_, fresh, _) = calls
    assert stale[-1] is None and fresh[-1] == 0.0


def test_the_inflow_and_rain_minutes_share_each_clock_hour(art, monkeypatch):
    """Distinct rain and replay inflow every hour, none in the current hour: the hour ending at the
    top of this one has the replay's last inflow and its rain on the same minutes, and the current
    hour holds that inflow (the replay ends at the top of the hour)."""
    calls = _filter_inputs(monkeypatch)
    hours = 14

    def rain(now, start):
        top = now.replace(minute=0, second=0, microsecond=0)
        rows, total = [{"state": "0.0", "last_changed": start}], 0.0
        for back in range(hours, 0, -1):
            total += back / 100                       # the hour `back` before the top: back/100 in
            rows.append({"state": f"{total:.2f}",
                         "last_changed": (top - timedelta(hours=back, minutes=-30)).isoformat()})
        return rows
    src = _filter_source(art, rain=rain)
    q = [float(i) for i in range(48)]                 # the replay's hourly inflow, oldest first
    monkeypatch.setattr(src, "archive_replay", lambda as_of, r: (None, q))
    src.build_bundle()
    (_, by_min, inflow), = calls
    c = max(i for i in range(1, len(by_min)) if by_min[i] != by_min[i - 1])   # this hour starts
    assert by_min[c:] == [0.0] * (len(by_min) - c)
    assert inflow[c - 60:] == [q[-1]] * (len(inflow) - c + 60)
    assert by_min[c - 60:c] == [pytest.approx(0.01 / 60)] * 60
    assert (inflow[c - 61], by_min[c - 61]) == (q[-2], pytest.approx(0.02 / 60))


def test_minute_history_keeps_unknown_and_unavailable_states_and_parses_times(art):
    rows = [{"state": "1.36", "last_changed": "2026-11-15T10:00:00Z"},
            {"state": "unknown", "last_changed": "2026-11-15T10:01:00+00:00"},
            {"state": "unavailable", "last_changed": "2026-11-15T10:02:30.5+00:00"},
            {"state": "1.37", "last_changed": "not a time"}]
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[rows])),
                          base_url="http://test")
    src = LiveHASource(art, HAConfig(base_url="http://test", token="x"), client=client)
    t = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
    assert src.minute_history([_RAW_DEPTH], t, t + timedelta(minutes=5)) == {_RAW_DEPTH: [
        (t, "1.36"), (t + timedelta(minutes=1), "unknown"),
        (t + timedelta(minutes=2, seconds=30, microseconds=500000), "unavailable")]}
