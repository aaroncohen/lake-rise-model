"""The stateless prediction API."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lake_rise.api import create_app


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


def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "lake-rise simulator" in r.text
