"""Environment-driven configuration for the serving layer (spec 6: config via env)."""

from __future__ import annotations

import os

from .sources.live_ha import HAConfig


def ha_config_from_env() -> HAConfig | None:
    """Build an HAConfig from env vars, or None if credentials are absent.

    Required: HA_URL, HA_TOKEN. Optional overrides: LAKE_RISE_LAKE_SENSOR,
    LAKE_RISE_RAIN_SENSOR, LAKE_RISE_FORECAST_ENTITY, LAKE_RISE_STOPLOG_HELPER."""
    url, token = os.getenv("HA_URL"), os.getenv("HA_TOKEN")
    if not url or not token:
        return None
    cfg = HAConfig(base_url=url.rstrip("/"), token=token)
    if v := os.getenv("LAKE_RISE_LAKE_SENSOR"):
        cfg.lake_sensor = v
    if v := os.getenv("LAKE_RISE_RAIN_SENSOR"):
        cfg.rain_sensor = v
    if v := os.getenv("LAKE_RISE_FORECAST_ENTITY"):
        cfg.forecast_entity = v
    if v := os.getenv("LAKE_RISE_STOPLOG_HELPER"):
        cfg.stop_log_helper = v
    return cfg


def artifact_path_from_env() -> str | None:
    return os.getenv("LAKE_RISE_ARTIFACT")
