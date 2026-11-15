"""The stateless prediction API."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from lake_rise.api import create_app
from lake_rise.bundle import InputBundle
from lake_rise.sources.live_ha import HAConfig, LiveConditions


@pytest.fixture
def client(art):
    return TestClient(create_app(art))


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["anchors_pass"] is True
    assert body["live_source_configured"] is False  # no HA creds in test env
    # Alerting block present; disabled by default in the hermetic test env.
    alerting = body["alerting"]
    assert alerting["enabled"] is False
    assert alerting["scheduler_running"] is False
    assert "channels" in alerting


def test_alert_run_dry_run_on_snapshot(client, monkeypatch):
    """/alert/run evaluates without sending; with the test trigger on a wet fixture it
    reports the TEST action. Routes through the fixture so no HA is needed."""
    from lake_rise.alerting import run_once as real_run_once
    import lake_rise.api as api_mod
    from lake_rise.artifact import load_artifact
    from lake_rise.sources.fixture import FixtureSource

    monkeypatch.setenv("ALERT_TEST_ENABLED", "1")
    monkeypatch.setenv("ALERT_STATE_PATH", "/tmp/lake_rise_api_test_state.json")
    art = load_artifact()
    bundle = FixtureSource(art, "fixtures/example_snapshot.json").build_bundle()

    def fake_run_once(config, *, art=None, dry_run=True, **kw):
        return real_run_once(config, bundle=bundle, art=art, dry_run=dry_run)

    monkeypatch.setattr(api_mod, "run_once", fake_run_once, raising=False)
    # Patch the name imported lazily inside the endpoint.
    monkeypatch.setattr("lake_rise.alerting.run_once", fake_run_once, raising=False)

    r = client.post("/alert/run", params={"dry_run": True})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert "TEST" in body["actions"]
    assert body["sent"] is False


def test_alert_run_send_path_is_token_gated(client, monkeypatch):
    """A real send (dry_run=false) requires the X-Alert-Token header to match
    ALERT_API_TOKEN; preview (dry_run=true) stays open. The gate is enforced before
    run_once, so a stub stands in for the (otherwise live) evaluation."""
    from datetime import datetime, timezone

    from lake_rise.alerting.rules import AlertDecision
    from lake_rise.alerting.service import RunResult

    dec = AlertDecision(
        generated_at=datetime(2026, 1, 15, tzinfo=timezone.utc), horizon_hours=72,
        current_elevation=339.0, freeboard_ft=3.0, datum_offset_ft=338.375, data_fresh=True,
        active_rank=0, active_level_name=None, probabilities={}, thresholds=(),
        peak_elevation=339.0, peak_at=None, peak_elevation_high=339.0, forecast_total_in=0.0,
        peak_rain_hour=None, confidence_pct=90, confidence_label="High", test_active=False)
    calls: list[bool] = []

    def stub(config, *, art=None, dry_run=True, **kw):
        calls.append(dry_run)
        return RunResult(decision=dec, actions=[], sent=False)

    monkeypatch.setattr("lake_rise.alerting.run_once", stub, raising=False)

    # dry_run=true preview is open even with no token, and reaches run_once.
    assert client.post("/alert/run", params={"dry_run": True}).status_code == 200
    assert calls == [True]
    calls.clear()

    # dry_run=false with ALERT_API_TOKEN unset -> send path disabled (403), run_once not reached.
    monkeypatch.delenv("ALERT_API_TOKEN", raising=False)
    r = client.post("/alert/run", params={"dry_run": False})
    assert r.status_code == 403 and "disabled" in r.json()["detail"]
    assert calls == []

    # token set, wrong/absent header -> 403; correct header -> passes the gate (200).
    monkeypatch.setenv("ALERT_API_TOKEN", "s3cret")
    assert client.post("/alert/run", params={"dry_run": False}).status_code == 403
    assert client.post("/alert/run", params={"dry_run": False},
                       headers={"X-Alert-Token": "wrong"}).status_code == 403
    assert calls == []
    r = client.post("/alert/run", params={"dry_run": False},
                    headers={"X-Alert-Token": "s3cret"})
    assert r.status_code == 200
    assert calls == [False]  # gate passed, run_once invoked with dry_run=False


def test_model_version(client, art):
    r = client.get("/model/version")
    assert r.status_code == 200
    assert r.json()["version"] == art.version
    assert all(a["passed"] for a in r.json()["validation_anchors"])


def test_predict_with_inline_snapshot(client):
    snap = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "ha_snapshot.json").read_text())
    r = client.post("/predict", json=snap)
    assert r.status_code == 200
    body = r.json()
    assert "freeboard_ft" in body and "scenarios" in body
    assert len(body["scenarios"]) == 3
    assert 0.0 <= body["p_cross_crest"] <= 1.0
    # monotone band
    peaks = {s["name"]: s["peak_elevation"] for s in body["scenarios"]}
    assert peaks["low"] <= peaks["median"] <= peaks["high"]


def test_predict_without_source_returns_503(client):
    r = client.post("/predict")  # no body, no HA creds configured
    assert r.status_code == 503


def test_presets_listed(client):
    keys = [p["key"] for p in client.get("/presets").json()]
    assert "step6_design" in keys and "moderate_storm" in keys


def test_simulate_step6_preset_peaks_near_anchor(client):
    r = client.post("/simulate", json={
        "current_elevation_abs_ft": 338.8, "stop_log_count": 0,
        "initial_sm_in": 4.5, "month": 1, "band": True,
        "storm": {"preset": "step6_design", "horizon_h": 72},
    })
    assert r.status_code == 200
    peaks = {s["name"]: s["peak_elevation"] for s in r.json()["scenarios"]}
    assert 342.6 < peaks["median"] < 343.7      # ~343.1 design anchor
    assert peaks["high"] >= peaks["median"]


def test_simulate_custom_storm(client):
    r = client.post("/simulate", json={
        "current_elevation_abs_ft": 339.675, "stop_log_count": 3, "month": 7,
        "storm": {"rate_in_per_hr": 0.2, "duration_h": 12, "horizon_h": 48},
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["scenarios"][0]["trajectory"]) == 48
    rf = body["rainfall"]
    assert rf["total_in"] == pytest.approx(2.4, abs=0.01)   # 0.2 * 12
    assert rf["peak_hour"] == 1                              # constant rate -> first hour
    assert len(rf["median_hourly_in"]) == 48
    assert rf["scenario_totals_in"]["high"] >= rf["scenario_totals_in"]["median"]


def test_simulate_storm_offset_shifts_rain_and_lowers_confidence(client):
    base = {"current_elevation_abs_ft": 339.675, "stop_log_count": 3, "month": 1, "band": True}
    now = client.post("/simulate", json={**base,
        "storm": {"preset": "moderate_storm", "start_offset_h": 0, "horizon_h": 72}}).json()["rainfall"]
    later = client.post("/simulate", json={**base,
        "storm": {"preset": "moderate_storm", "start_offset_h": 24, "horizon_h": 72}}).json()["rainfall"]

    assert later["peak_hour"] == now["peak_hour"] + 24          # rain delayed 24 h
    assert later["storm_start_h"] == 24
    # day-1 winter is high confidence; a day-2 storm is lower and its band is wider
    assert now["confidence_pct"] >= 80
    assert later["confidence_pct"] < now["confidence_pct"]
    assert later["band_widen_at_storm"] > now["band_widen_at_storm"] > 1.0
    # rainfall range present and ordered, full horizon length
    assert len(later["low_hourly_in"]) == 72 and len(later["high_hourly_in"]) == 72
    assert sum(later["high_hourly_in"]) >= sum(later["median_hourly_in"]) >= sum(later["low_hourly_in"])


def test_overtopping_risk_smooth_and_leans_wet_when_uncertain(client):
    base = {"current_elevation_abs_ft": 340.0, "stop_log_count": 3, "month": 1,
            "initial_sm_in": 4.5, "band": True}
    near = client.post("/simulate", json={**base,
        "storm": {"preset": "moderate_storm", "start_offset_h": 0, "horizon_h": 168}}).json()
    far = client.post("/simulate", json={**base,
        "storm": {"preset": "moderate_storm", "start_offset_h": 120, "horizon_h": 168}}).json()

    # a lower threshold is never less likely than a higher one (early warn >= dam crest >= bridge)
    assert near["p_cross_341"] >= near["p_cross_crest"] >= near["p_cross_bridge_deck"]
    assert far["p_cross_341"] >= far["p_cross_crest"] >= far["p_cross_bridge_deck"]
    # the bridge-deck threshold is emitted as a distinct prediction level
    assert {"bridge_deck", "dam_crest", "early_warning"} == {
        t["label"] for t in far["threshold_probabilities"]}
    # the far (more-uncertain) storm surfaces overtopping risk the near one does not
    assert far["p_cross_crest"] > near["p_cross_crest"]
    # smooth: at least one value escapes the old quarter-step buckets
    assert any(v not in (0.0, 0.25, 0.5, 0.75, 1.0)
               for v in (near["p_cross_341"], far["p_cross_crest"]))


def test_historical_catalog_endpoint(client):
    cat = client.get("/historical").json()
    assert 8 <= len(cat) <= 20                                # curated, near Woodinville
    assert cat[0]["total_in"] >= cat[-1]["total_in"]          # severity-sorted
    assert all(c["region"] == 31 and c["distance_mi"] <= 40 for c in cat)
    assert {"id", "station", "date", "storm_type", "total_in", "duration_h"} <= cat[0].keys()


def test_simulate_historical_storm(client):
    cat = client.get("/historical").json()
    worst = cat[0]                                            # Seattle RG12 2007, 7.56 in / 72 h
    r = client.post("/simulate", json={
        "current_elevation_abs_ft": 339.675, "stop_log_count": 3, "month": 1,
        "initial_sm_in": 4.5, "band": True,
        "storm": {"historical_id": worst["id"], "horizon_h": 72}}).json()
    # the median rainfall driving it matches the catalog total for that storm
    assert abs(r["rainfall"]["total_in"] - worst["total_in"]) < 0.05
    assert len(r["scenarios"]) == 3


def test_simulate_unknown_historical_400(client):
    r = client.post("/simulate", json={
        "current_elevation_abs_ft": 339.0, "storm": {"historical_id": "nope"}})
    assert r.status_code == 400


def test_config_exposes_seasonal_defaults(client, art):
    cfg = client.get("/config").json()
    assert cfg["lzsn_in"] == art.hspf.LZSN_in
    assert len(cfg["seasonal_sm_default_in"]) == 12
    # winter wetter than late summer
    assert cfg["seasonal_sm_default_in"]["1"] > cfg["seasonal_sm_default_in"]["8"]
    assert cfg["control_elev_ft"]["3"] == 339.675


def test_simulate_unknown_preset_400(client):
    r = client.post("/simulate", json={
        "current_elevation_abs_ft": 339.0, "storm": {"preset": "nope"}})
    assert r.status_code == 400


def test_simulate_response_has_factors(client):
    """The /simulate response includes a factors dict with the expected top-level keys."""
    r = client.post("/simulate", json={
        "current_elevation_abs_ft": 339.5, "stop_log_count": 3, "month": 4,
        "initial_sm_in": 3.0,
        "storm": {"rate_in_per_hr": 0.2, "duration_h": 6, "horizon_h": 24},
    })
    assert r.status_code == 200
    body = r.json()
    assert "factors" in body, "factors key missing from /simulate response"
    fb = body["factors"]
    assert fb is not None, "factors is null"
    required_keys = {"valid_at", "per_hour_ft", "cumulative_ft", "net_ft",
                     "net_cumulative_ft", "state", "totals_ft"}
    assert required_keys <= set(fb.keys()), f"Missing keys: {required_keys - set(fb.keys())}"
    # Arrays have one entry per forecast hour (horizon_h=24)
    assert len(fb["valid_at"]) == 24
    assert len(fb["net_ft"]) == 24
    # Per-component arrays present and same length
    assert len(fb["per_hour_ft"]["watershed_runoff"]) == 24
    assert len(fb["per_hour_ft"]["direct_rain"]) == 24
    assert len(fb["per_hour_ft"]["spillway"]) == 24
    assert len(fb["cumulative_ft"]["watershed_runoff"]) == 24
    # State arrays present
    assert len(fb["state"]["soil_moisture_in"]) == 24
    assert len(fb["state"]["soil_saturation_pct"]) == 24
    # Totals dict present
    totals_keys = {"watershed_runoff", "baseflow", "direct_rain", "spillway", "net"}
    assert totals_keys <= set(fb["totals_ft"].keys())


def test_root_serves_the_field_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>Crystal Lake</title>" in r.text


def test_root_with_mode_backtest_redirects_to_the_dashboard(client):
    r = client.get("/?mode=backtest", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "dashboard?mode=backtest"


def test_root_with_mode_live_serves_the_field_page(client):
    r = client.get("/?mode=live")
    assert r.status_code == 200 and "<title>Crystal Lake</title>" in r.text


def test_dashboard_serves_the_old_page(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "lake-rise simulator" in r.text


# --- /live/predict tests ---------------------------------------------------------

def _make_fake_conditions(art) -> LiveConditions:
    """A plausible LiveConditions for unit-testing /live/predict without real HA."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    # 20d older block + 10d recent -> 720 hours trailing
    older = [0.01] * (20 * 24)
    recent = [0.0] * (10 * 24)
    recent[5] = 0.15  # a small rain event
    trailing = older + recent
    return LiveConditions(
        reading_ft=1.80,
        stop_log_count=3,
        as_of=now.isoformat(),
        rate_in_per_hr=0.0,
        today_in=0.0,
        week_in=0.10,
        month_in=0.34,
        event_in=0.0,
        older_block_in=0.10,
        trailing_rainfall_in=trailing,
        forecast_point_in=[0.05, 0.10, 0.0, 0.02] + [0.0] * 68,
        forecast_pop_frac=[0.8, 0.7, 0.2, 0.3] + [0.0] * 68,
        has_gaps=False,
    )


