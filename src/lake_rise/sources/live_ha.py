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
from .snapshot import Snapshot, bundle_from_snapshot


@dataclass
class HAConfig:
    base_url: str                      # e.g. http://homeassistant.local:8123
    token: str                         # long-lived access token
    lake_sensor: str = "sensor.crystal_lake_depth_smoothed"
    rain_sensor: str = "sensor.gw3000b_hourly_rain_piezo"
    forecast_entity: str = "weather.47_77849_122_10882"   # Apple WeatherKit (preferred)
    stop_log_helper: str | None = None  # optional input_number; else date-based default
    trailing_days: int = 10
    horizon_hours: int = 72
    stale_after_hours: float = 3.0      # gauge considered stale if it hasn't reported within this
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
        hour = ts.replace(minute=0, second=0, microsecond=0)
        by_hour[hour] = max(by_hour.get(hour, 0.0), val)

    h0 = start.replace(minute=0, second=0, microsecond=0)
    h1 = end.replace(minute=0, second=0, microsecond=0)
    n = max(0, int((h1 - h0).total_seconds() // 3600))
    series = [by_hour.get(h0 + timedelta(hours=i), 0.0) for i in range(n)]

    # Heuristic gap flag: very sparse coverage suggests recorder/sensor outage, not dry.
    has_gaps = n > 0 and (len(by_hour) / n) < 0.25
    return series, has_gaps


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

        reading = float(self._get_state(self.cfg.lake_sensor)["state"])

        start = now - timedelta(days=self.cfg.trailing_days)
        raw = self._get_history(self.cfg.rain_sensor, start, now)
        parsed: list[tuple[datetime, float]] = []
        for s in raw:
            try:
                ts = datetime.fromisoformat(s["last_changed"].replace("Z", "+00:00"))
                parsed.append((ts, float(s["state"])))
            except (KeyError, ValueError):
                continue  # skip unknown/unavailable
        trailing, _ = hourly_from_accumulator(parsed, start, now)
        # Freshness from the gauge's recency, not rain-hour coverage (dry != gap).
        try:
            has_gaps = _state_age_hours(self._get_state(self.cfg.rain_rate_sensor), now) > self.cfg.stale_after_hours
        except httpx.HTTPError:
            has_gaps = True

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

        reading = float(self._get_state(self.cfg.lake_sensor)["state"])

        # --- bucket rain states ---------------------------------------------------
        def _bucket(entity_id: str) -> float:
            try:
                return float(self._get_state(entity_id)["state"])
            except (KeyError, ValueError, httpx.HTTPError):
                return 0.0

        # Rate sensor read in full so we can judge freshness from its recency, not from
        # how many hours had rain (dry weather is not a gap).
        try:
            rate_state = self._get_state(self.cfg.rain_rate_sensor)
            rate_in_per_hr = float(rate_state.get("state"))
        except (KeyError, ValueError, TypeError, httpx.HTTPError):
            rate_state, rate_in_per_hr = {}, 0.0
        has_gaps = _state_age_hours(rate_state, now) > self.cfg.stale_after_hours

        today_in = _bucket(self.cfg.rain_daily_sensor)
        week_in = _bucket(self.cfg.rain_weekly_sensor)
        month_in = _bucket(self.cfg.rain_monthly_sensor)
        event_in = _bucket(self.cfg.rain_event_sensor)

        # --- recent hourly series (~10d) ------------------------------------------
        start = now - timedelta(days=self.cfg.trailing_days)
        raw = self._get_history(self.cfg.rain_sensor, start, now)
        parsed: list[tuple[datetime, float]] = []
        for s in raw:
            try:
                ts = datetime.fromisoformat(s["last_changed"].replace("Z", "+00:00"))
                parsed.append((ts, float(s["state"])))
            except (KeyError, ValueError):
                continue
        recent_hourly, _ = hourly_from_accumulator(parsed, start, now)

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

    def fetch_backtest(self, hours_back: int) -> dict:
        """Pull real rainfall and lake-level history and run a backtest over
        the past ``hours_back`` hours.

        T0 = now - hours_back. Rain covers the full trailing spin-up window
        plus the forward window (trailing_days total). Lake depth is fetched
        from T0-2h to now so we can anchor the model at T0.
        """
        now = datetime.now(timezone.utc).replace(microsecond=0)
        t0 = now - timedelta(hours=hours_back)

        # --- rainfall: full trailing window for spin-up + forward --------------
        rain_start = (now - timedelta(days=self.cfg.trailing_days)).replace(
            minute=0, second=0, microsecond=0
        )
        raw_rain = self._get_history(self.cfg.rain_sensor, rain_start, now)
        parsed_rain: list[tuple[datetime, float]] = []
        for s in raw_rain:
            try:
                ts = datetime.fromisoformat(s["last_changed"].replace("Z", "+00:00"))
                parsed_rain.append((ts, float(s["state"])))
            except (KeyError, ValueError):
                continue
        rain_hourly, _ = hourly_from_accumulator(parsed_rain, rain_start, now)

        # --- lake level: T0-2h to now for anchor and forward comparison --------
        lake_start = t0 - timedelta(hours=2)
        raw_lake = self._get_history(self.cfg.lake_sensor, lake_start, now)
        level_by_hour = backtest.level_history_to_hourly(
            raw_lake, self.art.datum.sensor_to_absolute_offset_ft
        )

        # --- control elevation for the date ------------------------------------
        count = default_stop_log_count(self.art.stop_logs, t0.month, t0.day)
        control_elev = control_elev_for_stop_logs(self.art.stop_logs, count)

        # --- run backtest ------------------------------------------------------
        result = backtest.run_backtest(
            self.art, rain_hourly, rain_start, level_by_hour, t0, now, control_elev
        )

        # --- data freshness: is the rain-rate sensor recent? -------------------
        data_fresh = False
        try:
            rate_state = self._get_state(self.cfg.rain_rate_sensor)
            data_fresh = _state_age_hours(rate_state, now) <= self.cfg.stale_after_hours
        except Exception:  # noqa: BLE001
            data_fresh = False

        return {**result, "stop_log_count": count, "data_fresh": data_fresh}
