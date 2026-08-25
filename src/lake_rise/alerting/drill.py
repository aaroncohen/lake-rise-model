"""Monthly communications drill: one progression through every alert level.

Fires once per month (on or after ALERT_DRILL_DOM) and sends five messages —
Advisory, Danger, Critical, Downstream Evac Notice, All Clear — to the drill
audience (default: ops).  All messages are prominently labelled as a test.

This is entirely independent of the main fire-on-crossing state machine:
it does not change level_rank, does not consult the live forecast, and only
updates last_drill_ym in the persisted state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..artifact import Artifact, load_artifact
from ..settings import artifact_path_from_env
from .channels import ConsoleNotifier, build_notifiers
from .channels.base import Notifier
from .config import AlertConfig
from .render import render_drill
from .state import ALERT_STATE_LOCK, AlertState, load_state, save_state

log = logging.getLogger("lake_rise.alerting")

# Fixed escalation sequence for the drill.
_DRILL_STEPS: list[tuple[str | None, str]] = [
    ("ADVISORY",  "LEVEL"),
    ("DANGER",    "LEVEL"),
    ("CRITICAL",  "LEVEL"),
    ("EVACUATE",  "LEVEL"),
    ("EVACUATE",  "ALL_CLEAR"),  # carries the prior level name so "clears the prior X" renders
]


def should_run_drill(state: AlertState, config: AlertConfig) -> bool:
    if not config.drill_enabled:
        return False
    now = datetime.now(timezone.utc)
    if now.day < config.drill_dom:
        return False
    current_ym = now.strftime("%Y-%m")
    return state.last_drill_ym != current_ym


def run_drill(
    config: AlertConfig,
    *,
    art: Artifact | None = None,
    notifiers: list[Notifier] | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Send the full 5-step drill sequence.  Returns the list of dispatched step labels."""
    with ALERT_STATE_LOCK:
        return _run_drill_unlocked(
            config, art=art, notifiers=notifiers, dry_run=dry_run)


def _run_drill_unlocked(
    config: AlertConfig,
    *,
    art: Artifact | None = None,
    notifiers: list[Notifier] | None = None,
    dry_run: bool = False,
) -> list[str]:
    recipients = config.audience_recipients(config.drill_audience)
    if recipients.is_empty and not dry_run:
        log.warning("drill audience %r has no recipients configured; skipping", config.drill_audience)
        return []

    art = art or load_artifact(artifact_path_from_env())

    if notifiers is None:
        notifiers = [ConsoleNotifier()] if dry_run else build_notifiers(config)

    dispatched: list[str] = []
    for level_name, kind in _DRILL_STEPS:
        alert = render_drill(level_name, kind, config, art)
        label = f"{kind}:{level_name}"
        for notifier in notifiers:
            try:
                notifier.send(alert, recipients)
            except Exception:  # noqa: BLE001
                log.exception("drill notifier %s failed for step %s",
                              getattr(notifier, "name", "?"), label)
        dispatched.append(label)

    if not dry_run:
        state = load_state(config.state_path)
        state.last_drill_ym = datetime.now(timezone.utc).strftime("%Y-%m")
        save_state(config.state_path, state)

    log.info("drill complete: %d steps dispatched%s", len(dispatched),
             " [dry-run]" if dry_run else "")
    return dispatched