class _FakeSource:
    """Stand-in for LiveHASource: ignores (art, cfg), returns known conditions."""
    def __init__(self, art, cfg):
        self._art = art

    def fetch_conditions(self) -> LiveConditions:
        return _make_fake_conditions(self._art)

    def with_archive_spinup(self, bundle, recent_rain):
        return bundle                       # no archive in the fake -> keep the hindcast

    run: dict | None = None                 # the archived ensemble run, if any

    def ensemble_run(self) -> dict | None:
        return self.run


class _FakeShared:
    """Stand-in for ``live_source(...)``: ``build_bundle`` returns the bundle the fake
    conditions imply, and counts the calls."""
    calls = 0
    run: dict | None = None

    def __init__(self, art, cfg=None):
        self._art = art

    def build_bundle(self):
        from lake_rise.scenarios import ensemble_members, synthesize_scenarios
        type(self).calls += 1
        c = _make_fake_conditions(self._art)
        series = list(c.forecast_point_in)[:72]
        members = []
        if self.run is not None:
            members = ensemble_members(self._art, self.run, datetime.fromisoformat(c.as_of),
                                       72, shape_in=series)
        return InputBundle(
            as_of=c.as_of,
            current_elevation_abs_ft=c.reading_ft + self._art.datum.sensor_to_absolute_offset_ft,
            stop_log_count=c.stop_log_count,
            trailing_rainfall_in=c.trailing_rainfall_in,
            forecast_scenarios=synthesize_scenarios(
                self._art, series, month=datetime.fromisoformat(c.as_of).month,
                pop_frac=c.forecast_pop_frac),
            ensemble_members=members,
            ensemble_cycle=self.run["cycle"] if members else None,
            rainfall_has_gaps=c.has_gaps,
        )


