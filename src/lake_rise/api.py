"""Stateless prediction API (spec 6). Loads the latest artifact, builds an input
bundle (inline snapshot or live HA pull), runs the pure predictor, and returns the
HA-shaped result. Notifications belong to Home Assistant; this only serves outputs.

Run: ``uvicorn lake_rise.api:app`` (config via env: HA_URL, HA_TOKEN, LAKE_RISE_ARTIFACT)."""

from __future__ import annotations

import logging

from fastapi import Body, FastAPI, HTTPException

from .artifact import Artifact, load_artifact
from .predict import PredictionResult, predict
from .settings import artifact_path_from_env, ha_config_from_env
from .sources.live_ha import LiveHASource
from .sources.snapshot import Snapshot, bundle_from_snapshot
from .validate import run_anchors

log = logging.getLogger("lake_rise.api")


def create_app(art: Artifact | None = None) -> FastAPI:
    art = art or load_artifact(artifact_path_from_env())
    # Anchor results are deterministic for a given artifact: compute once at startup.
    anchors = [a.__dict__ for a in run_anchors(art)]
    app = FastAPI(title="Crystal Lake lake-rise prediction", version=art.version)
    app.state.art = art
    app.state.anchors = anchors

    @app.get("/health")
    def health() -> dict:
        ha = ha_config_from_env()
        return {
            "status": "ok",
            "model_version": art.version,
            "live_source_configured": ha is not None,
            "anchors_pass": all(a["passed"] for a in anchors),
        }

    @app.get("/model/version")
    def model_version() -> dict:
        return {
            "version": art.version,
            "description": art.description,
            "validation_anchors": anchors,
            "validation_targets": art.validation_targets.model_dump(),
        }

    @app.post("/predict", response_model=PredictionResult)
    def do_predict(snapshot: Snapshot | None = Body(default=None)) -> PredictionResult:
        """Predict from an inline snapshot, or pull live HA data if none is supplied."""
        if snapshot is not None:
            bundle = bundle_from_snapshot(art, snapshot)
        else:
            ha = ha_config_from_env()
            if ha is None:
                raise HTTPException(
                    status_code=503,
                    detail="No snapshot supplied and no live HA source configured "
                           "(set HA_URL and HA_TOKEN).",
                )
            try:
                bundle = LiveHASource(art, ha).build_bundle()
            except Exception as exc:  # noqa: BLE001 - surface upstream failure to caller
                raise HTTPException(status_code=502, detail=f"HA pull failed: {exc}") from exc

        result = predict(bundle, art)
        # Log every call -> seeds the later prediction-vs-actual record (spec 2.1).
        log.info("predict: as_of=%s elev=%.3f freeboard=%.3f p_crest=%.2f fresh=%s",
                 result.generated_at, result.current_elevation, result.freeboard_ft,
                 result.p_cross_crest, result.data_fresh)
        return result

    return app


app = create_app()
