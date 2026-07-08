"""Calibration pipeline config from env, reusing the alerting SMTP value object."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..alerting.config import SMTPConfig, _bool
from ..settings import _load_dotenv_once
from .state import DEFAULT_STATE_PATH, VERSIONS_PATH


@dataclass(frozen=True)
class CalibrationConfig:
    enabled: bool
    recipient: str | None            # single operator email for the proposal
    bfi_target: float
    min_recession_days: int
    startup_backfill_days: int       # on startup, pull this much history (HA returns what it retains)
    state_path: Path
    versions_path: Path              # directory holding the versioned artifacts
    api_token: str | None
    ui_base_url: str | None
    template_path: Path | None       # directory of override templates
    smtp: SMTPConfig


def _smtp_from_env() -> SMTPConfig:
    return SMTPConfig(
        host=os.getenv("SMTP_HOST", ""),
        port=int(os.getenv("SMTP_PORT", "587")),
        user=os.getenv("SMTP_USER"),
        password=os.getenv("SMTP_PASSWORD"),
        sender=os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")),
        starttls=_bool("SMTP_STARTTLS", True),
    )


def calibration_config_from_env() -> CalibrationConfig:
    _load_dotenv_once()
    tp = os.getenv("CALIB_TEMPLATE_PATH")
    return CalibrationConfig(
        enabled=_bool("CALIB_ENABLED", False),
        recipient=os.getenv("CALIB_RECIPIENT") or None,
        bfi_target=float(os.getenv("CALIB_BFI_TARGET", "0.67")),
        min_recession_days=int(os.getenv("CALIB_MIN_RECESSION_DAYS", "5")),
        # Pull a generous window on startup so we persist as much as HA still retains (HA truncates
        # to its own recorder retention; the gap-aware merge fills recoverable holes idempotently).
        startup_backfill_days=int(os.getenv("CALIB_BACKFILL_DAYS", "400")),
        state_path=Path(os.getenv("CALIB_STATE_PATH", str(DEFAULT_STATE_PATH))),
        versions_path=Path(os.getenv("CALIB_VERSIONS_PATH", str(VERSIONS_PATH))),
        api_token=os.getenv("CALIB_API_TOKEN") or None,
        ui_base_url=os.getenv("CALIB_UI_BASE_URL") or None,
        template_path=Path(tp) if tp else None,
        smtp=_smtp_from_env(),
    )