def _patch_live(monkeypatch, shared=_FakeShared, source=_FakeSource):
    monkeypatch.setattr("lake_rise.api.ha_config_from_env",
                        lambda: HAConfig(base_url="http://test", token="x"))
    monkeypatch.setattr("lake_rise.api.LiveHASource", source)
    monkeypatch.setattr("lake_rise.api.live_source", shared)
    shared.calls = 0


def test_live_predict_without_a_storm_uses_the_shared_builder(monkeypatch, art):
    _patch_live(monkeypatch)
    assert TestClient(create_app(art)).post("/live/predict", json={}).status_code == 200
    assert _FakeShared.calls == 1


def test_live_predict_with_another_horizon_keeps_its_own_path(monkeypatch, art):
    from lake_rise.alerting import alert_config_from_env
    _patch_live(monkeypatch)
    other = alert_config_from_env().horizon_hours + 24
    r = TestClient(create_app(art)).post("/live/predict", json={"horizon_h": min(other, 168)
                                         if other <= 168 else 24})
    assert r.status_code == 200
    assert _FakeShared.calls == 0


def test_live_predict_shared_pull_failure_is_502(monkeypatch, art):
    class Broken(_FakeShared):
        def build_bundle(self):
            raise RuntimeError("HA down")
    _patch_live(monkeypatch, shared=Broken)
    r = TestClient(create_app(art)).post("/live/predict", json={})
    assert r.status_code == 502 and "HA pull failed" in r.json()["detail"]


