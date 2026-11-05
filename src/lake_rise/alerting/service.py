"""Orchestration: the one place that wires prediction -> evaluation -> notification.

``run_once`` pulls the live forecast, predicts, evaluates the ladder + test trigger,
applies the fire-on-crossing logic, and dispatches any resulting notices through the
configured channels. The hourly scheduler and the CLI/API both call this; because of
the crossing rule, most runs evaluate and persist but send nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime

from ..artifact import Artifact
from ..bundle import InputBundle
from ..observed import GaugeObservation
from ..predict import predict
from ..settings import ha_config_from_env
from ..sources.live_ha import LiveHASource
from .channels import ConsoleNotifier, build_notifiers
from .channels.base import Notifier
from .config import AlertConfig
from .render import render
from .rules import AlertDecision, evaluate
from .state import (
    ALERT_STATE_LOCK,
    NotifyAction,
    decide_notifications,
    decide_observed_notifications,
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
    prediction: object | None = None      # the PredictionResult behind the decision


@dataclass
class ObservedRunResult:
    observation: GaugeObservation
    decision: AlertDecision | None
    actions: list[NotifyAction]
    sent: bool


def build_live_bundle(art: Artifact, config: AlertConfig) -> InputBundle:
    """Build the live input bundle from Home Assistant (Apple WeatherKit forecast)."""
    ha = ha_config_from_env()
    if ha is None:
        raise RuntimeError("No live HA source configured (set HA_URL and HA_TOKEN).")
    ha.horizon_hours = config.horizon_hours
    return LiveHASource(art, ha).build_bundle()


def _forecastless_bundle(art: Artifact, observation: GaugeObservation,
                         config: AlertConfig) -> InputBundle:
    """A bundle from the observed gauge alone (no forecast rain, flagged degraded), so an
    observed EAP notice can still be rendered when the forecast pipeline fails."""
    from ..bundle import ScenarioRain
    from ..geometry import default_stop_log_count
    from ..hourly import floor_hour

    now = observation.detected_at
    zeros = [0.0] * config.horizon_hours
    return InputBundle(
        as_of=floor_hour(now),
        current_elevation_abs_ft=observation.gauge_ft + art.datum.sensor_to_absolute_offset_ft,
        stop_log_count=default_stop_log_count(art.stop_logs, now.month, now.day),
        forecast_scenarios=[ScenarioRain(name=n, hourly_in=zeros) for n in ("low", "median", "high")],
        rainfall_has_gaps=True,
    )


def _log_forecast(bundle: InputBundle, result, art: Artifact) -> None:
    """Keep the forecast of record (``forecast_log``) for a live scheduled run. Runs after
    dispatch and must never affect alerting: any failure is logged and swallowed. The full-horizon
    QPF outlook is best-effort -- the record is still written without it."""
    from .. import forecast_log

    try:
        outlook = None
        try:
            ha = ha_config_from_env()
            if ha is not None:
                outlook = LiveHASource(art, ha).forecast_outlook()
        except Exception as exc:  # noqa: BLE001
            log.warning("forecast log: QPF outlook unavailable: %s", exc)
        forecast_log.append(forecast_log.forecast_record(
            bundle, result, artifact_version=art.version, qpf_outlook=outlook))
    except Exception as exc:  # noqa: BLE001 -- the log must never break an alert run
        log.warning("forecast log: record not written: %s", exc)


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
    elif action.kind == "EAP_CROSSING":
        recipients = config.resolve_eap_recipients(action.rank)
        kind = "EAP_CROSSING"
    else:  # LEVEL
        recipients = config.resolve_recipients(action.rank)
        kind = "LEVEL"

    alert = render(
        decision,
        config,
        kind=kind,
        level_name=action.level_name,
        observed_rank=action.rank if action.kind == "EAP_CROSSING" else 0,
        observed_gauge_ft=action.observed_gauge_ft,
        observed_detected_at=action.observed_detected_at,
        observed_degraded=action.observed_degraded,
        observed_degraded_reason=action.observed_degraded_reason,
        observed_previous_rank=action.observed_previous_rank,
    )
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


def _run_once_unlocked(
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
    result = predict(bundle, art)
    decision = evaluate(result, bundle, art, config)

    if bundle.horizon_hours == 0:
        # No forecast at all (the forecast call failed, or the entity returned nothing): every
        # scenario would be flat at the current level, so the probabilities would collapse and
        # the ladder would drop -- an ALL_CLEAR mid-storm. Hold every track instead.
        log.error("alert run: no forecast hours available; holding alert state unchanged")
        return RunResult(decision=decision, actions=[], sent=False, prediction=result)

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
                     sent=delivered_any and not dry_run, prediction=result)


def run_once(
    config: AlertConfig,
    *,
    bundle: InputBundle | None = None,
    art: Artifact | None = None,
    notifiers: list[Notifier] | None = None,
    dry_run: bool = False,
    force_test: bool = False,
) -> RunResult:
    """Public forecast-alert entry point. The slow network I/O -- pulling the live bundle, and
    the forecast-of-record log after -- happens outside ``ALERT_STATE_LOCK``, which serializes only
    the state read-decide-dispatch-persist transaction, so a slow HA can't block the 5-minute
    observed-EAP check. A dry run never persists, so it doesn't take the lock at all."""
    if art is None:
        # No explicit artifact (the hourly scheduler): serve the calibration active version,
        # not the raw env artifact, so an approved re-tuning actually reaches the alert path.
        from ..calibration.service import active_artifact_and_version
        art, _ = active_artifact_and_version()
    live = bundle is None
    if live:
        bundle = build_live_bundle(art, config)
    kwargs = dict(bundle=bundle, art=art, notifiers=notifiers, dry_run=dry_run,
                  force_test=force_test)
    if dry_run:
        return _run_once_unlocked(config, **kwargs)
    with ALERT_STATE_LOCK:
        res = _run_once_unlocked(config, **kwargs)
    prediction = getattr(res, "prediction", None)
    if live and prediction is not None:
        _log_forecast(bundle, prediction, art)
    return res


