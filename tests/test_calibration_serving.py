"""S2 regression: the calibration active version is actually *served*.

The train -> approve -> version loop must reach the serving layer. These tests prove the
resolver serves the active version and that a running API picks up an approved/reverted
pointer per-request (the previously-missing wire between calibration and serving).
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from lake_rise.api import create_app
from lake_rise.calibration import service
from lake_rise.calibration.service import active_artifact_and_version

# Reuse the calibration pipeline test helpers (plain functions, not fixtures).
from test_calibration import _cfg, _geometric_recession


def _promote_v1(cfg, art, tmp_path) -> str:
    """Train + approve a clean recession proposal to promote v1 (retunes AGWRC)."""
    rec = _geometric_recession(art, datetime(2026, 7, 1, tzinfo=timezone.utc), k_true=0.95)
    (tmp_path / "cont.json").write_text(rec.model_dump_json())
    cand = service.run_training(cfg, continuous_path=tmp_path / "cont.json",
                                storms_path=tmp_path / "none")
    return service.approve(cfg, cand.id, cand.token)


def test_active_artifact_and_version_reflects_promotion(art, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("CALIB_STATE_PATH", str(cfg.state_path))
    monkeypatch.setenv("CALIB_VERSIONS_PATH", str(cfg.versions_path))

    _, v = active_artifact_and_version()
    assert v == "v0"                                        # nothing promoted -> baseline

    assert _promote_v1(cfg, art, tmp_path) == "v1"
    served, v = active_artifact_and_version()
    assert v == "v1"
    assert served.hspf.AGWRC_per_day != art.hspf.AGWRC_per_day   # the retuned value is served


def test_api_serves_active_version_and_refreshes_on_revert(art, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("CALIB_STATE_PATH", str(cfg.state_path))
    monkeypatch.setenv("CALIB_VERSIONS_PATH", str(cfg.versions_path))
    _promote_v1(cfg, art, tmp_path)

    client = TestClient(create_app())                       # non-pinned: resolves active version
    assert client.get("/model/version").json()["version"] == "v1"

    # An out-of-band pointer flip (CLI-style revert) must be picked up by the running server.
    service.revert(cfg, "v0")
    assert client.get("/model/version").json()["version"] == "v0"
