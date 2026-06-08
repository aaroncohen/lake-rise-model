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

from ..artifact import Artifact
from ..bundle import InputBundle
from ..geometry import default_stop_log_count
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
        trailing, has_gaps = hourly_from_accumulator(parsed, start, now)

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

    def build_bundle(self) -> InputBundle:
        return bundle_from_snapshot(self.art, self.fetch_snapshot())
