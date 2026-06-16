"""Stateless prediction API (spec 6). Loads the latest artifact, builds an input
bundle (inline snapshot or live HA pull), runs the pure predictor, and returns the
HA-shaped result. Notifications belong to Home Assistant; this only serves outputs.

Run: ``uvicorn lake_rise.api:app`` (config via env: HA_URL, HA_TOKEN, LAKE_RISE_ARTIFACT)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import historical
from .artifact import Artifact, load_artifact
from .bundle import InputBundle, ScenarioRain
from .predict import PredictionResult, predict
from .presets import STORM_PRESETS, build_storm
from .scenarios import confidence_for_lead, confidence_label, synthesize_scenarios
from .settings import artifact_path_from_env, ha_config_from_env
from .storms import storm_series
from .sources.live_ha import LiveHASource, LiveConditions
from .sources.snapshot import Snapshot, bundle_from_snapshot
from .validate import run_anchors

log = logging.getLogger("lake_rise.api")
STATIC_DIR = Path(__file__).resolve().parent / "static"


class StormSpec(BaseModel):
    """One of: a preset key, a historical-storm id, a custom hourly array, or a
    constant rate+duration."""
    preset: str | None = None
    historical_id: str | None = None
    rate_in_per_hr: float | None = None
    duration_h: int | None = None
    hourly_in: list[float] | None = None
    start_offset_h: int = Field(0, ge=0, le=168)   # hours of dry lead before the storm begins
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


class LivePredictRequest(BaseModel):
    """Live prediction: optionally override with a what-if storm, else use Apple WeatherKit."""
    storm: StormSpec | None = None
    start_offset_h: int = Field(0, ge=0, le=168)   # dry-lead hours before storm (what-if only)
    horizon_h: int = Field(72, ge=6, le=168)
    stop_log_count: int | None = None   # None -> live/seasonal default; else a what-if override


_BACKTEST_MAX_HOURS = 240   # HAConfig default trailing_days (10) * 24


class BacktestRequest(BaseModel):
    """Request a backtest over the past N hours."""
    hours_back: int = 48
    stop_log_count: int | None = None   # None -> seasonal default at T0


def _storm_series(art: Artifact, spec: StormSpec) -> list[float]:
    """HTTP wrapper over the shared ``storms.storm_series`` builder; maps an unknown
    preset/historical id (KeyError) to a 400."""
    try:
        return storm_series(
            art,
            preset=spec.preset,
            historical_id=spec.historical_id,
            hourly_in=spec.hourly_in,
            rate_in_per_hr=spec.rate_in_per_hr,
            duration_h=spec.duration_h,
            start_offset_h=spec.start_offset_h,
            horizon_h=spec.horizon_h,
        )
    except KeyError as exc:
        raise HTTPException(400, f"unknown storm spec: {exc}") from exc


def _rainfall_block(
    art: Artifact,
    series: list[float],
    scenarios: list,
    month: int,
    start_offset_h: int,
    initial_sm_in: float | None,
) -> dict:
    """Build the ``rainfall`` sub-dict shared by /simulate and /live/predict."""
    by_name = {s.name: s.hourly_in for s in scenarios}
    totals = {n: round(sum(h), 2) for n, h in by_name.items()}
    peak_hour = (max(range(len(series)), key=lambda i: series[i]) + 1) if any(series) else None
    confidence_pct, band_widen_at_storm = confidence_for_lead(art, start_offset_h, month)
    label = confidence_label(confidence_pct)
    sm = (initial_sm_in if initial_sm_in is not None
          else round(art.seasonal_sm_default(month), 2))
    return {
        "median_hourly_in": series,
        "low_hourly_in": by_name.get("low", series),
        "high_hourly_in": by_name.get("high", series),
        "total_in": round(sum(series), 2),
        "peak_hour": peak_hour,
        "scenario_totals_in": totals,
        "initial_sm_in": sm,
        "storm_start_h": start_offset_h,
        "confidence_pct": confidence_pct,
        "confidence_label": label,
        "band_widen_at_storm": band_widen_at_storm,
    }


def create_app(art: Artifact | None = None) -> FastAPI:
    art = art or load_artifact(artifact_path_from_env())
    # Anchor results are deterministic for a given artifact: compute once at startup.
    anchors = [a.__dict__ for a in run_anchors(art)]

    from .alerting import alert_config_from_env
    from .alerting.scheduler import start_scheduler

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Start the hourly alert scheduler if alerting is enabled & prerequisites present.
        scheduler = start_scheduler(alert_config_from_env())
        app.state.alert_scheduler = scheduler
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)

    app = FastAPI(title="Crystal Lake lake-rise prediction", version=art.version,
                  lifespan=lifespan)
    app.state.art = art
    app.state.anchors = anchors

    @app.get("/health")
    def health() -> dict:
        ha = ha_config_from_env()
        acfg = alert_config_from_env()
        return {
            "status": "ok",
            "model_version": art.version,
            "live_source_configured": ha is not None,
            "anchors_pass": all(a["passed"] for a in anchors),
            "alerting": {
                "enabled": acfg.enabled,
                "scheduler_running": getattr(app.state, "alert_scheduler", None) is not None,
                "interval_minutes": acfg.interval_minutes,
                "channels": list(acfg.channels),
                "email_configured": acfg.smtp.configured,
                "sms_configured": acfg.twilio.configured,
                "test_enabled": acfg.test_enabled,
            },
        }

    @app.post("/alert/run")
    def alert_run(dry_run: bool = True, x_alert_token: str | None = Header(default=None)) -> dict:
        """Evaluate the forecast now and dispatch any crossing notices (manual / HA-triggered).
        Defaults to dry_run=True (renders without sending or mutating state).

        A REAL send (dry_run=false) requires the X-Alert-Token header to match the
        configured ALERT_API_TOKEN, so a random network/UI visitor cannot trigger alerts;
        if ALERT_API_TOKEN is unset, the HTTP send path is disabled entirely. The preview
        (dry_run=true) path stays open — it only renders and never mutates state."""
        from .alerting import run_once
        cfg = alert_config_from_env()
        if not dry_run:
            if not cfg.api_token:
                raise HTTPException(
                    status_code=403,
                    detail="HTTP send path disabled: set ALERT_API_TOKEN to enable dry_run=false.")
            if x_alert_token != cfg.api_token:
                raise HTTPException(status_code=403, detail="invalid or missing X-Alert-Token.")
        try:
            run = run_once(cfg, art=art, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"alert run failed: {exc}") from exc
        d = run.decision
        return {
            "dry_run": dry_run,
            "active_rank": d.active_rank,
            "active_level": d.active_level_name,
            "p_early_warning": d.probabilities.get("early_warning"),
            "p_crest": d.probabilities.get("dam_crest"),
            "p_bridge_deck": d.probabilities.get("bridge_deck"),
            "test_active": d.test_active,
            "actions": [a.kind for a in run.actions],
            "sent": run.sent,
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
        out = []
        for p in STORM_PRESETS.values():
            series = build_storm(art, p.key)
            nz = [i for i, v in enumerate(series) if v > 0]
            duration_h = (nz[-1] - nz[0] + 1) if nz else 0
            out.append({
                "key": p.key,
                "label": p.label,
                "description": p.description,
                "total_in": round(sum(series), 2),
                "duration_h": duration_h,
                "peak_in_per_hr": round(max(series), 3) if series else 0.0,
            })
        return out

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
            "backtest_max_hours": _BACKTEST_MAX_HOURS,
            # Season when stop-logs are in (UI computes the seasonal default count).
            "stop_log_season_installed": art.stop_logs.season_installed.model_dump(),
        }

    @app.post("/backtest")
    def backtest_endpoint(req: BacktestRequest = Body(default=BacktestRequest())) -> dict:
        """Validate the model against real history: anchor at T0, run forward with
        real observed rain, compare predicted vs actual gauge levels."""
        ha = ha_config_from_env()
        if ha is None:
            raise HTTPException(
                status_code=503,
                detail="No live HA source configured (set HA_URL and HA_TOKEN).",
            )
        hours_back = max(6, min(req.hours_back, _BACKTEST_MAX_HOURS))
        logs = None if req.stop_log_count is None else max(0, min(req.stop_log_count, 3))
        try:
            result = LiveHASource(art, ha).fetch_backtest(hours_back, stop_log_count=logs)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"HA pull failed: {exc}") from exc
        log.info(
            "backtest: hours_back=%d t0=%s rmse_ft=%s",
            hours_back, result.get("t0"), result.get("metrics", {}).get("rmse_ft"),
        )
        return result

    @app.get("/historical")
    def historical_catalog() -> list[dict]:
        """Catalog of real Western Washington storms, most-severe first."""
        return historical.catalog()

    @app.post("/simulate")
    def simulate(req: SimulateRequest) -> dict:
        """Project a preset or custom storm from user-supplied lake/watershed state.
        Returns the prediction plus the driving rainfall (so the UI can show how much
        rain, and when, is contributing)."""
        series = _storm_series(art, req.storm)
        if req.band:
            scenarios = synthesize_scenarios(art, series, month=req.month)
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
        # Forecast confidence falls off with the storm's lead time, from the same
        # QPF-skill model that widens the band (so the indicator and the band agree).
        return {
            **result.model_dump(mode="json"),
            "rainfall": _rainfall_block(art, series, scenarios, req.month,
                                        req.storm.start_offset_h, bundle.initial_sm_in),
        }

    @app.post("/live/predict")
    def live_predict(req: LivePredictRequest = Body(default=LivePredictRequest())) -> dict:
        """Full live prediction from HA: pulls current conditions, optionally overrides
        the forecast with a what-if storm, and returns the prediction + rich context."""
        ha = ha_config_from_env()
        if ha is None:
            raise HTTPException(
                status_code=503,
                detail="No live HA source configured (set HA_URL and HA_TOKEN).",
            )
        try:
            conditions = LiveHASource(art, ha).fetch_conditions()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"HA pull failed: {exc}") from exc

        month = datetime.fromisoformat(conditions.as_of).month

        # --- build forecast series -----------------------------------------------
        if req.storm is not None:
            # What-if override: treat the storm spec the same as /simulate, but
            # respect req.start_offset_h and req.horizon_h as the enclosing defaults.
            storm_spec = StormSpec(
                preset=req.storm.preset,
                historical_id=req.storm.historical_id,
                rate_in_per_hr=req.storm.rate_in_per_hr,
                duration_h=req.storm.duration_h,
                hourly_in=req.storm.hourly_in,
                start_offset_h=req.storm.start_offset_h or req.start_offset_h,
                horizon_h=req.storm.horizon_h or req.horizon_h,
            )
            series = _storm_series(art, storm_spec)
            forecast_source = (
                f"what-if: {req.storm.preset or req.storm.historical_id or 'custom'}"
            )
            scenarios = synthesize_scenarios(art, series, month=month)
        else:
            # Live WeatherKit path: apply offset + pad/truncate to horizon.
            raw = list(conditions.forecast_point_in)
            series = ([0.0] * req.start_offset_h + raw)[:req.horizon_h]
            series += [0.0] * max(0, req.horizon_h - len(series))
            forecast_source = "Apple WeatherKit (live)"
            # Pass PoP only when the forecast is aligned (offset 0, no what-if).
            pop = conditions.forecast_pop_frac if req.start_offset_h == 0 else None
            scenarios = synthesize_scenarios(art, series, month=month, pop_frac=pop)

        # Stop-logs: live/seasonal default unless the user supplies a what-if override.
        logs = (conditions.stop_log_count if req.stop_log_count is None
                else max(0, min(req.stop_log_count, 3)))
        bundle = InputBundle(
            as_of=conditions.as_of,
            current_elevation_abs_ft=(
                conditions.reading_ft + art.datum.sensor_to_absolute_offset_ft
            ),
            stop_log_count=logs,
            trailing_rainfall_in=conditions.trailing_rainfall_in,
            forecast_scenarios=scenarios,
            initial_sm_in=None,
            rainfall_has_gaps=conditions.has_gaps,
        )
        result = predict(bundle, art)

        log.info(
            "live_predict: as_of=%s elev=%.3f freeboard=%.3f p_crest=%.2f fresh=%s source=%s",
            result.generated_at, result.current_elevation, result.freeboard_ft,
            result.p_cross_crest, result.data_fresh, forecast_source,
        )

        return {
            **result.model_dump(mode="json"),
            "rainfall": _rainfall_block(
                art, series, scenarios, month, req.start_offset_h,
                None,   # initial_sm_in from hindcast, not user-supplied
            ),
            "current": {
                "current_elevation_abs_ft": bundle.current_elevation_abs_ft,
                "stop_log_count": logs,
                "rain_rate_in_per_hr": conditions.rate_in_per_hr,
                "rain_today_in": conditions.today_in,
                "rain_week_in": conditions.week_in,
                "rain_month_in": conditions.month_in,
                "rain_event_in": conditions.event_in,
                "as_of": conditions.as_of,
                "data_fresh": not conditions.has_gaps,
                "forecast_source": forecast_source,
            },
            "past": {
                "window_days": round(len(conditions.trailing_rainfall_in) / 24),
                "total_in": round(sum(conditions.trailing_rainfall_in), 2),
                "older_block_in": round(conditions.older_block_in, 2),
                "sm_in": round(result.state_sm_in, 2),
                "s_if_in": round(result.state_s_if_in, 3),
            },
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text()

    return app


app = create_app()