def test_live_predict_with_a_what_if_storm_keeps_its_own_path(monkeypatch, art):
    _patch_live(monkeypatch)
    r = TestClient(create_app(art)).post(
        "/live/predict", json={"storm": {"preset": "step6_design", "horizon_h": 72}})
    assert r.status_code == 200
    assert _FakeShared.calls == 0


def test_live_predict_no_ha_env_503(client):
    # No HA_URL/HA_TOKEN in the test environment -> 503.
    r = client.post("/live/predict", json={})
    assert r.status_code == 503


def test_live_predict_returns_current_past(monkeypatch, art):
    monkeypatch.setattr("lake_rise.api.ha_config_from_env",
                        lambda: HAConfig(base_url="http://test", token="x"))
    monkeypatch.setattr("lake_rise.api.LiveHASource", _FakeSource)
    monkeypatch.setattr("lake_rise.api.live_source", _FakeShared)

    test_client = TestClient(create_app(art))
    r = test_client.post("/live/predict", json={})
    assert r.status_code == 200
    body = r.json()

    # Top-level keys = /simulate keys + current + past
    assert "scenarios" in body and "rainfall" in body
    assert "freeboard_ft" in body and "p_cross_crest" in body
    assert "current" in body and "past" in body

    current = body["current"]
    assert "current_elevation_abs_ft" in current
    assert "stop_log_count" in current
    assert "rain_rate_in_per_hr" in current
    assert "rain_today_in" in current
    assert "rain_week_in" in current
    assert "rain_month_in" in current
    assert "rain_event_in" in current
    assert "as_of" in current
    assert "data_fresh" in current
    assert current["data_fresh"] is True
    assert current["forecast_source"] == "Apple WeatherKit (live)"

    past = body["past"]
    assert "window_days" in past
    assert "total_in" in past
    assert "older_block_in" in past
    assert "sm_in" in past
    assert "s_if_in" in past
    # sm should be positive (hindcast through ~30d of light rain seeds SM)
    assert past["sm_in"] >= 0.0
    assert past["s_if_in"] >= 0.0


# --- /backtest tests ---------------------------------------------------------

def test_backtest_no_ha_env_503(client):
    """No HA creds in test environment -> 503."""
    r = client.post("/backtest", json={})
    assert r.status_code == 503


def test_backtest_returns_predicted_actual(monkeypatch, art):
    """Monkeypatched LiveHASource returns a known dict; endpoint passes it through."""
    known_result = {
        "t0": "2026-04-01T00:00:00+00:00",
        "now": "2026-04-01T06:00:00+00:00",
        "hours": 6,
        "predicted": [
            {"valid_at": "2026-04-01T00:00:00+00:00", "elevation": 339.5},
            {"valid_at": "2026-04-01T01:00:00+00:00", "elevation": 339.51},
        ],
        "actual": [
            {"valid_at": "2026-04-01T00:00:00+00:00", "elevation": 339.5},
            {"valid_at": "2026-04-01T01:00:00+00:00", "elevation": 339.52},
        ],
        "rainfall_in": [0.0] * 6,
        "rain_total_in": 0.0,
        "metrics": {
            "rmse_ft": 0.01,
            "mae_ft": 0.01,
            "max_err_ft": 0.01,
            "final_err_ft": -0.01,
            "pred_peak_elev_ft": 339.51,
            "pred_peak_time": "2026-04-01T01:00:00+00:00",
            "actual_peak_elev_ft": 339.52,
            "actual_peak_time": "2026-04-01T01:00:00+00:00",
            "peak_err_ft": -0.01,
            "peak_timing_err_h": 0.0,
            "peak_within_target": True,
            "timing_within_target": True,
        },
        "stop_log_count": 3,
        "data_fresh": True,
    }

    class _FakeBacktestSource:
        def __init__(self, art, cfg):
            pass

        def fetch_backtest(self, hours_back: int, stop_log_count: int | None = None) -> dict:
            return known_result

    monkeypatch.setattr("lake_rise.api.ha_config_from_env",
                        lambda: HAConfig(base_url="http://test", token="x"))
    monkeypatch.setattr("lake_rise.api.LiveHASource", _FakeBacktestSource)

    test_client = TestClient(create_app(art))
    r = test_client.post("/backtest", json={"hours_back": 6})
    assert r.status_code == 200
    body = r.json()

    assert "predicted" in body and "actual" in body
    assert "t0" in body and "now" in body and "hours" in body
    assert "metrics" in body
    assert body["predicted"][0]["elevation"] == pytest.approx(339.5)


