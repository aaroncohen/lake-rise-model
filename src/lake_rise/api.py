"""Stateless prediction API (spec 6). Loads the latest artifact, builds an input
bundle (inline snapshot or live HA pull), runs the pure predictor, and returns the
HA-shaped result. Notifications belong to Home Assistant; this only serves outputs.

Run: ``uvicorn lake_rise.api:app`` (config via env: HA_URL, HA_TOKEN, LAKE_RISE_ARTIFACT)."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from . import forecast_log, historical
from .alerting.rules import evaluate
from .alerting.service import live_source
from .artifact import Artifact
from .bundle import InputBundle, ScenarioRain
from .field import FieldSummary, summarize
from .field_example import example_summary
from .lake_filter import TREND_MIN_MINUTES
from .predict import PredictionResult, predict, scenario_rain
from .presets import STORM_PRESETS, build_storm
from .scenarios import (
    confidence_for_lead, confidence_label, synthesize_scenarios,
)
from .secutil import token_matches
from .settings import ha_config_from_env
from .storms import storm_series
from .sources.live_ha import LiveHASource, LiveConditions
from .sources.snapshot import Snapshot, bundle_from_snapshot
from .validate import run_anchors

log = logging.getLogger("lake_rise.api")
STATIC_DIR = Path(__file__).resolve().parent / "static"

_clock = time.monotonic        # module attribute so tests can advance it
_FIELD_CACHE_SECONDS = 120     # D17: /field is computed live and cached for 2 minutes
_FIELD_RETRY_S = 30            # after a failed build, /field waits this long before calling HA again
# The forecast parts of a field summary, carried over from the last good one when a build
# brings no forecast of either kind (D24). Levels carry over too, with their "reached" taken
# from the new build: it reflects the lake now.
_FIELD_FORECAST_PARTS = ("mode", "peaks_ft", "peak_levels", "cases", "timeline", "rain_6h",
                         "rain_total_in", "ensemble_cycle")


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
    agreement_pct: int | None = None,
) -> dict:
    """Build the ``rainfall`` sub-dict shared by /simulate and /live/predict. With an ensemble
    (``agreement_pct``), the confidence is how closely its forecasts agree on the peak; otherwise
    it is the QPF-skill-by-lead table's."""
    by_name = {s.name: s.hourly_in for s in scenarios}
    totals = {n: round(sum(h), 2) for n, h in by_name.items()}
    peak_hour = (max(range(len(series)), key=lambda i: series[i]) + 1) if any(series) else None
    confidence_pct, band_widen_at_storm = confidence_for_lead(art, start_offset_h, month)
    if agreement_pct is not None:
        confidence_pct = agreement_pct
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
        "confidence_basis": "ensemble_agreement" if agreement_pct is not None else "lead_time",
        "agreement_tolerance_ft": (art.ensemble_rain.agreement_tolerance_ft
                                   if agreement_pct is not None and art.ensemble_rain else None),
        "band_widen_at_storm": band_widen_at_storm,
    }


