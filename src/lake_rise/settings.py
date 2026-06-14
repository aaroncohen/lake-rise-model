"""Environment-driven configuration for the serving layer (spec 6: config via env).

Values may live in a ``.env`` file (repo root or current dir) instead of exported
shell vars; real environment variables always take precedence over the file."""

from __future__ import annotations

import os
from pathlib import Path

from .sources.live_ha import HAConfig

_DOTENV_LOADED = False


def _load_dotenv_once() -> None:
    """Load KEY=VALUE lines from a ``.env`` in the current working directory into
    os.environ (without overriding already-set vars). No dependency; supports
    comments, blank lines, optional 'export ' and surrounding quotes. Put the file
    in the directory you launch the server from (the repo root, typically)."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    path = Path.cwd() / ".env"
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, val = line.partition("=")
        if not sep:
            continue
        val = val.strip()
        # Strip inline comments (` # ...`) but not bare `#` inside values.
        if " #" in val:
            val = val[:val.index(" #")].rstrip()
        os.environ.setdefault(key.strip(), val.strip('"').strip("'"))  # real env wins


def ha_config_from_env() -> HAConfig | None:
    """Build an HAConfig from env vars, or None if credentials are absent.

    Required: HA_URL, HA_TOKEN. Optional overrides: LAKE_RISE_LAKE_SENSOR,
    LAKE_RISE_RAIN_SENSOR, LAKE_RISE_FORECAST_ENTITY, LAKE_RISE_STOPLOG_HELPER,
    LAKE_RISE_LAKE_FRESH_SENSOR, LAKE_RISE_LAKE_STALE_MINUTES."""
    _load_dotenv_once()
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
    if v := os.getenv("LAKE_RISE_LAKE_FRESH_SENSOR"):
        cfg.lake_fresh_sensor = v
    if v := os.getenv("LAKE_RISE_LAKE_STALE_MINUTES"):
        try:
            cfg.lake_stale_minutes = float(v)
        except ValueError:
            pass  # keep the default on a malformed value
    return cfg


def artifact_path_from_env() -> str | None:
    _load_dotenv_once()
    return os.getenv("LAKE_RISE_ARTIFACT")
