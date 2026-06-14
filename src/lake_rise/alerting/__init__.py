"""Forecast-driven alerting for the Crystal Lake lake-rise model.

Pulls the live forecast on a schedule, evaluates an adjustable escalation ladder and a
toggleable test trigger, and dispatches notices (email / Twilio SMS) only when the
situation crosses up into a higher level. See ``service.run_once`` for the entry point.
"""

from .config import AlertConfig, alert_config_from_env
from .rules import AlertDecision, evaluate
from .service import RunResult, run_once

__all__ = [
    "AlertConfig",
    "alert_config_from_env",
    "AlertDecision",
    "evaluate",
    "RunResult",
    "run_once",
]
