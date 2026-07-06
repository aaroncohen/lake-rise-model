"""Orchestration: the one place that wires prediction -> evaluation -> notification.

``run_once`` pulls the live forecast, predicts, evaluates the ladder + test trigger,
applies the fire-on-crossing logic, and dispatches any resulting notices through the
configured channels. The hourly scheduler and the CLI/API both call this; because of
the crossing rule, most runs evaluate and persist but send nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from ..artifact import Artifact
from ..bundle import InputBundle
from ..predict import predict
from ..settings import ha_config_from_env
from ..sources.live_ha import LiveHASource
from .channels import ConsoleNotifier, build_notifiers
from .channels.base import Notifier
from .config import AlertConfig
from .render import render
from .rules import AlertDecision, evaluate
from .state import (
    NotifyAction,
    decide_notifications,
    hold_undelivered,
    load_state,
    save_state,
)

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
              notifiers: list[Notifier]) -> bool:
    """Send `action` through every channel and return a delivery receipt: True iff there were
    recipients and at least one notifier accepted the send without raising. False (no delivery)
    covers the dangerous modes — empty recipients, no notifiers, or every notifier raised — so the
    caller can decline to advance state and retry next tick.

    Caveat: a notifier may still no-op internally when it has no recipients for *its* medium (an
    email-only channel with SMS-only recipients); the Notifier protocol returns None, so that
    per-medium partial delivery is not distinguished here (a separate refinement)."""
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
    delivered = False
    for n in notifiers:
        # Still hand every notice to the console/dry-run writer even with no recipients (it renders
        # "(none)"); a real delivery only counts when recipients exist and the channel didn't raise.
        try:
            n.send(alert, recipients)
            delivered = delivered or not recipients.is_empty
        except Exception:  # noqa: BLE001 - one bad channel shouldn't sink the others
            log.exception("notifier %s failed to send %s", getattr(n, "name", "?"), action.kind)
    return delivered


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
    if art is None:
        # No explicit artifact (the hourly scheduler): serve the calibration active version,
        # not the raw env artifact, so an approved re-tuning actually reaches the alert path.
        from ..calibration.service import active_artifact_and_version
        art, _ = active_artifact_and_version()
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

    undelivered: set[str] = set()
    for action in actions:
        # An ALL_CLEAR carries the episode high-water mark; fold it into the decision so the
        # renderer can report how high the lake actually got (the live decision's peak is low
        # once things have calmed).
        dec = decision
        if action.episode_peak_ft is not None:
            dec = replace(decision, episode_peak_elevation=action.episode_peak_ft,
                          episode_peak_at=action.episode_peak_at)
        if not _dispatch(action, dec, config, notifiers):
            undelivered.add(action.kind)

    if not dry_run:
        # Fail loud, and don't advance a track whose notice wasn't delivered — otherwise
        # fire-on-crossing would swallow the escalation (no re-fire while the level holds).
        persist = new_state
        if undelivered:
            log.error("alert run: %d notice(s) NOT delivered (%s); holding state to retry next tick",
                      len(undelivered), ",".join(sorted(undelivered)))
            persist = hold_undelivered(new_state, prior, undelivered)
        save_state(config.state_path, persist)

    log.info(
        "alert run: rank=%d(%s) test=%s p_crest=%.2f -> %d notice(s)%s",
        decision.active_rank, decision.active_level_name, decision.test_active,
        decision.probabilities.get("dam_crest", 0.0), len(actions),
        " [dry-run]" if dry_run else "",
    )
    emitted_kinds = {a.kind for a in actions}
    delivered_any = bool(emitted_kinds) and emitted_kinds != undelivered
    return RunResult(decision=decision, actions=actions,
                     sent=delivered_any and not dry_run)