def create_app(art: Artifact | None = None) -> FastAPI:
    from .calibration.service import active_artifact_and_version

    # An explicitly-supplied artifact (tests) is pinned; otherwise resolve the calibration
    # active version and refresh it per-request so an approved/reverted re-tuning is served.
    _pinned = art is not None
    if art is None:
        art, _active_version = active_artifact_and_version()
    else:
        _active_version = None
    # Anchor results are deterministic for a given artifact: compute once, recompute on version change.
    anchors = [a.__dict__ for a in run_anchors(art)]

    def _ensure_current() -> None:
        """Re-resolve the calibration active version; reload artifact + anchors only if it changed."""
        nonlocal art, anchors, _active_version
        if _pinned:
            return
        from .calibration.config import calibration_config_from_env
        from .calibration.state import load_state
        version = load_state(calibration_config_from_env().state_path).active_version
        if version == _active_version:
            return
        art, _active_version = active_artifact_and_version()
        anchors = [a.__dict__ for a in run_anchors(art)]
        app.state.art = art
        app.state.anchors = anchors

    from .alerting import alert_config_from_env
    from .alerting.scheduler import start_scheduler
    from .calibration.config import calibration_config_from_env
    from .calibration.scheduler import start_archive_scheduler

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Start the hourly alert scheduler if alerting is enabled & prerequisites present.
        scheduler = start_scheduler(alert_config_from_env())
        app.state.alert_scheduler = scheduler
        # Start the hourly continuous-archive job (the calibration data substrate).
        archive_scheduler = start_archive_scheduler(calibration_config_from_env(), art)
        app.state.archive_scheduler = archive_scheduler
        try:
            yield
        finally:
            for s in (scheduler, archive_scheduler):
                if s is not None:
                    s.shutdown(wait=False)

    app = FastAPI(title="Crystal Lake lake-rise prediction", version=art.version,
                  lifespan=lifespan)
    app.state.art = art
    app.state.anchors = anchors
    app.state.field_cache = None       # {"key", "at", "value"}: the last /field response
    app.state.field_last_good = None   # {"key", "value"}: the last summary with a forecast
    app.state.field_failed = None      # {"key", "at"}: the last failed build
    app.state.field_lock = threading.Lock()
    app.state.field_example = None     # {"key", "value"}: the example storm for this hour

    @app.middleware("http")
    async def _refresh_model(request, call_next):
        # Pick up an approved/reverted calibration version before serving the request. Cheap:
        # a small JSON read; the artifact reloads only when the active version actually changed.
        _ensure_current()
        return await call_next(request)

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
                "observed_interval_minutes": acfg.observed_interval_minutes,
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
            if not token_matches(x_alert_token, cfg.api_token):
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

    @app.post("/calibration/approve")
    def calibration_approve(candidate_id: str, token: str,
                            x_calib_token: str | None = Header(default=None)) -> dict:
        """Approve the pending calibration proposal (the emailed one-time token). Gated by the
        X-Calib-Token header against CALIB_API_TOKEN; if that's unset the HTTP approve path is
        disabled (use the CLI). Promotes the candidate to a new active version."""
        from .calibration import service
        from .calibration.config import calibration_config_from_env

        cfg = calibration_config_from_env()
        if not cfg.api_token:
            raise HTTPException(403, "HTTP approve disabled: set CALIB_API_TOKEN, or use the CLI.")
        if not token_matches(x_calib_token, cfg.api_token):
            raise HTTPException(403, "invalid or missing X-Calib-Token.")
        try:
            version = service.approve(cfg, candidate_id, token)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {"approved": candidate_id, "active_version": version}

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
            "control_elev_ft": {c: art.stop_logs.control_elev(c)
                                for c in range(0, art.stop_logs.max_count + 1)},
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
        logs = (None if req.stop_log_count is None
                else max(0, min(req.stop_log_count, art.stop_logs.max_count)))
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
        source = LiveHASource(art, ha)
        try:
            conditions = source.fetch_conditions()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"HA pull failed: {exc}") from exc

        month = datetime.fromisoformat(conditions.as_of).month

        # --- build forecast series -----------------------------------------------
        members: list[ScenarioRain] = []
        bundle = None
        acfg = alert_config_from_env()
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
        elif req.start_offset_h == 0 and req.horizon_h == acfg.horizon_hours:
            # The shared builder (as the alert job and /field use): its bundle carries the
            # forecast, ensemble members and archive spin-up.
            try:
                bundle = live_source(art, acfg).build_bundle()
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"HA pull failed: {exc}") from exc
            if req.stop_log_count is not None:
                bundle = bundle.model_copy(update={"stop_log_count": max(
                    0, min(req.stop_log_count, art.stop_logs.max_count))})
            scenarios = bundle.forecast_scenarios
            members = bundle.ensemble_members
            series = next(s.hourly_in for s in scenarios if s.name == "median")
            forecast_source = "Apple WeatherKit (live)"
            if members:
                cycle = datetime.fromisoformat(str(bundle.ensemble_cycle))
                forecast_source = (f"ECMWF ensemble {cycle:%Y-%m-%d %HZ}, calibrated "
                                   "(timed by Apple WeatherKit)")
        else:
            # Live WeatherKit path: apply offset + pad/truncate to horizon.
            raw = list(conditions.forecast_point_in)
            series = ([0.0] * req.start_offset_h + raw)[:req.horizon_h]
            series += [0.0] * max(0, req.horizon_h - len(series))
            forecast_source = "Apple WeatherKit (live)"
            # Pass PoP only when the forecast is aligned (offset 0, no what-if).
            pop = conditions.forecast_pop_frac if req.start_offset_h == 0 else None
            scenarios = synthesize_scenarios(art, series, month=month, pop_frac=pop)

        if bundle is None:
            bundle = InputBundle(
                as_of=conditions.as_of,
                current_elevation_abs_ft=(
                    conditions.reading_ft + art.datum.sensor_to_absolute_offset_ft
                ),
                stop_log_count=conditions.stop_log_count,
                trailing_rainfall_in=conditions.trailing_rainfall_in,
                forecast_scenarios=scenarios,
                initial_sm_in=None,
                rainfall_has_gaps=conditions.has_gaps,
            )
            # Same spin-up as the alert path: replay the archived true rain, topped up with
            # this pull's measured hourly rain (not the synthetic older-block prepend).
            recent = conditions.trailing_rainfall_in[-ha.trailing_days * 24:]
            bundle = source.with_archive_spinup(bundle, recent)
            # The same starting level as the default path: filtered on the live stop logs, then
            # the caller's what-if count.
            bundle = source.with_filtered_level(bundle)
            if req.stop_log_count is not None:
                bundle = bundle.model_copy(update={"stop_log_count": max(
                    0, min(req.stop_log_count, art.stop_logs.max_count))})
        result = predict(bundle, art)
        if members:                    # show the rain of the members picked as low/median/high
            scenarios = scenario_rain(bundle, result)
            series = next(s.hourly_in for s in scenarios if s.name == "median")

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
                agreement_pct=result.ensemble_agreement_pct,
            ),
            "current": {
                "current_elevation_abs_ft": bundle.current_elevation_abs_ft,
                "stop_log_count": bundle.stop_log_count,
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

    @app.get("/field", response_model=FieldSummary)
    def field() -> FieldSummary:
        """The field view's summary, built live and cached for 2 minutes per artifact version.
        Public and read-only (D13). A failed Home Assistant pull serves the last good result
        marked stale; a build with no forecast keeps the last forecast under the new lake (D24).
        Errors are never cached, but for 30 s after one /field answers without calling HA."""
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if ha_config_from_env() is None:
            raise HTTPException(status_code=503, detail="No live HA source configured")
        key = (art.version, _active_version)
        with app.state.field_lock:
            cached = app.state.field_cache
            if (cached and cached["key"] == key
                    and _clock() - cached["at"] < _FIELD_CACHE_SECONDS):
                return cached["value"]
            last = app.state.field_last_good
            last = last["value"] if last and last["key"] == key else None

            def unreachable() -> FieldSummary:
                if last is None:
                    raise HTTPException(status_code=502, detail="Can't reach the gauge")
                # The old gauge age would read as current: None makes the page show the problem.
                details = (last.details.model_copy(update={"gauge_age_min": None,
                                                           "surface_age_min": None})
                           if last.details else None)
                # Likewise the rain rate: unknown, not the last one.
                lake = last.lake.model_copy(update={"rain_rate_in_per_h": None})
                return last.model_copy(update={"details": details, "lake": lake,
                                               "stale_since": last.computed_at,
                                               "unavailable": "Can't reach the gauge"})

            failed = app.state.field_failed
            # During an HA outage every visitor would otherwise wait out the client timeout.
            if (failed and failed["key"] == key
                    and _clock() - failed["at"] < _FIELD_RETRY_S):
                return unreachable()
            try:
                cfg = alert_config_from_env()
                src = live_source(art, cfg)
                bundle = src.build_bundle()
                try:
                    # Before this forecast's hour: at :50-:59 the window holds this hour's own record.
                    previous = forecast_log.previous(now, before_as_of=bundle.as_of)
                except Exception:   # a log problem must never fail or stale the field view
                    log.warning("field: forecast log unreadable", exc_info=True)
                    previous = None
                result = predict(bundle, art)
                decision = evaluate(result, bundle, art, cfg)
                has_forecast = bool(result.ensemble_peaks_ft
                                    or any(s.hourly_in for s in bundle.forecast_scenarios))
                options = ([(k, predict(bundle.model_copy(update={"stop_log_count": k}), art))
                            for k in range(bundle.stop_log_count - 1, -1, -1)]
                           if bundle.stop_log_count > 0 and has_forecast else [])
                # The filter's rate (P10) once it has run 2 h past any stop-log cut; else the
                # 30-min median's hour-on-hour change.
                est = getattr(src, "lake_estimate", None)
                if est is not None and (est.minutes or 0) >= TREND_MIN_MINUTES:
                    trend, trend_was = est.rate_ft_per_h, est.rate_hour_ago_ft_per_h
                else:
                    trend = src.lake_trend_ft_per_h(now)
                    trend_was = src.lake_trend_ft_per_h(now - timedelta(hours=1))
                summary = summarize(
                    result, bundle, decision, art,
                    observation=src.fetch_gauge_observation(now),
                    trend_ft_per_h=trend,
                    # The flag waits out the laser's routine surface gaps (gauge_alert).
                    gauge_problem=src.gauge_alert(now), computed_at=now,
                    gauge_age_min=src.gauge_age_minutes(now),
                    surface_age_min=src.surface_age_minutes(now),
                    rain_rate_in_per_h=src.rain_rate_in_per_h(now),
                    previous=previous,
                    trend_was_ft_per_h=trend_was,
                    options=options)
            except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as exc:
                log.warning("field: build failed: %s", exc)
                app.state.field_failed = {"key": key, "at": _clock()}
                return unreachable()
            app.state.field_failed = None
            if summary.mode == "no_forecast":
                if last is not None:
                    reached = {lv.key: lv.reached for lv in summary.levels}
                    # change and trend_change stay as the new summary has them. The parts in
                    # _FIELD_FORECAST_PARTS, the status, and the advice (actions, next_level,
                    # stop_logs) carry over: the advice belongs to the displayed, stale-marked
                    # forecast. The status is that forecast's verdict, but its observed EAP
                    # level is a live reading.
                    # The advice carries over only while the live inputs it rests on are
                    # unchanged: a newly observed EAP level, or a different stop-log count,
                    # takes the new build's advice.
                    # details: the forecast rows are last good's, the data rows are live.
                    def title(s: FieldSummary) -> str | None:
                        return (s.status.observed_eap or {}).get("title")

                    advice = summary if title(summary) != title(last) else last
                    logs = (summary if getattr(last.details, "stop_log_count", None)
                            != summary.details.stop_log_count else last)
                    details = last.details.model_copy(update={k: getattr(summary.details, k) for k in (
                        "gauge_age_min", "surface_age_min", "rain_gauge_24h_in", "stop_log_count",
                        "stop_log_default", "soil_pct")}) if last.details else summary.details
                    summary = summary.model_copy(update={
                        "details": details,
                        **{k: getattr(last, k) for k in _FIELD_FORECAST_PARTS},
                        "actions": advice.actions, "next_level": advice.next_level,
                        "stop_logs": logs.stop_logs,
                        "status": last.status.model_copy(
                            update={"observed_eap": summary.status.observed_eap}),
                        "levels": [lv.model_copy(update={"reached": reached[lv.key]})
                                   for lv in last.levels],
                        "stale_since": last.computed_at, "unavailable": "No new forecast"})
            else:
                app.state.field_last_good = {"key": key, "value": summary}
            app.state.field_cache = {"key": key, "at": _clock(), "value": summary}
            return summary

    @app.get("/field/example", response_model=FieldSummary)
    def field_example() -> FieldSummary:
        """The example storm, for showing the field view to people (/?example=storm). Built from
        a fixed storm as if forecast this hour; it reads nothing live and touches no /field cache,
        alert state or forecast log."""
        hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        key = (art.version, hour)
        cached = app.state.field_example
        if cached is None or cached["key"] != key:
            cached = app.state.field_example = {
                "key": key, "value": example_summary(art, alert_config_from_env(), hour)}
        return cached["value"]

    @app.get("/", response_class=HTMLResponse)
    def index(mode: str | None = None) -> Response:
        """The field view (D31). Old ?mode=manual|backtest links redirect to the dashboard,
        relatively, so a proxy prefix such as /lake-rise/ survives."""
        if mode in ("manual", "backtest"):
            return RedirectResponse(f"dashboard?mode={mode}", status_code=307)
        return HTMLResponse((STATIC_DIR / "field.html").read_text())

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> str:
        return (STATIC_DIR / "index.html").read_text()

    return app


app = create_app()
