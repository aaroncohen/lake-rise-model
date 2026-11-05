"""Live Home Assistant REST source. Implements the same DataSource protocol as the
fixture source, so the predictor and API are unchanged whether data is snapshotted or
pulled live. Read-only: GET states/history + a read-only weather.get_forecasts call.

Config (entities) defaults to the real Crystal Lake setup; credentials come from the
caller (see settings.py). Network calls are isolated in small methods and go through an
injectable httpx.Client so they can be mocked in tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math

import httpx

from .. import backtest
from ..artifact import Artifact
from ..bundle import InputBundle, SpinupState
from ..geometry import control_elev_for_stop_logs, default_stop_log_count
from ..hourly import (
    floor_hour,
    hour_grid,
    parse_ha_rows,
    parse_ha_states,
    rain_hourly_from_daily_accumulator,
)
from ..observed import OBSERVED_AVERAGE_MINUTES, GaugeObservation
from ..predict import spinup_from_state, spinup_to_state
from .snapshot import Snapshot, bundle_from_snapshot

# Trailing window for denoising the LIVE "now" lake anchor (there's no completed hourly
# bucket to median over yet). 30 min is the knee of the noise-vs-lag curve on real data:
# residual anchor noise ~0.21 in (negligible for the forecast start and freeboard) while
# the worst-case lag on a fast storm rise is only ~window/2 = 15 min. A trailing median
# lags a monotonic rise by ~half the window, so keep this short for the early-warning path.
LIVE_ANCHOR_WINDOW_HOURS = 0.5

# Hours of an unavailable rain accumulator in the trailing window that mark the hindcast's rain as
# degraded (-> the predictor floors the state at the seasonal normal, #4). Same tolerance as the
# archive state estimator's gap rule (antecedent.usable_window).
RAIN_GAP_DEGRADED_HOURS = 6

# Physically plausible hourly means for the archived weather; anything outside is a sensor
# fault or a disconnected sentinel (the old lake probe read -40 F) and is stored as missing.
# Water below freezing can't be open lake water, so the lake probe's floor is 32 F.
MET_VALID_RANGE = {
    "air_temp_f": (-20.0, 115.0),
    "rh_pct": (1.0, 100.0),
    "wind_mph": (0.0, 100.0),
    "water_temp_f": (32.0, 95.0),
}


def _rain_gap_hours(states: list[tuple[datetime, float | None]], start: datetime,
                    hourly: list[float | None]) -> int:
    """Hours the rain accumulator was genuinely unavailable: ``None`` hours from HA's oldest
    retained row onward. Hours before that row are the edge of HA's ~10-day raw-history
    retention, not a sensor outage: the accumulator writes a row only when it changes, so after
    a dry spell at the window's start the nightly purge removes the row that held its value, and
    HA returns no start-state row. Counting those hours flagged a healthy gauge as degraded and
    floored the forecast state (2026-11-05). A real outage still records
    ``unknown``/``unavailable`` rows, so it is counted."""
    if not states:
        return len(hourly)
    first = min(t for t, _ in states)
    return sum(r is None for i, r in enumerate(hourly) if start + timedelta(hours=i) >= first)


@dataclass
class HAConfig:
    base_url: str                      # e.g. http://homeassistant.local:8123
    token: str                         # long-lived access token
    lake_sensor: str = "sensor.crystal_lake_depth_smoothed"
    # The unsmoothed depth template the smoothed one averages: it goes ``unknown`` when the
    # ultrasonic has no valid surface echo, so the archive records those hours as missing
    # instead of the smoothed sensor's held last sample.
    lake_raw_sensor: str = "sensor.crystal_lake_depth"
    # Liveness is judged from this raw, least-rounded sensor — NOT lake_sensor. The depth
    # chain (gw3000b_air_gap → air_gap_filtered → crystal_lake_depth → ..._smoothed) rounds
    # to 0.01 ft, which swallows the sub-rounding air-gap noise, so the smoothed value can
    # legitimately hold for 30+ min in calm water and look "stale" even though the gauge is
    # healthy. The raw air-gap sensor still moves every ~1-7 min, so it's an honest
    # hardware-liveness signal. Override via LAKE_RISE_LAKE_FRESH_SENSOR.
    lake_fresh_sensor: str = "sensor.gw3000b_air_gap_1"
    # Rain that DRIVES the model (hindcast, archive, backtest, storm capture, statistics backfill).
    # Must be a since-midnight accumulator: hourly rain is its per-hour rise
    # (hourly.rain_hourly_from_daily_accumulator). Not gw3000b_hourly_rain_piezo -- a rolling
    # 60-min total that double-counts bursts (2026-11-04 calibration-log entry).
    rain_sensor: str = "sensor.gw3000b_daily_rain_piezo"
    forecast_entity: str = "weather.47_77849_122_10882"   # Apple WeatherKit (preferred)
    # Operator-set board count (input_number, 0..max boards); the date-based seasonal default
    # is the fallback when it's missing, unavailable or out of range. None = always the default.
    stop_log_helper: str | None = "input_number.crystal_lake_stop_logs"
    trailing_days: int = 10
    horizon_hours: int = 72
    # Gauge considered stale if lake_fresh_sensor hasn't reported within this many minutes.
    # The raw air-gap sensor changes value every ~1-7 min, so 15 min clears normal cadence
    # with headroom while still catching a real hardware dropout quickly. Override via
    # LAKE_RISE_LAKE_STALE_MINUTES.
    lake_stale_minutes: float = 15.0
    # Bucket rain sensors (GW3000B piezo series)
    rain_rate_sensor: str = "sensor.gw3000b_rain_rate_piezo"       # in/hr, current
    rain_daily_sensor: str = "sensor.gw3000b_daily_rain_piezo"     # today
    rain_weekly_sensor: str = "sensor.gw3000b_weekly_rain_piezo"   # current week
    rain_monthly_sensor: str = "sensor.gw3000b_monthly_rain_piezo" # current month
    rain_event_sensor: str = "sensor.gw3000b_event_rain_piezo"     # current event
    # Weather for the lake-evaporation estimate, archived hourly from HA long-term statistics.
    # The carport sensor reads warm: the operator's template corrects the temperature, and the
    # humidity is relative to the RAW reading (so vapor pressure uses the raw temperature).
    air_temp_sensor: str = "sensor.carport_temp_corrected"
    air_temp_raw_sensor: str = "sensor.carport_temp"
    humidity_sensor: str = "sensor.carport_humidity"
    wind_sensor: str = "sensor.gw3000b_wind_speed"
    water_temp_sensor: str = "sensor.lake_temperature"      # ~1 ft deep, near shore, shaded


@dataclass
class LiveConditions:
    """Fully assembled current conditions, ready for bundle construction or direct API response."""
    reading_ft: float               # raw lake depth sensor reading
    stop_log_count: int
    as_of: str                      # ISO timestamp
    rate_in_per_hr: float           # instantaneous rain rate
    today_in: float                 # daily accumulator
    week_in: float                  # weekly accumulator
    month_in: float                 # monthly accumulator
    event_in: float                 # current event accumulator
    older_block_in: float           # the ~20d uniform prepend (month - recent sum)
    trailing_rainfall_in: list[float]   # ~30d hourly series (older_block + recent_hourly)
    forecast_point_in: list[float]
    forecast_pop_frac: list[float]
    has_gaps: bool


def _state_age_hours(state: dict, now: datetime) -> float:
    """Hours since an HA entity last reported. Prefers ``last_reported`` (updates on
    every report, even when the value is unchanged) so a dry-but-healthy gauge reads
    as fresh; falls back to ``last_updated``/``last_changed``. Unknown → very stale."""
    for key in ("last_reported", "last_updated", "last_changed"):
        ts = state.get(key)
        if ts:
            try:
                t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                return (now - t).total_seconds() / 3600.0
            except ValueError:
                continue
    return 1e9


def time_weighted_gauge_average(
    samples: list[tuple[datetime, float]],
    start: datetime,
    end: datetime,
) -> tuple[float | None, int]:
    """Average a change-triggered gauge history over ``[start, end]``.

    Home Assistant history is not uniformly sampled: unchanged values may be sparse while
    a noisy or rapidly changing period has many rows. A plain mean therefore overweights
    transients. Treat readings as piecewise-constant instead, carrying the last pre-window
    value to the boundary. Without that boundary value the full window is unconfirmed.
    """
    valid = sorted(
        ((ts, value) for ts, value in samples if ts <= end and math.isfinite(value)),
        key=lambda item: item[0],
    )
    baseline = next(((ts, value) for ts, value in reversed(valid) if ts <= start), None)
    if baseline is None or end <= start:
        return None, 0

    points = [(start, baseline[1])]
    points.extend((ts, value) for ts, value in valid if start < ts <= end)
    # Collapse identical timestamps, keeping the newest value returned for that instant.
    collapsed: list[tuple[datetime, float]] = []
    for point in points:
        if collapsed and point[0] == collapsed[-1][0]:
            collapsed[-1] = point
        else:
            collapsed.append(point)
    source_count = len(collapsed)
    if collapsed[-1][0] < end:
        collapsed.append((end, collapsed[-1][1]))

    seconds = (end - start).total_seconds()
    area = sum(
        value * (next_ts - ts).total_seconds()
        for (ts, value), (next_ts, _) in zip(collapsed, collapsed[1:])
    )
    return round(area / seconds, 3), source_count


class LiveHASource:
    def __init__(self, art: Artifact, cfg: HAConfig, client: httpx.Client | None = None):
        self.art = art
        self.cfg = cfg
        self._client = client or httpx.Client(
            base_url=cfg.base_url,
            headers={"Authorization": f"Bearer {cfg.token}"},
            timeout=30.0,
        )

    # --- raw HA calls (isolated for mocking) ----------------------------------------

    def _get_state(self, entity_id: str) -> dict:
        r = self._client.get(f"/api/states/{entity_id}")
        r.raise_for_status()
        return r.json()

    def _get_history(self, entity_id: str, start: datetime, end: datetime) -> list[dict]:
        r = self._client.get(
            f"/api/history/period/{start.isoformat()}",
            params={
                "filter_entity_id": entity_id,
                "end_time": end.isoformat(),
                "minimal_response": "true",
                "significant_changes_only": "false",
            },
        )
        r.raise_for_status()
        data = r.json()
        return data[0] if data else []

    def _smoothed_reading(self, now: datetime) -> float:
        """Live lake depth, denoised: the median of the last ~1 h of samples instead of a
        single instantaneous (noisy) reading. Falls back to the current state if the window
        has no usable samples -- including when the history fetch itself fails: smoothing is
        best-effort, so a history outage degrades to the instantaneous reading rather than
        crashing the live prediction (the degrade-don't-crash guardrail rain history follows)."""
        try:
            hist = self._get_history(
                self.cfg.lake_sensor, now - timedelta(hours=LIVE_ANCHOR_WINDOW_HOURS), now)
        except httpx.HTTPError:
            hist = []                      # fall through to the instantaneous state below
        med = backtest.smoothed_anchor_elev(hist, 0.0, now, window_hours=LIVE_ANCHOR_WINDOW_HOURS)
        if med is not None:
            return med
        return float(self._get_state(self.cfg.lake_sensor)["state"])

    def gauge_problem(self, now: datetime) -> str | None:
        """Why the lake gauge can't be trusted right now, or ``None`` if it can. Stale when the
        raw liveness sensor hasn't reported recently; INVALID when the depth sensor itself flags
        its source as not valid (``source_value_valid: false`` -- the ultrasonic has lost the
        water surface, e.g. reading the bottom echo in rain, while still reporting). The smoothed
        sensor then holds its last sample, so an invalid gauge looks alive and flat otherwise."""
        try:
            if _state_age_hours(self._get_state(self.cfg.lake_fresh_sensor), now) > self.cfg.lake_stale_minutes / 60:
                return "gauge liveness is stale"
            state = self._get_state(self.cfg.lake_sensor)
        except httpx.HTTPError:
            return "gauge state is unavailable"
        if (state.get("attributes") or {}).get("source_value_valid") is False:
            return "gauge reading is invalid (no valid surface echo)"
        return None

    def fetch_gauge_observation(self, now: datetime | None = None) -> GaugeObservation:
        """Return the observed-EAP signal: a fresh rolling 15-minute gauge average.

        History plus the instantaneous state are used so the newest value is never omitted.
        When a fresh instantaneous value exists but the history cannot confirm an average,
        it is returned as a degraded fallback; stale/invalid current data is never presented
        as a crossing.
        """
        now = now or datetime.now(timezone.utc).replace(microsecond=0)
        start = now - timedelta(minutes=OBSERVED_AVERAGE_MINUTES)
        history_start = start - timedelta(minutes=OBSERVED_AVERAGE_MINUTES)
        reasons: list[str] = []

        try:
            problem = self.gauge_problem(now)
        except (KeyError, TypeError, ValueError):
            problem = "gauge liveness is stale"
        if problem is not None:
            return GaugeObservation(now, None, False, 0, problem)

        try:
            current_state = self._get_state(self.cfg.lake_sensor)
            current = float(current_state["state"])
            if not math.isfinite(current):
                raise ValueError("non-finite gauge reading")
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return GaugeObservation(now, None, False, 0, "current gauge reading is unavailable")

        try:
            history = parse_ha_rows(self._get_history(self.cfg.lake_sensor, history_start, now))
        except httpx.HTTPError:
            history = []
            reasons.append("15-minute gauge history is unavailable")

        samples = [(ts, value) for ts, value in history if math.isfinite(value)]
        # The state endpoint is the authoritative newest sample. Count it as a second sample
        # only when its report timestamp is distinct from the last history row; two API views
        # of one reading must not accidentally satisfy the two-sample confirmation rule.
        current_ts = None
        for key in ("last_reported", "last_updated", "last_changed"):
            if current_state.get(key):
                try:
                    current_ts = datetime.fromisoformat(
                        str(current_state[key]).replace("Z", "+00:00"))
                    break
                except ValueError:
                    pass
        current_ts = current_ts or now
        if not any(ts == current_ts for ts, _ in samples):
            samples.append((current_ts, current))
        average, sample_count = time_weighted_gauge_average(samples, start, now)
        if average is not None and sample_count >= 2:
            return GaugeObservation(
                detected_at=now,
                gauge_ft=average,
                confirmed=True,
                sample_count=sample_count,
            )

        reasons.append("fewer than two valid samples were available")
        return GaugeObservation(
            detected_at=now,
            gauge_ft=current,
            confirmed=False,
            sample_count=1,
            degraded_reason="; ".join(dict.fromkeys(reasons)),
        )

    def forecast_outlook(self) -> list[dict]:
        """The forecast source's full hourly horizon (Apple WeatherKit: 168 h), untruncated and
        compacted to time / precipitation (in) / PoP (%), for the forecast-of-record log."""
        return [{"datetime": f.get("datetime"), "precipitation_in": f.get("precipitation"),
                 "pop_pct": f.get("precipitation_probability")}
                for f in self._get_forecast(self.cfg.forecast_entity)]

    def _get_forecast(self, entity_id: str) -> list[dict]:
        r = self._client.post(
            "/api/services/weather/get_forecasts",
            params={"return_response": "true"},
            json={"type": "hourly", "entity_id": entity_id},
        )
        r.raise_for_status()
        resp = r.json().get("service_response", {})
        return resp.get(entity_id, {}).get("forecast", [])

    # --- assembly -------------------------------------------------------------------

    def _trailing_rain(self, now: datetime) -> tuple[list[float], bool]:
        """The hindcast's trailing hourly rain: the accumulator's per-hour rise over the last
        ``trailing_days``, ending at ``floor_hour(now)``. The window starts on the hour so HA's
        start-state row is the first hour's baseline. Returns ``(series, degraded)``; ``degraded``
        means the history couldn't be retrieved, or the accumulator was unavailable for
        ``RAIN_GAP_DEGRADED_HOURS`` or more (those hours drive the model as 0.0)."""
        start = floor_hour(now - timedelta(days=self.cfg.trailing_days))
        try:
            states = parse_ha_states(self._get_history(self.cfg.rain_sensor, start, now))
        except httpx.HTTPError:
            states = []                    # degrade, don't crash the whole prediction
        if not any(v is not None for _, v in states):
            return [0.0] * len(hour_grid(start, now)), True
        hourly = rain_hourly_from_daily_accumulator(states, start, now)
        gaps = _rain_gap_hours(states, start, hourly)
        return [0.0 if r is None else r for r in hourly], gaps >= RAIN_GAP_DEGRADED_HOURS

    def _stop_log_count(self, now: datetime) -> int:
        if self.cfg.stop_log_helper:
            try:
                st = self._get_state(self.cfg.stop_log_helper)["state"]
                count = int(round(float(st)))
                if 0 <= count <= self.art.stop_logs.max_count:
                    return count
            except (KeyError, ValueError, httpx.HTTPError):
                pass  # fall back to the date-based default (also for an out-of-range value)
        return default_stop_log_count(self.art.stop_logs, now.month, now.day)

    def fetch_snapshot(self, lake_reading_ft: float | None = None) -> Snapshot:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        # `as_of` anchors the model timeline and MUST sit on an hour boundary: the trailing
        # series is bucketed on clock hours (`_trailing_rain` -> `hour_grid`), and
        # `predict` derives `hind_start = as_of - len(trailing)h` and runs the forecast from
        # `as_of`. `model.run` applies rain hour i over [start+i, start+i+1], so alignment only
        # holds when `as_of` is floored -- otherwise every hindcast hour lands at the wrong
        # sub-hour offset and forecast points are stamped :mm past the hour (skew-critical).
        # `now` stays real for the gauge anchor, staleness, and history-fetch windows below.
        as_of = floor_hour(now)

        reading = self._smoothed_reading(now) if lake_reading_ft is None else lake_reading_ft

        trailing, rain_degraded = self._trailing_rain(now)
        # rainfall_has_gaps = an ACTUAL failure of the driving data, not a proxy: the rain
        # history couldn't be retrieved or the accumulator was unavailable for a real stretch
        # (a dry-but-healthy accumulator just holds its value, so this is NOT confounded with
        # dry weather); OR the lake gauge is genuinely stale. Either degrades the state
        # estimate, so the predictor floors the spun-up state at the seasonal normal (#4).
        rain_fetch_failed = rain_degraded
        try:
            lake_stale = self.gauge_problem(now) is not None   # stale OR invalid gauge
        except httpx.HTTPError:
            lake_stale = True
        has_gaps = rain_fetch_failed or lake_stale

        try:
            fc = self._get_forecast(self.cfg.forecast_entity)[: self.cfg.horizon_hours]
        except httpx.HTTPError:
            fc = []              # degrade, don't crash; a missing forecast un-models incoming
        if not fc:               # failed call OR an entity that returned nothing (renamed?):
            has_gaps = True      # degraded input, never a "dry" forecast -> predictor floors state
        point = [float(f.get("precipitation") or 0.0) for f in fc]
        pop = [float(f.get("precipitation_probability") or 0.0) / 100.0 for f in fc]

        return Snapshot(
            as_of=as_of.isoformat(),
            lake_depth_reading_ft=reading,
            stop_log_count=self._stop_log_count(now),
            trailing_rainfall_in=trailing,
            rainfall_has_gaps=has_gaps,
            forecast_point_in=point,
            forecast_pop_frac=pop,
            noaa_high_total_in=None,
        )

    def fetch_conditions(self) -> LiveConditions:
        """Pull lake reading, all bucket rain states, hourly accumulator history, and
        Apple WeatherKit forecast into a single LiveConditions struct.

        Trailing series construction: the recent ~10d hourly series comes from the
        rolling accumulator history; an older block (~20d uniform) is prepended to
        approximate the last ~30d total, using the monthly accumulator minus the
        recent sum. This avoids any statistics endpoint or WebSocket subscription."""
        now = datetime.now(timezone.utc).replace(microsecond=0)
        as_of = floor_hour(now)  # hour-boundary anchor for the model timeline (see fetch_snapshot)

        reading = self._smoothed_reading(now)

        # --- bucket rain states ---------------------------------------------------
        def _bucket(entity_id: str) -> float:
            try:
                return float(self._get_state(entity_id)["state"])
            except (KeyError, ValueError, httpx.HTTPError):
                return 0.0

        try:
            rate_state = self._get_state(self.cfg.rain_rate_sensor)
            rate_in_per_hr = float(rate_state.get("state"))
        except (KeyError, ValueError, TypeError, httpx.HTTPError):
            rate_in_per_hr = 0.0
        # Lake-gauge liveness (one half of the degraded-data signal); the rain-retrieval
        # failure half is OR'd in after the rain history is fetched below.
        try:
            lake_stale = self.gauge_problem(now) is not None   # stale OR invalid gauge
        except httpx.HTTPError:
            lake_stale = True

        today_in = _bucket(self.cfg.rain_daily_sensor)
        week_in = _bucket(self.cfg.rain_weekly_sensor)
        month_in = _bucket(self.cfg.rain_monthly_sensor)
        event_in = _bucket(self.cfg.rain_event_sensor)

        # --- recent hourly series (~10d) ------------------------------------------
        recent_hourly, rain_degraded = self._trailing_rain(now)
        # Rain history unretrievable / accumulator unavailable for a real stretch (not dry
        # weather); OR the lake gauge is stale. Either degrades the state estimate -> floored.
        has_gaps = rain_degraded or lake_stale

        # --- older block (~20d uniform prepend) -----------------------------------
        # Use monthly total to estimate what happened in the 20d before the 10d window.
        # If the monthly read failed (0), just use the recent window.
        older_block_in: float = 0.0
        older_block: list[float] = []
        if month_in > 0:
            older_total = max(0.0, month_in - sum(recent_hourly))
            older_hours = 20 * 24
            per_hour = older_total / older_hours
            older_block = [per_hour] * older_hours
            older_block_in = older_total

        trailing_rainfall_in = older_block + recent_hourly

        # --- forecast -------------------------------------------------------------
        try:
            fc = self._get_forecast(self.cfg.forecast_entity)[: self.cfg.horizon_hours]
        except httpx.HTTPError:
            fc = []              # degrade, don't crash; a missing forecast un-models incoming
        if not fc:               # failed call OR an entity that returned nothing (renamed?):
            has_gaps = True      # degraded input, never a "dry" forecast -> predictor floors state
        point = [float(f.get("precipitation") or 0.0) for f in fc]
        pop = [float(f.get("precipitation_probability") or 0.0) / 100.0 for f in fc]

        return LiveConditions(
            reading_ft=reading,
            stop_log_count=self._stop_log_count(now),
            as_of=as_of.isoformat(),
            rate_in_per_hr=rate_in_per_hr,
            today_in=today_in,
            week_in=week_in,
            month_in=month_in,
            event_in=event_in,
            older_block_in=older_block_in,
            trailing_rainfall_in=trailing_rainfall_in,
            forecast_point_in=point,
            forecast_pop_frac=pop,
            has_gaps=has_gaps,
        )

    def build_bundle(self, lake_reading_ft: float | None = None) -> InputBundle:
        bundle = bundle_from_snapshot(self.art, self.fetch_snapshot(lake_reading_ft=lake_reading_ft))
        return self.with_archive_spinup(bundle, bundle.trailing_rainfall_in)

    def with_archive_spinup(self, bundle: InputBundle, recent_rain: list[float]) -> InputBundle:
        """``bundle`` with its watershed state spun up from the archived true rain
        (``archive_spinup``) -- unless the trailing rain is degraded, in which case the predictor's
        seasonal-floored hindcast (#4) stays in charge. ``recent_rain`` is this pull's measured
        hourly rain ending at ``bundle.as_of`` (not any synthetic prepend)."""
        if bundle.rainfall_has_gaps:
            return bundle
        sp = self.archive_spinup(bundle.as_of, recent_rain)
        return bundle if sp is None else bundle.model_copy(update={"spinup_state": sp})

    def _archived_evaporation(self, start: datetime, end: datetime) -> list[float | None] | None:
        """Lake-evaporation rate per hour over ``[start, end)`` from the archived weather
        (``evaporation.hourly_rates``); ``None`` -- the monthly table throughout -- when the
        archive is unreadable or the artifact has no site constants."""
        if self.art.lake_evaporation_model is None:
            return None
        try:
            from .. import evaporation
            from ..calibration import archive

            # Whole local days either side, so the daily means/ranges are complete.
            rec = archive.load_window(start - timedelta(days=1), end + timedelta(days=1))
            hours = max(0, int((floor_hour(end) - start).total_seconds() // 3600))
            return evaporation.hourly_rates(self.art, rec.samples, start, hours)
        except Exception:  # noqa: BLE001 -- the table is a safe fallback
            return None

    def archive_spinup(self, as_of: datetime, recent_rain: list[float]) -> SpinupState | None:
        """Watershed state at ``as_of`` from replaying the continuous archive's true rain
        (``antecedent.replay_subsurface``), topped up with ``recent_rain`` (hourly, ending at
        ``as_of``) so the newest hours the hourly archive job hasn't stored yet are included.
        ``None`` -- keep the short hindcast -- when the archive is unreadable or too short."""
        try:
            from .. import antecedent
            from ..calibration import archive

            cap_days = (antecedent.groundwater_half_life_days(self.art)
                        * antecedent.DEFAULT_CAP_HALF_LIVES)
            rec = archive.load_window(as_of - timedelta(days=cap_days + 2), as_of)
            by_hour = {s.hour: s for s in rec.samples}
            t0 = as_of - timedelta(hours=len(recent_rain))
            for i, r in enumerate(recent_rain):
                h = (t0 + timedelta(hours=i)).isoformat()
                prev = by_hour.get(h)
                if prev is not None and prev.rain_in is not None:
                    continue    # the archive's value (possibly statistics-repaired) wins; the
                                # live series drives unavailable hours as 0.0
                by_hour[h] = archive.HourSample(hour=h, rain_in=r,
                                                elev_ft=prev.elev_ft if prev else None)
            merged = archive.ContinuousRecord(samples=[by_hour[k] for k in sorted(by_hour)])
            out = antecedent.replay_subsurface(self.art, merged, as_of)
            if out is None:
                return None
            st, evidence = out
            return spinup_from_state(st, evidence["history_days"])
        except Exception:  # noqa: BLE001 -- a bad/missing archive must not break a prediction
            return None

    def _backtest_inputs(self, hours_back: int, stop_log_count: int | None = None) -> dict:
        """Pull the real observations a backtest needs over the past ``hours_back`` hours:
        trailing+forward rain, the observed hourly gauge, the T0 anchor window, and the
        control elevation. Shared by ``fetch_backtest`` (which scores immediately) and
        ``capture_storm`` (which freezes these inputs to a StormRecord for offline replay).

        T0 = now - hours_back. Rain covers the full trailing spin-up window plus the forward
        window (trailing_days total). Lake depth is fetched from T0-2h to now to anchor at T0.
        """
        now = datetime.now(timezone.utc).replace(microsecond=0)
        t0 = now - timedelta(hours=hours_back)

        # --- rainfall: full trailing window for spin-up + forward --------------
        # Per-hour increase of the daily accumulator (true hourly rain; see
        # rain_hourly_from_daily_accumulator). run_backtest / StormRecord need a complete series,
        # so an unavailable hour drives the model as 0.0 -- counted in rain_gap_hours so a
        # capture records it rather than passing it off as observed-dry.
        rain_start = floor_hour(now - timedelta(days=self.cfg.trailing_days))
        raw_rain = self._get_history(self.cfg.rain_sensor, rain_start, now)
        rain_states = parse_ha_states(raw_rain)
        rain_with_gaps = rain_hourly_from_daily_accumulator(rain_states, rain_start, now)
        rain_hourly = [0.0 if r is None else r for r in rain_with_gaps]
        rain_gap_hours = _rain_gap_hours(rain_states, rain_start, rain_with_gaps)

        # --- lake level: T0-2h to now. level_history_to_hourly takes the per-hour
        # median, so the anchor (the hour containing T0) is already denoised. ----
        offset = self.art.datum.sensor_to_absolute_offset_ft
        lake_start = t0 - timedelta(hours=2)
        raw_lake = self._get_history(self.cfg.lake_sensor, lake_start, now)
        level_by_hour = backtest.level_history_to_hourly(raw_lake, offset)

        # --- control elevation: caller override, else seasonal default at T0 ----
        count = (stop_log_count if stop_log_count is not None
                 else default_stop_log_count(self.art.stop_logs, t0.month, t0.day))
        control_elev = control_elev_for_stop_logs(self.art.stop_logs, count)

        # --- data freshness: has the raw liveness sensor reported within the window? ---
        # Keyed off lake_fresh_sensor (not the rounded lake_sensor): the smoothed depth can
        # hold steady for 30+ min in calm water and look stale though the gauge is healthy.
        data_fresh = False
        try:
            data_fresh = self.gauge_problem(now) is None
        except Exception:  # noqa: BLE001
            data_fresh = False

        # --- T0 watershed state: the same archive replay the live forecast uses at its issue
        # time (topped up with this pull's rain before T0), so a backtest scores the predictor as
        # it actually runs. None -> run_backtest's short seasonal-seed hindcast (the live fallback).
        t0_hour = floor_hour(t0)
        pre_t0 = rain_hourly[:max(0, int((t0_hour - rain_start).total_seconds() // 3600))]
        spinup_state = self.archive_spinup(t0_hour, pre_t0)
        lake_evap = self._archived_evaporation(t0_hour, now)

        return {
            "rain_hourly": rain_hourly, "rain_start": rain_start, "level_by_hour": level_by_hour,
            "t0": t0, "now": now, "control_elev": control_elev,
            "stop_log_count": count, "data_fresh": data_fresh, "rain_gap_hours": rain_gap_hours,
            "spinup_state": spinup_state, "lake_evap_in_per_day": lake_evap,
        }

    def fetch_backtest(self, hours_back: int, stop_log_count: int | None = None) -> dict:
        """Pull real history and run a backtest over the past ``hours_back`` hours."""
        inp = self._backtest_inputs(hours_back, stop_log_count)
        sp = inp["spinup_state"]
        state0 = spinup_to_state(self.art, sp, 0.0) if sp is not None else None  # h <- gauge at T0
        result = backtest.run_backtest(
            self.art, inp["rain_hourly"], inp["rain_start"], inp["level_by_hour"],
            inp["t0"], inp["now"], inp["control_elev"], state0=state0,
            lake_evap_in_per_day=inp["lake_evap_in_per_day"],
        )
        return {**result, "stop_log_count": inp["stop_log_count"], "data_fresh": inp["data_fresh"]}

    def capture_storm(self, hours_back: int, label: str, notes: str = "",
                      stop_log_count: int | None = None) -> "StormRecord":
        """Freeze the past ``hours_back`` hours of real observations into a StormRecord for
        offline backtesting. Capture promptly after a storm, while the ~10-day raw
        HA history still covers the window."""
        from ..storm_record import StormRecord

        inp = self._backtest_inputs(hours_back, stop_log_count)
        if inp["rain_gap_hours"]:
            gap_note = f"{inp['rain_gap_hours']} h of rain unavailable (driven as 0.0)"
            notes = f"{notes}; {gap_note}" if notes else gap_note
        return StormRecord(
            label=label, captured_at=inp["now"].isoformat(), source="live_ha", notes=notes,
            data_fresh=inp["data_fresh"],
            rain_start=inp["rain_start"].isoformat(), rain_hourly=list(inp["rain_hourly"]),
            level_by_hour={k.isoformat(): round(v, 3) for k, v in inp["level_by_hour"].items()},
            t0=inp["t0"].isoformat(), now=inp["now"].isoformat(),
            control_elev=inp["control_elev"], stop_log_count=inp["stop_log_count"],
            spinup_state=inp["spinup_state"], lake_evap_in_per_day=inp["lake_evap_in_per_day"],
        )

    def continuous_samples(self, days: int | None = None) -> list:
        """Pull the trailing window's observed hourly gauge + rain as archive samples, so an
        hourly job can append them into the rolling continuous record (recessions + long-term
        continuity the signature extractors need). ``days`` overrides the pull window -- raise it
        for a one-shot reconcile that backfills more of HA's retention; the gap-aware fillers +
        gap-preserving merge fill recoverable holes and leave genuinely-missing hours as None."""
        from ..calibration.archive import samples_from_backtest_inputs
        from ..hourly import held_value_hourly, level_hourly_gapaware

        now = datetime.now(timezone.utc).replace(microsecond=0)
        start = floor_hour(now - timedelta(days=days or self.cfg.trailing_days))
        parsed_rain = parse_ha_states(self._get_history(self.cfg.rain_sensor, start, now))
        parsed_lake = parse_ha_states(self._get_history(self.cfg.lake_raw_sensor, start, now))
        # HA's history always opens with the state held at `start`, so even a dry spell yields a
        # numeric row for the accumulator; an empty/all-unavailable response is data-missing, NOT
        # dryness. Within the window the accumulator transform marks unavailable hours `None`
        # (missing), never fake zeros -- but a wholly empty fetch is a systemic failure: fail
        # closed rather than archive an all-missing window.
        if not any(v is not None for _, v in parsed_rain):
            raise RuntimeError(
                f"rain history for {self.cfg.rain_sensor} returned no usable samples over "
                f"{start.isoformat()}..{now.isoformat()}; refusing to archive (a fetch failure must "
                "not be recorded as observations)")
        if not any(v is not None for _, v in parsed_lake):
            raise RuntimeError(
                f"lake history for {self.cfg.lake_raw_sensor} returned no usable readings over "
                f"{start.isoformat()}..{now.isoformat()}; refusing to archive a window with no "
                "elevation signal")
        offset = self.art.datum.sensor_to_absolute_offset_ft
        rain_hourly = rain_hourly_from_daily_accumulator(parsed_rain, start, now)
        level_list = level_hourly_gapaware(parsed_lake, offset, start, now)
        level_by_hour = dict(zip(hour_grid(start, now), level_list))
        samples = samples_from_backtest_inputs(rain_hourly, start, level_by_hour)
        if self.cfg.stop_log_helper:
            try:
                parsed = parse_ha_states(self._get_history(self.cfg.stop_log_helper, start, now))
            except httpx.HTTPError:
                parsed = []         # the count is optional context; never block the archive
            counts = dict(zip(hour_grid(start, now), held_value_hourly(parsed, start, now)))
            for s in samples:
                c = counts.get(datetime.fromisoformat(s.hour))
                if c is not None and 0 <= round(c) <= self.art.stop_logs.max_count:
                    s.stop_log_count = int(round(c))
        return samples

    def statistics_rain_samples(self, days: int) -> list:
        """Hourly rain for the trailing ``days`` from HA's *long-term statistics* of the daily
        accumulator -- kept permanently, unlike the ~10-day raw history -- as rain-only archive
        samples (``elev_ft`` None, so the gap-preserving merge leaves archived levels untouched).
        This is the backfill path: the hourly ``change`` equals the accumulator diff
        ``continuous_samples`` computes. Hours with no statistic stay ``None`` (missing)."""
        from ..calibration.archive import HourSample
        from .ha_statistics import hourly_change

        now = datetime.now(timezone.utc).replace(microsecond=0)
        start = floor_hour(now - timedelta(days=days))
        end = floor_hour(now)
        rain = hourly_change(self._get_statistics(self.cfg.rain_sensor, start, end), start, end)
        return [HourSample(hour=h.isoformat(), rain_in=r) for h, r in zip(hour_grid(start, end), rain)]

    def statistics_met_samples(self, days: int) -> list:
        """Hourly weather for the trailing ``days`` from HA's long-term statistics (hourly means),
        as weather-only archive samples (level and rain ``None``, so the merge leaves them alone).
        Values outside a physical range -- a disconnected sensor's sentinel, such as the old lake
        probe's -40 F -- are stored as missing."""
        from ..calibration.archive import HourSample
        from .ha_statistics import hourly_mean

        now = datetime.now(timezone.utc).replace(microsecond=0)
        start = floor_hour(now - timedelta(days=days))
        end = floor_hour(now)
        fields = {  # archive field: (entity, valid range)
            "air_temp_f": (self.cfg.air_temp_sensor, MET_VALID_RANGE["air_temp_f"]),
            "air_temp_raw_f": (self.cfg.air_temp_raw_sensor, MET_VALID_RANGE["air_temp_f"]),
            "rh_pct": (self.cfg.humidity_sensor, MET_VALID_RANGE["rh_pct"]),
            "wind_mph": (self.cfg.wind_sensor, MET_VALID_RANGE["wind_mph"]),
            "water_temp_f": (self.cfg.water_temp_sensor, MET_VALID_RANGE["water_temp_f"]),
        }
        rows = self._get_statistics_many([e for e, _ in fields.values()], start, end, ("mean",))
        series = {f: hourly_mean(rows.get(e, []), start, end, valid=v) for f, (e, v) in fields.items()}
        return [HourSample(hour=h.isoformat(), **{f: series[f][i] for f in fields})
                for i, h in enumerate(hour_grid(start, end))]

    def _get_statistics_many(self, statistic_ids: list[str], start: datetime, end: datetime,
                             types: tuple[str, ...]) -> dict[str, list[dict]]:
        """HA long-term hourly statistics for several entities (websocket; isolated for mocking)."""
        from .ha_statistics import statistics_for_ids

        return statistics_for_ids(self.cfg.base_url, self.cfg.token, statistic_ids, start, end, types)

    def _get_statistics(self, statistic_id: str, start: datetime, end: datetime) -> list[dict]:
        """HA long-term hourly statistics rows (websocket API; isolated for mocking)."""
        from .ha_statistics import statistics_during_period

        return statistics_during_period(self.cfg.base_url, self.cfg.token, statistic_id, start, end)