def test_backtest_clamps_hours(monkeypatch, art):
    """hours_back is clamped to [6, 240]."""
    received = {}

    class _FakeSource:
        def __init__(self, art, cfg):
            pass

        def fetch_backtest(self, hours_back: int, stop_log_count: int | None = None) -> dict:
            received["hours_back"] = hours_back
            return {
                "t0": "2026-04-01T00:00:00+00:00",
                "now": "2026-04-01T06:00:00+00:00",
                "hours": hours_back,
                "predicted": [],
                "actual": [],
                "rainfall_in": [],
                "rain_total_in": 0.0,
                "metrics": {},
                "stop_log_count": 0,
                "data_fresh": True,
            }

    monkeypatch.setattr("lake_rise.api.ha_config_from_env",
                        lambda: HAConfig(base_url="http://test", token="x"))
    monkeypatch.setattr("lake_rise.api.LiveHASource", _FakeSource)

    tc = TestClient(create_app(art))

    # Under-minimum -> clamped to 6
    tc.post("/backtest", json={"hours_back": 1})
    assert received["hours_back"] == 6

    # Over-maximum -> clamped to 240
    tc.post("/backtest", json={"hours_back": 9999})
    assert received["hours_back"] == 240

    # In range -> unchanged
    tc.post("/backtest", json={"hours_back": 48})
    assert received["hours_back"] == 48


def test_config_exposes_backtest_max_hours(client):
    cfg = client.get("/config").json()
    assert "backtest_max_hours" in cfg
    assert cfg["backtest_max_hours"] == 240


def test_live_predict_stop_log_override(monkeypatch, art):
    """An explicit stop_log_count overrides the live/seasonal default (3 in the fake)."""
    monkeypatch.setattr("lake_rise.api.ha_config_from_env",
                        lambda: HAConfig(base_url="http://test", token="x"))
    monkeypatch.setattr("lake_rise.api.LiveHASource", _FakeSource)
    monkeypatch.setattr("lake_rise.api.live_source", _FakeShared)
    tc = TestClient(create_app(art))
    assert tc.post("/live/predict", json={}).json()["current"]["stop_log_count"] == 3
    assert tc.post("/live/predict", json={"stop_log_count": 0}).json()["current"]["stop_log_count"] == 0


def test_live_predict_what_if_override(monkeypatch, art):
    monkeypatch.setattr("lake_rise.api.ha_config_from_env",
                        lambda: HAConfig(base_url="http://test", token="x"))
    monkeypatch.setattr("lake_rise.api.LiveHASource", _FakeSource)
    monkeypatch.setattr("lake_rise.api.live_source", _FakeShared)

    test_client = TestClient(create_app(art))

    # Without what-if: live WeatherKit source, small forecast
    live_r = test_client.post("/live/predict", json={}).json()
    # With what-if: a heavy preset storm
    storm_r = test_client.post("/live/predict", json={
        "storm": {"preset": "step6_design", "horizon_h": 72}
    }).json()

    assert storm_r["current"]["forecast_source"].startswith("what-if:")
    assert live_r["current"]["forecast_source"] == "Apple WeatherKit (live)"
    # The what-if storm should produce much more median rainfall
    assert storm_r["rainfall"]["total_in"] > live_r["rainfall"]["total_in"]
    # Both have the standard /simulate-equivalent keys
    assert len(storm_r["scenarios"]) == 3
    assert storm_r["rainfall"]["scenario_totals_in"]["high"] >= storm_r["rainfall"]["scenario_totals_in"]["median"]


def test_calibration_approve_requires_proposal_token(monkeypatch, art, tmp_path):
    from lake_rise.alerting.config import SMTPConfig
    from lake_rise.calibration.config import CalibrationConfig
    from lake_rise.calibration.state import (
        CalibrationState, Candidate, ProposedParam, load_state, new_token, now_iso, save_state,
    )

    cfg = CalibrationConfig(
        enabled=True, recipient=None, bfi_target=0.67, min_recession_days=5,
        startup_backfill_days=400,
        state_path=tmp_path / "state.json", versions_path=tmp_path / "versions",
        api_token="gate-token", ui_base_url=None, template_path=None,
        smtp=SMTPConfig(host="", port=587, user=None, password=None, sender="", starttls=True),
    )
    monkeypatch.setenv("CALIB_STATE_PATH", str(cfg.state_path))
    monkeypatch.setenv("CALIB_VERSIONS_PATH", str(cfg.versions_path))
    monkeypatch.setenv("CALIB_API_TOKEN", "gate-token")

    proposal_token = new_token()
    cand = Candidate(
        id="c1", created_at=now_iso(), base_version="v0",
        params=[ProposedParam(
            param="hspf.AGWRC_per_day", current=art.hspf.AGWRC_per_day,
            proposed=0.95, confidence="firm",
        )],
        anchors_pass=True, veto={"passed": True, "reason": "test"}, token=proposal_token,
    )
    save_state(CalibrationState(pending=cand), cfg.state_path)
    client = TestClient(create_app(art))

    bad = client.post(
        f"/calibration/approve?candidate_id={cand.id}&token=wrong-token",
        headers={"X-Calib-Token": "gate-token"},
    )
    assert bad.status_code == 409

    good = client.post(
        f"/calibration/approve?candidate_id={cand.id}&token={proposal_token}",
        headers={"X-Calib-Token": "gate-token"},
    )
    assert good.status_code == 200
    assert good.json()["active_version"] == "v1"
    assert load_state(cfg.state_path).pending is None


