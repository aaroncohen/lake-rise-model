"""The stateless prediction API."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lake_rise.api import create_app
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

    # a lower threshold is never less likely than a higher one
    assert near["p_cross_341"] >= near["p_cross_crest"]
    assert far["p_cross_341"] >= far["p_cross_crest"]
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


def test_index_page_served(client):
    r = client.get("/")
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


def test_live_predict_no_ha_env_503(client):
    # No HA_URL/HA_TOKEN in the test environment -> 503.
    r = client.post("/live/predict", json={})
    assert r.status_code == 503


def test_live_predict_returns_current_past(monkeypatch, art):
    monkeypatch.setattr("lake_rise.api.ha_config_from_env",
                        lambda: HAConfig(base_url="http://test", token="x"))
    monkeypatch.setattr("lake_rise.api.LiveHASource", _FakeSource)

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
    tc = TestClient(create_app(art))
    assert tc.post("/live/predict", json={}).json()["current"]["stop_log_count"] == 3
    assert tc.post("/live/predict", json={"stop_log_count": 0}).json()["current"]["stop_log_count"] == 0


def test_live_predict_what_if_override(monkeypatch, art):
    monkeypatch.setattr("lake_rise.api.ha_config_from_env",
                        lambda: HAConfig(base_url="http://test", token="x"))
    monkeypatch.setattr("lake_rise.api.LiveHASource", _FakeSource)

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
