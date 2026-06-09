"""Stateless prediction API (spec 6). Loads the latest artifact, builds an input
bundle (inline snapshot or live HA pull), runs the pure predictor, and returns the
HA-shaped result. Notifications belong to Home Assistant; this only serves outputs.

Run: ``uvicorn lake_rise.api:app`` (config via env: HA_URL, HA_TOKEN, LAKE_RISE_ARTIFACT)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .artifact import Artifact, load_artifact
from .bundle import InputBundle, ScenarioRain
from .predict import PredictionResult, predict
from .presets import STORM_PRESETS, build_storm
from .scenarios import synthesize_scenarios
from .settings import artifact_path_from_env, ha_config_from_env
from .sources.live_ha import LiveHASource
from .sources.snapshot import Snapshot, bundle_from_snapshot
from .validate import run_anchors

log = logging.getLogger("lake_rise.api")
STATIC_DIR = Path(__file__).resolve().parent / "static"


class StormSpec(BaseModel):
    """One of: a preset key, a custom hourly array, or a constant rate+duration."""
    preset: str | None = None
    rate_in_per_hr: float | None = None
    duration_h: int | None = None
    hourly_in: list[float] | None = None
    horizon_h: int = Field(72, ge=6, le=168)


class SimulateRequest(BaseModel):
    """Situational lake/watershed state + a storm to project (visualization page)."""
    current_elevation_abs_ft: float
    stop_log_count: int = Field(3, ge=0, le=3)
    initial_sm_in: float | None = None     # None -> seasonal default for the month
    initial_s_if_in: float = 0.0
    month: int = Field(1, ge=1, le=12)     # season drives PET + seasonal SM default
    band: bool = True                      # synthesize low/median/high around the storm
    storm: StormSpec = StormSpec(preset="moderate_storm")


def _storm_series(art: Artifact, spec: StormSpec) -> list[float]:
    if spec.preset is not None:
        try:
            series = build_storm(art, spec.preset)
        except KeyError as exc:
            raise HTTPException(400, f"unknown preset '{spec.preset}'") from exc
    elif spec.hourly_in is not None:
        series = list(spec.hourly_in)
    elif spec.rate_in_per_hr is not None and spec.duration_h is not None:
        series = [spec.rate_in_per_hr] * spec.duration_h
    else:
        series = []
    # pad/truncate to the horizon so the recession limb is visible
    h = spec.horizon_h
    return (series + [0.0] * h)[:h]


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

    @app.get("/presets")
    def presets() -> list[dict]:
        return [{"key": p.key, "label": p.label, "description": p.description}
                for p in STORM_PRESETS.values()]

    @app.get("/config")
    def config() -> dict:
        """Constants the UI needs to guide inputs: bucket capacity, the seasonal
        soil-moisture default per month, control elevations, and thresholds."""
        return {
            "lzsn_in": art.hspf.LZSN_in,
            "seasonal_sm_default_in": {m: round(art.seasonal_sm_default(m), 2) for m in range(1, 13)},
            "control_elev_ft": {c: art.stop_logs.control_elev(c) for c in range(0, 4)},
            "thresholds_abs_ft": art.thresholds_abs_ft.model_dump(),
            # WQ-pole / staff "stick" reading = absolute elevation - this offset.
            "sensor_to_absolute_offset_ft": art.datum.sensor_to_absolute_offset_ft,
        }

    @app.post("/simulate")
    def simulate(req: SimulateRequest) -> dict:
        """Project a preset or custom storm from user-supplied lake/watershed state.
        Returns the prediction plus the driving rainfall (so the UI can show how much
        rain, and when, is contributing)."""
        series = _storm_series(art, req.storm)
        if req.band:
            scenarios = synthesize_scenarios(art, series)
        else:
            scenarios = [ScenarioRain(name=n, hourly_in=series) for n in ("low", "median", "high")]
        bundle = InputBundle(
            as_of=datetime(2026, req.month, 15),
            current_elevation_abs_ft=req.current_elevation_abs_ft,
            stop_log_count=req.stop_log_count,
            forecast_scenarios=scenarios,
            initial_sm_in=req.initial_sm_in,
            initial_s_if_in=req.initial_s_if_in,
        )
        result = predict(bundle, art)
        totals = {s.name: round(sum(s.hourly_in), 2) for s in scenarios}
        peak_hour = (max(range(len(series)), key=lambda i: series[i]) + 1) if any(series) else None
        return {
            **result.model_dump(mode="json"),
            "rainfall": {
                "median_hourly_in": series,         # what drives the median scenario
                "total_in": round(sum(series), 2),
                "peak_hour": peak_hour,
                "scenario_totals_in": totals,
                "initial_sm_in": bundle.initial_sm_in if bundle.initial_sm_in is not None
                                 else round(art.seasonal_sm_default(req.month), 2),
            },
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text()

    return app


app = create_app()