def test_live_predict_uses_the_ensemble_when_a_run_is_archived(monkeypatch, art):
    from datetime import datetime, timedelta

    as_of = datetime.fromisoformat(_make_fake_conditions(art).as_of)
    cycle = as_of - timedelta(hours=12)
    run = {"cycle": cycle.isoformat(),
           "periods": [{"lead_h": 24 * d, "hours": 24, "members_in": [0.1 * m for m in range(50)]}
                       for d in range(4)]}
    monkeypatch.setattr("lake_rise.api.ha_config_from_env",
                        lambda: HAConfig(base_url="http://test", token="x"))
    monkeypatch.setattr("lake_rise.api.LiveHASource",
                        type("WithRun", (_FakeSource,), {"run": run}))
    monkeypatch.setattr("lake_rise.api.live_source",
                        type("WithRun", (_FakeShared,), {"run": run}))
    body = TestClient(create_app(art)).post("/live/predict", json={}).json()
    assert body["input_summary"]["rain_source"] == "ensemble"
    assert body["current"]["forecast_source"].startswith("ECMWF ensemble")
    rain = body["rainfall"]
    assert rain["scenario_totals_in"]["low"] < rain["scenario_totals_in"]["high"]
    assert rain["total_in"] == rain["scenario_totals_in"]["median"]


# --- /field tests -----------------------------------------------------------------

class _FieldSource:
    """Stand-in for ``live_source(...)`` on /field: ``build_bundle`` returns ``bundle`` (or
    raises when it is an exception) and counts the calls; the gauge reads are fixed."""
    calls = 0
    bundle = None
    gauge_ft = 0.62

    def __init__(self, art, cfg=None):
        pass

    def build_bundle(self):
        type(self).calls += 1
        if isinstance(self.bundle, Exception):
            raise self.bundle
        return self.bundle

    def fetch_gauge_observation(self, now):
        from lake_rise.observed import GaugeObservation
        return GaugeObservation(detected_at=now, gauge_ft=self.gauge_ft, confirmed=True,
                                sample_count=15)

    trends = (0.05, 0.05)    # (now, an hour ago)

    def lake_trend_ft_per_h(self, now):
        """The trend now, or the one an hour ago when ``now`` is over half an hour old."""
        from datetime import datetime, timedelta, timezone
        recent = datetime.now(timezone.utc) - now < timedelta(minutes=30)
        return self.trends[0 if recent else 1]

    def gauge_problem(self, now):
        return None

    def gauge_age_minutes(self, now):
        return 2

    rain_rate = 0.12

    def rain_rate_in_per_h(self, now):
        return self.rain_rate


def _spread_bundle():
    from test_ensemble_rain import _bundle, _storm
    return _bundle([_storm(0.2 * j) for j in range(50)])


def _no_forecast_bundle(elev_abs_ft):
    from lake_rise.bundle import ScenarioRain
    from test_ensemble_rain import _bundle
    return _bundle([]).model_copy(update={
        "current_elevation_abs_ft": elev_abs_ft,
        "forecast_scenarios": [ScenarioRain(name=n, hourly_in=[])
                               for n in ("low", "median", "high")]})


@pytest.fixture
def field(monkeypatch, art):
    """A /field client on a fake live source, with a clock the test advances."""
    clock = {"t": 1000.0}
    monkeypatch.setattr("lake_rise.api.ha_config_from_env",
                        lambda: HAConfig(base_url="http://test", token="x"))
    monkeypatch.setattr("lake_rise.api.live_source", _FieldSource)
    monkeypatch.setattr("lake_rise.api._clock", lambda: clock["t"])
    _FieldSource.calls = 0
    _FieldSource.bundle = _spread_bundle()
    _FieldSource.gauge_ft = 0.62
    _FieldSource.trends = (0.05, 0.05)

    class F:
        client = TestClient(create_app(art))
        source = _FieldSource

        @staticmethod
        def advance(seconds):
            clock["t"] += seconds

        @staticmethod
        def get():
            return F.client.get("/field")

    return F


def test_field_returns_an_ensemble_summary_without_a_token(field):
    r = field.get()
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "ensemble"
    assert len(body["peaks_ft"]) == 50
    assert body["lake"]["trend"] == "rising"
    assert body["stale_since"] is None and body["unavailable"] is None


def test_field_reports_the_trend_change_against_an_hour_ago(field):
    field.source.trends = (0.07, 0.04)
    assert field.get().json()["trend_change"] == "faster"


def test_field_reuses_its_result_within_120_seconds(field):
    first = field.get().json()
    field.advance(119)
    assert field.get().json() == first
    assert field.source.calls == 1


def test_field_rebuilds_after_120_seconds(field):
    field.get()
    field.advance(121)
    assert field.get().status_code == 200
    assert field.source.calls == 2


def test_a_failure_is_not_cached(field):
    field.source.bundle = RuntimeError("HA down")
    assert field.get().status_code == 502
    field.advance(31)
    field.source.bundle = _spread_bundle()
    assert field.get().status_code == 200
    assert field.source.calls == 2


def test_after_a_failure_field_waits_30_seconds_before_calling_ha_again(field):
    field.source.bundle = RuntimeError("HA down")
    assert field.get().status_code == 502
    field.advance(10)
    assert field.get().status_code == 502
    assert field.source.calls == 1
    field.advance(21)
    field.source.bundle = _spread_bundle()
    assert field.get().status_code == 200
    assert field.source.calls == 2


def test_while_waiting_to_retry_field_serves_the_last_good_result_marked_stale(field):
    good = field.get().json()
    field.advance(121)
    field.source.bundle = httpx.ConnectError("HA down")
    field.get()
    field.advance(10)
    body = field.get().json()
    assert body["unavailable"] == "Can't reach the gauge"
    assert body["stale_since"] == good["computed_at"]
    assert field.source.calls == 2


def test_failure_after_a_success_serves_the_previous_result_marked_stale(field):
    good = field.get().json()
    field.advance(121)
    field.source.bundle = httpx.ConnectError("HA down")
    r = field.get()
    assert r.status_code == 200
    body = r.json()
    assert body["stale_since"] == good["computed_at"]
    assert body["unavailable"] == "Can't reach the gauge"
    assert body["peaks_ft"] == good["peaks_ft"]
    field.advance(31)
    field.source.bundle = _spread_bundle()
    assert field.get().json()["unavailable"] is None    # the stale answer wasn't cached
    assert field.source.calls == 3


