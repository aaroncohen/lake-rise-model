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
    assert len(r.json()["scenarios"][0]["trajectory"]) == 48


def test_simulate_unknown_preset_400(client):
    r = client.post("/simulate", json={
        "current_elevation_abs_ft": 339.0, "storm": {"preset": "nope"}})
    assert r.status_code == 400


def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "lake-rise simulator" in r.text
