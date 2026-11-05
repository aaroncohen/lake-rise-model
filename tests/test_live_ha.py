"""Live HA REST source, with all network calls mocked via httpx.MockTransport."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

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
        if params.get("filter_entity_id") == "sensor.crystal_lake_depth_smoothed":
            # Recent lake-depth samples within the ~1 h anchor window (median -> 1.36).
            return httpx.Response(200, json=[[
                {"state": "1.36", "last_changed": (now - timedelta(minutes=m)).isoformat()}
                for m in (10, 25, 45)
            ]])
        # one small rain event a few hours ago. Like real HA, the history opens with the state
        # held at the window start (the accumulator's baseline).
        t = (now - timedelta(hours=3)).isoformat()
        window_start = path.split("/api/history/period/", 1)[1]
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


def test_build_bundle_applies_datum(live_source, art):
    bundle = live_source.build_bundle()
    assert bundle.current_elevation_abs_ft == 1.36 + art.datum.sensor_to_absolute_offset_ft
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
    """2026-11-04: the ultrasonic locked onto the bottom echo; the depth template went unknown and
    the smoothed sensor held its last sample with source_value_valid=false while the raw air-gap
    sensor kept reporting. That must read as a degraded gauge everywhere, not a flat lake."""
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