def test_failure_with_no_previous_result_is_502(field):
    field.source.bundle = RuntimeError("HA down")
    r = field.get()
    assert r.status_code == 502
    assert r.json() == {"detail": "Can't reach the gauge"}


def test_ha_unconfigured_is_503(art):
    r = TestClient(create_app(art)).get("/field")
    assert r.status_code == 503
    assert r.json() == {"detail": "No live HA source configured"}


def test_an_empty_forecast_keeps_the_previous_forecast_marked_stale_with_the_new_lake(field):
    good = field.get().json()
    assert good["status"]["rank"] > 0 and good["status"]["observed_eap"] is None
    field.advance(121)
    field.source.bundle = _no_forecast_bundle(339.625)   # 1.25 ft on the stick
    field.source.gauge_ft = 3.5                          # Plastic, seen on the gauge now
    body = field.get().json()
    assert body["unavailable"] == "No new forecast"
    assert body["stale_since"] == good["computed_at"]
    for part in ("mode", "peaks_ft", "peak_levels", "cases", "timeline", "rain_6h",
                 "ensemble_cycle"):
        assert body[part] == good[part]
    status = body["status"]
    assert [status[k] for k in ("level", "rank", "headline")] == [
        good["status"][k] for k in ("level", "rank", "headline")]
    assert status["observed_eap"]["gauge_ft"] == 3.5
    assert [lv["pct"] for lv in body["levels"]] == [lv["pct"] for lv in good["levels"]]
    assert body["lake"]["now_ft"] == 1.25 != good["lake"]["now_ft"]
    # The merged answer doesn't become the last good one: a later failure still dates to it.
    field.advance(121)
    field.source.bundle = RuntimeError("HA down")
    assert field.get().json()["stale_since"] == good["computed_at"]


def test_ha_down_clears_the_rain_rate_but_keeps_the_forecast_rain(field):
    good = field.get().json()
    assert good["lake"]["rain_rate_in_per_h"] == 0.12
    field.advance(121)
    field.source.bundle = httpx.ConnectError("HA down")
    body = field.get().json()
    assert body["lake"]["rain_rate_in_per_h"] is None
    assert body["rain_total_in"] == good["rain_total_in"]


def test_no_forecast_merge_keeps_the_last_good_forecast_rain(field):
    good = field.get().json()
    assert good["rain_total_in"]
    field.advance(121)
    field.source.bundle = _no_forecast_bundle(339.625)
    body = field.get().json()
    assert body["unavailable"] == "No new forecast"
    assert body["rain_total_in"] == good["rain_total_in"]
    assert body["lake"]["rain_rate_in_per_h"] == 0.12


def test_no_forecast_merge_keeps_the_last_good_advice(field):
    good = field.get().json()
    assert good["actions"] and good["stop_logs"] and good["next_level"]
    field.advance(121)
    field.source.bundle = _no_forecast_bundle(339.625)   # 0.63 ft on the stick
    body = field.get().json()
    assert body["unavailable"] == "No new forecast"
    for part in ("actions", "next_level", "stop_logs"):
        assert body[part] == good[part]


def test_no_forecast_merge_takes_the_advice_of_a_newly_observed_eap_level(field):
    from test_ensemble_rain import _bundle
    field.source.bundle = _bundle([[0.0] * 72] * 50)     # a quiet forecast
    good = field.get().json()
    assert good["actions"] == [] and good["status"]["observed_eap"] is None
    field.advance(121)
    field.source.bundle = _no_forecast_bundle(339.625)
    field.source.gauge_ft = 3.5                           # Level 1, seen on the gauge now
    body = field.get().json()
    assert body["unavailable"] == "No new forecast"
    assert body["status"]["observed_eap"]["gauge_ft"] == 3.5
    assert [a["id"] for a in body["actions"]][:1] == ["pull_logs"]


def test_no_forecast_merge_drops_stop_log_options_for_another_count(field):
    good = field.get().json()
    assert good["stop_logs"]["logs_in"] == 3
    field.advance(121)
    field.source.bundle = _no_forecast_bundle(339.625).model_copy(update={"stop_log_count": 2})
    body = field.get().json()
    assert body["unavailable"] == "No new forecast"
    assert body["stop_logs"] is None


def test_field_details_carry_the_gauge_age(field):
    assert field.get().json()["details"]["gauge_age_min"] == 2


def test_ha_down_clears_the_gauge_age_in_details(field):
    field.get()
    field.advance(121)
    field.source.bundle = httpx.ConnectError("HA down")
    assert field.get().json()["details"]["gauge_age_min"] is None
    field.advance(10)    # the hold-off path too
    assert field.get().json()["details"]["gauge_age_min"] is None


def test_no_forecast_merge_keeps_live_data_rows_current(field, monkeypatch):
    good = field.get().json()["details"]
    field.advance(121)
    field.source.bundle = _no_forecast_bundle(339.625).model_copy(update={
        "trailing_rainfall_in": [0.05] * 24, "stop_log_count": 2,
        "stop_log_count_is_default": not good["stop_log_default"], "initial_sm_in": 0.0})
    monkeypatch.setattr(field.source, "gauge_age_minutes", lambda self, now: 9, raising=False)
    d = field.get().json()["details"]
    assert d["gauge_age_min"] == 9
    assert d["rain_gauge_24h_in"] == 1.2 != good["rain_gauge_24h_in"]
    assert d["stop_log_count"] == 2 != good["stop_log_count"]
    assert d["stop_log_default"] is not good["stop_log_default"]
    assert d["soil_pct"] != good["soil_pct"]
    for k in ("factors", "net_ft", "factors_window", "factors_until", "rain_days", "cases", "rain_source",
              "agreement_pct", "agreement_label", "agreement_tol_ft"):
        assert d[k] == good[k]
    assert d["factors"] and d["soil_pct"] is not None


