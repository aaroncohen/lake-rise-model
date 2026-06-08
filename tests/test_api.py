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
