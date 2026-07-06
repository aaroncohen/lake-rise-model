"""Live Home Assistant REST source. Implements the same DataSource protocol as the
fixture source, so the predictor and API are unchanged whether data is snapshotted or
pulled live. Read-only: GET states/history + a read-only weather.get_forecasts call.

Config (entities) defaults to the real Crystal Lake setup; credentials come from the
caller (see settings.py). Network calls are isolated in small methods and go through an
injectable httpx.Client so they can be mocked in tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from .. import backtest
from ..artifact import Artifact
from ..bundle import InputBundle
from ..geometry import control_elev_for_stop_logs, default_stop_log_count
from ..hourly import floor_hour, hour_grid, parse_ha_rows
from .snapshot import Snapshot, bundle_from_snapshot

# Trailing window for denoising the LIVE "now" lake anchor (there's no completed hourly
# bucket to median over yet). 30 min is the knee of the noise-vs-lag curve on real data:
# residual anchor noise ~0.21 in (negligible for the forecast start and freeboard) while
# the worst-case lag on a fast storm rise is only ~window/2 = 15 min. A trailing median
# lags a monotonic rise by ~half the window, so keep this short for the early-warning path.
LIVE_ANCHOR_WINDOW_HOURS = 0.5


@dataclass
class HAConfig:
    base_url: str                      # e.g. http://homeassistant.local:8123
    token: str                         # long-lived access token
    lake_sensor: str = "sensor.crystal_lake_depth_smoothed"
    # Liveness is judged from this raw, least-rounded sensor — NOT lake_sensor. The depth
    # chain (gw3000b_air_gap → air_gap_filtered → crystal_lake_depth → ..._smoothed) rounds
    # to 0.01 ft, which swallows the sub-rounding air-gap noise, so the smoothed value can
    # legitimately hold for 30+ min in calm water and look "stale" even though the gauge is
    # healthy. The raw air-gap sensor still moves every ~1-7 min, so it's an honest
    # hardware-liveness signal. Override via LAKE_RISE_LAKE_FRESH_SENSOR.
    lake_fresh_sensor: str = "sensor.gw3000b_air_gap_1"
    rain_sensor: str = "sensor.gw3000b_hourly_rain_piezo"
    forecast_entity: str = "weather.47_77849_122_10882"   # Apple WeatherKit (preferred)
    stop_log_helper: str | None = None  # optional input_number; else date-based default
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


def hourly_from_accumulator(states: list[tuple[datetime, float]], start: datetime,
                            end: datetime) -> tuple[list[float], bool]:
    """Convert the gauge's within-hour rolling accumulator into per-clock-hour totals.

    The gw3000b hourly piezo value ramps up during an hour then resets, so the hour's
    peak ~= that hour's rainfall. We bucket by clock hour, take the max, and fill the
    contiguous hour grid with zeros. APPROXIMATE — good enough for soil-moisture spin-up;
    a true hourly statistic is a later enhancement. Returns (series, has_gaps)."""
    by_hour: dict[datetime, float] = {}
    for ts, val in states:
        hour = floor_hour(ts)
        by_hour[hour] = max(by_hour.get(hour, 0.0), val)

    grid = hour_grid(start, end)
    n = len(grid)
    series = [by_hour.get(h, 0.0) for h in grid]

    # Sparse-coverage heuristic. NOTE: this is DRY-CONFOUNDED and must NOT be used as an
    # outage/gap signal for safety logic -- the HA recorder only stores rows on value
    # change, so a dry-but-healthy accumulator (flat at 0) produces few records and looks
    # identical to a real outage. Callers discard it; genuine "data missing" is detected
    # from actual retrieval failures at fetch time (empty history / HTTP error), not here.
    # Retained only as a coarse diagnostic. See the 2026-07-03 #4 calibration-log entry.
    sparse_records = n > 0 and (len(by_hour) / n) < 0.25
    return series, sparse_records


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
        has no usable samples."""
        hist = self._get_history(
            self.cfg.lake_sensor, now - timedelta(hours=LIVE_ANCHOR_WINDOW_HOURS), now)
        med = backtest.smoothed_anchor_elev(hist, 0.0, now, window_hours=LIVE_ANCHOR_WINDOW_HOURS)
        if med is not None:
            return med
        return float(self._get_state(self.cfg.lake_sensor)["state"])

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

    def _stop_log_count(self, now: datetime) -> int:
        if self.cfg.stop_log_helper:
            try:
                st = self._get_state(self.cfg.stop_log_helper)["state"]
                return int(round(float(st)))
            except (KeyError, ValueError, httpx.HTTPError):
                pass  # fall back to the date-based default
        return default_stop_log_count(self.art.stop_logs, now.month, now.day)

    def fetch_snapshot(self) -> Snapshot:
        now = datetime.now(timezone.utc).replace(microsecond=0)

        reading = self._smoothed_reading(now)

        start = now - timedelta(days=self.cfg.trailing_days)
        try:
            raw = self._get_history(self.cfg.rain_sensor, start, now)
        except httpx.HTTPError:
            raw = []                       # degrade, don't crash the whole prediction
        parsed = parse_ha_rows(raw)
        trailing, _ = hourly_from_accumulator(parsed, start, now)  # coverage flag discarded (dry-confounded)
        # rainfall_has_gaps = an ACTUAL failure to retrieve the driving data, not a proxy.
        # No usable rain record over the whole trailing window is a real retrieval failure
        # (a dry-but-healthy sensor still returns >=1 record, so this is NOT confounded with
        # dry weather); OR the lake gauge is genuinely stale. Either degrades the state
        # estimate, so the predictor floors the spun-up state at the seasonal normal (#4).
        rain_fetch_failed = not parsed
        try:
            lake_stale = _state_age_hours(self._get_state(self.cfg.lake_fresh_sensor), now) > self.cfg.lake_stale_minutes / 60
        except httpx.HTTPError:
            lake_stale = True
        has_gaps = rain_fetch_failed or lake_stale

        fc = self._get_forecast(self.cfg.forecast_entity)[: self.cfg.horizon_hours]
        point = [float(f.get("precipitation") or 0.0) for f in fc]
        pop = [float(f.get("precipitation_probability") or 0.0) / 100.0 for f in fc]

        return Snapshot(
            as_of=now.isoformat(),
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
            lake_stale = _state_age_hours(self._get_state(self.cfg.lake_fresh_sensor), now) > self.cfg.lake_stale_minutes / 60
        except httpx.HTTPError:
            lake_stale = True

        today_in = _bucket(self.cfg.rain_daily_sensor)
        week_in = _bucket(self.cfg.rain_weekly_sensor)
        month_in = _bucket(self.cfg.rain_monthly_sensor)
        event_in = _bucket(self.cfg.rain_event_sensor)

        # --- recent hourly series (~10d) ------------------------------------------
        start = now - timedelta(days=self.cfg.trailing_days)
        try:
            raw = self._get_history(self.cfg.rain_sensor, start, now)
        except httpx.HTTPError:
            raw = []                       # degrade, don't crash
        parsed = parse_ha_rows(raw)
        recent_hourly, _ = hourly_from_accumulator(parsed, start, now)  # coverage flag discarded
        # No usable rain record over the window = a real retrieval failure (not dry weather);
        # OR the lake gauge is stale. Either degrades the state estimate -> predictor floors it.
        has_gaps = (not parsed) or lake_stale

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
        fc = self._get_forecast(self.cfg.forecast_entity)[: self.cfg.horizon_hours]
        point = [float(f.get("precipitation") or 0.0) for f in fc]
        pop = [float(f.get("precipitation_probability") or 0.0) / 100.0 for f in fc]

        return LiveConditions(
            reading_ft=reading,
            stop_log_count=self._stop_log_count(now),
            as_of=now.isoformat(),
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

    def build_bundle(self) -> InputBundle:
        return bundle_from_snapshot(self.art, self.fetch_snapshot())

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
        rain_start = floor_hour(now - timedelta(days=self.cfg.trailing_days))
        raw_rain = self._get_history(self.cfg.rain_sensor, rain_start, now)
        parsed_rain = parse_ha_rows(raw_rain)
        rain_hourly, _ = hourly_from_accumulator(parsed_rain, rain_start, now)

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
            data_fresh = _state_age_hours(self._get_state(self.cfg.lake_fresh_sensor), now) <= self.cfg.lake_stale_minutes / 60
        except Exception:  # noqa: BLE001
            data_fresh = False

        return {
            "rain_hourly": rain_hourly, "rain_start": rain_start, "level_by_hour": level_by_hour,
            "t0": t0, "now": now, "control_elev": control_elev,
            "stop_log_count": count, "data_fresh": data_fresh,
        }

    def fetch_backtest(self, hours_back: int, stop_log_count: int | None = None) -> dict:
        """Pull real history and run a backtest over the past ``hours_back`` hours."""
        inp = self._backtest_inputs(hours_back, stop_log_count)
        result = backtest.run_backtest(
            self.art, inp["rain_hourly"], inp["rain_start"], inp["level_by_hour"],
            inp["t0"], inp["now"], inp["control_elev"],
        )
        return {**result, "stop_log_count": inp["stop_log_count"], "data_fresh": inp["data_fresh"]}

    def capture_storm(self, hours_back: int, label: str, notes: str = "",
                      stop_log_count: int | None = None) -> "StormRecord":
        """Freeze the past ``hours_back`` hours of real observations into a StormRecord for
        offline backtesting. Capture promptly after a storm, while the ~10-day raw
        HA history still covers the window."""
        from ..storm_record import StormRecord

        inp = self._backtest_inputs(hours_back, stop_log_count)
        return StormRecord(
            label=label, captured_at=inp["now"].isoformat(), source="live_ha", notes=notes,
            data_fresh=inp["data_fresh"],
            rain_start=inp["rain_start"].isoformat(), rain_hourly=list(inp["rain_hourly"]),
            level_by_hour={k.isoformat(): round(v, 3) for k, v in inp["level_by_hour"].items()},
            t0=inp["t0"].isoformat(), now=inp["now"].isoformat(),
            control_elev=inp["control_elev"], stop_log_count=inp["stop_log_count"],
        )

    def continuous_samples(self) -> list:
        """Pull the trailing window's observed hourly gauge + rain as archive samples, so an
        hourly job can append them into the rolling continuous record (recessions + long-term
        continuity the signature extractors need)."""
        from ..calibration.archive import samples_from_backtest_inputs

        now = datetime.now(timezone.utc).replace(microsecond=0)
        start = floor_hour(now - timedelta(days=self.cfg.trailing_days))
        raw_rain = self._get_history(self.cfg.rain_sensor, start, now)
        parsed = parse_ha_rows(raw_rain)
        # A dry-but-healthy accumulator still reports rows (flat values that parse), so `parsed` is
        # non-empty on a real dry spell. An empty/unparseable response is data-missing, NOT dryness:
        # `hourly_from_accumulator` would fabricate an all-zero series that append_samples then records
        # as a fake dry spell, poisoning the recession/BFI signatures. Fail closed instead of silently
        # archiving a fetch gap as zero rain (the module contract in `hourly_from_accumulator`).
        if not parsed:
            raise RuntimeError(
                f"rain history for {self.cfg.rain_sensor} returned no usable samples over "
                f"{start.isoformat()}..{now.isoformat()}; refusing to archive an all-zero rain window "
                "(a fetch gap must not be recorded as a dry spell)")
        rain_hourly, _ = hourly_from_accumulator(parsed, start, now)
        raw_lake = self._get_history(self.cfg.lake_sensor, start, now)
        level_by_hour = backtest.level_history_to_hourly(
            raw_lake, self.art.datum.sensor_to_absolute_offset_ft)
        if not level_by_hour:
            raise RuntimeError(
                f"lake history for {self.cfg.lake_sensor} returned no usable readings over "
                f"{start.isoformat()}..{now.isoformat()}; refusing to archive a window with no "
                "elevation signal")
        return samples_from_backtest_inputs(rain_hourly, start, level_by_hour)
