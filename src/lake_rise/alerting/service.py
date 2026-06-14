"""Orchestration: the one place that wires prediction -> evaluation -> notification.

``run_once`` pulls the live forecast, predicts, evaluates the ladder + test trigger,
applies the fire-on-crossing logic, and dispatches any resulting notices through the
configured channels. The hourly scheduler and the CLI/API both call this; because of
the crossing rule, most runs evaluate and persist but send nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from ..artifact import Artifact, load_artifact
from ..bundle import InputBundle
from ..predict import predict
from ..settings import artifact_path_from_env, ha_config_from_env
from ..sources.live_ha import LiveHASource
from .channels import ConsoleNotifier, build_notifiers
from .channels.base import Notifier
from .config import AlertConfig
from .render import render
from .rules import AlertDecision, evaluate
from .state import NotifyAction, decide_notifications, load_state, save_state

log = logging.getLogger("lake_rise.alerting")


@dataclass
class RunResult:
    decision: AlertDecision
    actions: list[NotifyAction]
    sent: bool


def build_live_bundle(art: Artifact, config: AlertConfig) -> InputBundle:
    """Build the live input bundle from Home Assistant (Apple WeatherKit forecast)."""
    ha = ha_config_from_env()
    if ha is None:
        raise RuntimeError("No live HA source configured (set HA_URL and HA_TOKEN).")
    ha.horizon_hours = config.horizon_hours
    return LiveHASource(art, ha).build_bundle()


def _dispatch(action: NotifyAction, decision: AlertDecision, config: AlertConfig,
              notifiers: list[Notifier]) -> None:
    if action.kind in ("TEST", "TEST_CLEAR"):
        recipients = config.audience_recipients(config.test_audience)
        kind = "TEST" if action.kind == "TEST" else "TEST_CLEAR"
    elif action.kind == "MONTHLY_TEST":
        recipients = config.audience_recipients(config.monthly_test_audience)
        kind = "TEST"
    elif action.kind == "ALL_CLEAR":
        recipients = config.resolve_recipients(action.rank)
        kind = "ALL_CLEAR"
    else:  # LEVEL
        recipients = config.resolve_recipients(action.rank)
        kind = "LEVEL"

    alert = render(decision, config, kind=kind, level_name=action.level_name)
    if recipients.is_empty:
        log.warning("alert %s has no recipients (audience unconfigured); not sent", action.kind)
    for n in notifiers:
        try:
            n.send(alert, recipients)
        except Exception:  # noqa: BLE001 - one bad channel shouldn't sink the others
            log.exception("notifier %s failed to send %s", getattr(n, "name", "?"), action.kind)


def run_once(
    config: AlertConfig,
    *,
    bundle: InputBundle | None = None,
    art: Artifact | None = None,
    notifiers: list[Notifier] | None = None,
    dry_run: bool = False,
    force_test: bool = False,
) -> RunResult:
    """Evaluate the current (or supplied) forecast and dispatch any crossing notices.

    dry_run -> route everything to the console and do NOT persist state (so repeated
    test runs keep producing output). Live runs persist and use the real channels.
    """
    art = art or load_artifact(artifact_path_from_env())
    bundle = bundle if bundle is not None else build_live_bundle(art, config)

    result = predict(bundle, art)
    decision = evaluate(result, bundle, art, config)

    prior = load_state(config.state_path)
    if force_test:
        decision = replace(decision, test_active=True)
        prior.test_active = False  # guarantee the False→True transition fires
    actions, new_state = decide_notifications(decision, prior, config)

    if notifiers is None:
        notifiers = [ConsoleNotifier()] if dry_run else build_notifiers(config)

    for action in actions:
        _dispatch(action, decision, config, notifiers)

    if not dry_run:
        save_state(config.state_path, new_state)

    log.info(
        "alert run: rank=%d(%s) test=%s p_crest=%.2f -> %d notice(s)%s",
        decision.active_rank, decision.active_level_name, decision.test_active,
        decision.probabilities.get("dam_crest", 0.0), len(actions),
        " [dry-run]" if dry_run else "",
    )
    return RunResult(decision=decision, actions=actions, sent=bool(actions) and not dry_run)