def test_a_new_artifact_version_doesnt_reuse_the_cache(monkeypatch, art):
    """An unpinned app follows the calibration active version; a version change keys a new
    cache entry and a new last-good result."""
    from types import SimpleNamespace

    active = {"version": "v0"}
    monkeypatch.setattr("lake_rise.calibration.state.load_state",
                        lambda path: SimpleNamespace(active_version=active["version"]))
    monkeypatch.setattr(
        "lake_rise.calibration.service.active_artifact_and_version",
        lambda: (art.model_copy(update={"version": active["version"]}), active["version"]))
    monkeypatch.setattr("lake_rise.api.ha_config_from_env",
                        lambda: HAConfig(base_url="http://test", token="x"))
    monkeypatch.setattr("lake_rise.api.live_source", _FieldSource)
    monkeypatch.setattr("lake_rise.api._clock", lambda: 1000.0)
    _FieldSource.calls = 0
    _FieldSource.bundle = _spread_bundle()
    client = TestClient(create_app())

    assert client.get("/field").status_code == 200
    active["version"] = "v1"
    assert client.get("/field").status_code == 200
    assert _FieldSource.calls == 2
    # v1 has no last-good result of its own, so a failure now is a 502, not v0's answer.
    active["version"] = "v2"
    _FieldSource.bundle = RuntimeError("HA down")
    assert client.get("/field").status_code == 502


def test_an_empty_forecast_marks_a_level_the_lake_has_just_crossed_reached(field, art):
    good = field.get().json()
    warning = next(lv for lv in good["levels"] if lv["key"] == "early_warning")
    assert warning["reached"] is False
    field.advance(121)
    field.source.bundle = _no_forecast_bundle(art.thresholds_abs_ft.early_warning + 0.1)
    body = field.get().json()
    assert body["unavailable"] == "No new forecast"
    levels = {lv["key"]: lv for lv in body["levels"]}
    assert levels["early_warning"]["reached"] is True
    assert levels["early_warning"]["pct"] == warning["pct"]     # the chances stay last good's


# --- /field: change against the log, stop-log options ------------------------------

def _wet_bundle():
    """Heavy rain on a 3-log lake: levels are in play, so the view is not 'normal'."""
    from test_ensemble_rain import _bundle, _storm
    return _bundle([_storm(1.0 + 0.2 * j) for j in range(50)]).model_copy(
        update={"current_elevation_abs_ft": 341.0})


def test_field_includes_stop_log_options_when_pulling_changes_a_percentage(field):
    field.source.bundle = _wet_bundle()
    body = field.get().json()
    stop_logs = body["stop_logs"]
    assert stop_logs is not None and stop_logs["logs_in"] == 3
    assert [o["logs_in"] for o in stop_logs["options"]] == [3, 2, 1, 0]


def test_field_includes_a_change_against_a_logged_record(field, art):
    from datetime import datetime, timedelta, timezone

    from lake_rise import forecast_log
    from lake_rise.predict import predict
    from lake_rise.field import level_pcts

    field.source.bundle = _wet_bundle()
    first = field.get().json()
    field.advance(121)
    result = predict(field.source.bundle, art)
    pcts = level_pcts(result, field.source.bundle, art)
    moved = {k: (v or 0) + 20 for k, v in pcts.items()}
    off = art.datum.sensor_to_absolute_offset_ft
    forecast_log.append({
        "generated_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "stop_log_count": field.source.bundle.stop_log_count,
        "rain_source": result.input_summary["rain_source"],
        "levels_pct": moved,
        "middle_peak_ft": first["cases"]["middle"] + off,
    })
    body = field.get().json()
    assert body["change"] is not None
    assert body["change"]["pct_was"]


def test_field_shows_the_change_at_55_past_against_the_hour_before(field, art):
    """At :50-:59 the log's window also holds this forecast hour's own record."""
    from datetime import datetime, timedelta, timezone

    from lake_rise import forecast_log
    from lake_rise.predict import predict

    bundle = field.source.bundle = _wet_bundle()
    result = predict(bundle, art)
    now = datetime.now(timezone.utc)
    record = {"stop_log_count": bundle.stop_log_count,
              "rain_source": result.input_summary["rain_source"],
              "middle_peak_ft": next(s for s in result.scenarios if s.name == "median").peak_elevation}
    forecast_log.append({**record, "generated_at": (now - timedelta(minutes=115)).isoformat(),
                         "as_of": (bundle.as_of - timedelta(hours=1)).isoformat(),
                         "levels_pct": {"overtop": 0}})
    forecast_log.append({**record, "generated_at": (now - timedelta(minutes=55)).isoformat(),
                         "as_of": bundle.as_of.isoformat(), "levels_pct": {"overtop": 0}})
    change = field.get().json()["change"]
    assert change is not None
    assert change["since"] == (now - timedelta(minutes=115)).isoformat().replace("+00:00", "Z")


def test_an_unreadable_forecast_log_does_not_fail_the_field_view(field, monkeypatch):
    def boom(at, **kw):
        raise OSError("disk gone")
    monkeypatch.setattr("lake_rise.api.forecast_log.previous", boom)
    r = field.get()
    assert r.status_code == 200
    assert r.json()["change"] is None