def run_observed_once(
    config: AlertConfig,
    *,
    art: Artifact | None = None,
    source: LiveHASource | None = None,
    notifiers: list[Notifier] | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> ObservedRunResult:
    """Poll the cheap 15-minute gauge signal and notify only on a new EAP crossing.

    The expensive rainfall/forecast bundle is fetched only when a crossing requires a
    notice. Forecast and observed tracks are then evaluated together but dispatched as
    distinct actions, with independent delivery rollback.
    """
    with ALERT_STATE_LOCK:
        if art is None:
            from ..calibration.service import active_artifact_and_version
            art, _ = active_artifact_and_version()
        if source is None:
            ha = ha_config_from_env()
            if ha is None:
                raise RuntimeError("No live HA source configured (set HA_URL and HA_TOKEN).")
            ha.horizon_hours = config.horizon_hours
            source = LiveHASource(art, ha)

        observation = source.fetch_gauge_observation(now=now)
        if observation.gauge_ft is None:
            log.error("observed EAP check skipped: %s",
                      observation.degraded_reason or "no usable gauge reading")
            return ObservedRunResult(observation, None, [], False)

        prior = load_state(config.state_path)
        observed_actions, observed_state = decide_observed_notifications(observation, prior)

        # No new crossing: persist only debounce/re-arm progress and avoid forecast traffic.
        if not observed_actions:
            if not dry_run and observed_state is not prior:
                save_state(config.state_path, observed_state)
            return ObservedRunResult(observation, None, [], False)

        # The observed EAP page must not depend on the forecast pipeline: if the live bundle
        # can't be built (or has no forecast), render the notice from a forecast-less fallback
        # and leave the forecast ladder exactly as it was.
        forecast_ok = True
        try:
            bundle = source.build_bundle(lake_reading_ft=observation.gauge_ft)
            if bundle.horizon_hours == 0:
                raise RuntimeError("the live forecast has no hours")
        except Exception as exc:  # noqa: BLE001
            log.error("observed EAP: forecast unavailable (%s); sending the observed notice "
                      "without it and holding the forecast ladder", exc)
            bundle = _forecastless_bundle(art, observation, config)
            forecast_ok = False
        result = predict(bundle, art)
        decision = evaluate(result, bundle, art, config)
        if forecast_ok:
            predictive_actions, predictive_state = decide_notifications(decision, prior, config)
        else:
            predictive_actions, predictive_state = [], prior
        observed_actions, new_state = decide_observed_notifications(observation, predictive_state)
        actions = [*predictive_actions, *observed_actions]

        if notifiers is None:
            notifiers = [ConsoleNotifier()] if dry_run else build_notifiers(config)

        undelivered: set[str] = set()
        for action in actions:
            dec = decision
            if action.episode_peak_ft is not None:
                dec = replace(decision, episode_peak_elevation=action.episode_peak_ft,
                              episode_peak_at=action.episode_peak_at)
            if not _dispatch(action, dec, config, notifiers):
                undelivered.add(action.kind)

        if not dry_run:
            persist = hold_undelivered(new_state, prior, undelivered)
            if undelivered:
                log.error("observed alert run: notice(s) NOT delivered (%s); retry armed",
                          ",".join(sorted(undelivered)))
            save_state(config.state_path, persist)

        emitted = {action.kind for action in actions}
        delivered_any = bool(emitted) and emitted != undelivered
        return ObservedRunResult(
            observation=observation,
            decision=decision,
            actions=actions,
            sent=delivered_any and not dry_run,
        )
