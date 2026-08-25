"""Persisted alert state and the fire-on-crossing decision.

The core rule: an alert is sent only when the situation crosses **up** into a higher
level than the one last alerted — never repeated hourly while the level is unchanged.
A downgrade is silent but lowers the stored rank, so a later re-escalation fires again.
An independent test track fires once when rain enters the forecast.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..fsutil import atomic_write_text
from ..observed import GaugeObservation
from .config import AlertConfig
from .eap import (
    EAP_RESET_GAUGE_FT,
    EAP_RESET_MINUTES,
    active_eap_rank,
    eap_level,
)
from .rules import AlertDecision


# One alerting process owns one state file. Serialize complete read/decide/send/write
# transactions so the forecast job, observed monitor, API, CLI, and drill cannot decide
# from the same prior state and then overwrite one another.
ALERT_STATE_LOCK = threading.RLock()


@dataclass
class AlertState:
    level_rank: int = 0          # last *alerted* ladder rank (tracks current active rank)
    level_name: str | None = None
    max_rank_reached: int = 0    # high-water mark of the current episode (for all-clear audience)
    test_active: bool = False
    last_monthly_test_ym: str | None = None   # "YYYY-MM" of the last monthly test sent
    last_drill_ym: str | None = None          # "YYYY-MM" of the last monthly drill sent
    updated_at: str | None = None
    # Observed lake-level high-water mark of the current alert episode (absolute ft) and when
    # it was reached. Accrues while elevated, resets to 0 on return to normal; surfaced in the
    # ALL_CLEAR notice so it can report how high the lake actually got.
    peak_elevation_ft: float = 0.0
    peak_elevation_at: str | None = None
    # Independent observed-EAP track. The rank stays latched through a continuous event;
    # re-arming requires 30 uninterrupted minutes below the 3.25 ft reset threshold.
    observed_eap_rank: int = 0
    observed_clear_since: str | None = None


# kinds: "LEVEL" (escalation up), "ALL_CLEAR", "TEST", "TEST_CLEAR"
@dataclass(frozen=True)
class NotifyAction:
    kind: str
    rank: int                    # rank used to resolve recipients (ladder actions)
    level_name: str | None = None
    # Episode high-water mark carried on the ALL_CLEAR so the orchestrator can render it.
    episode_peak_ft: float | None = None
    episode_peak_at: datetime | None = None
    observed_gauge_ft: float | None = None
    observed_detected_at: datetime | None = None
    observed_degraded: bool = False
    observed_degraded_reason: str | None = None
    observed_previous_rank: int = 0


def load_state(path: Path) -> AlertState:
    if not path.is_file():
        return AlertState()
    data = json.loads(path.read_text())
    return AlertState(
        level_rank=int(data.get("level_rank", 0)),
        level_name=data.get("level_name"),
        max_rank_reached=int(data.get("max_rank_reached", 0)),
        test_active=bool(data.get("test_active", False)),
        last_monthly_test_ym=data.get("last_monthly_test_ym"),
        last_drill_ym=data.get("last_drill_ym"),
        updated_at=data.get("updated_at"),
        peak_elevation_ft=float(data.get("peak_elevation_ft", 0.0)),
        peak_elevation_at=data.get("peak_elevation_at"),
        observed_eap_rank=int(data.get("observed_eap_rank", 0)),
        observed_clear_since=data.get("observed_clear_since"),
    )


def save_state(path: Path, state: AlertState) -> None:
    atomic_write_text(path, json.dumps({
        "level_rank": state.level_rank,
        "level_name": state.level_name,
        "max_rank_reached": state.max_rank_reached,
        "test_active": state.test_active,
        "last_monthly_test_ym": state.last_monthly_test_ym,
        "last_drill_ym": state.last_drill_ym,
        "updated_at": state.updated_at,
        "peak_elevation_ft": state.peak_elevation_ft,
        "peak_elevation_at": state.peak_elevation_at,
        "observed_eap_rank": state.observed_eap_rank,
        "observed_clear_since": state.observed_clear_since,
    }, indent=2))




def decide_notifications(
    decision: AlertDecision,
    prior: AlertState,
    config: AlertConfig,
) -> tuple[list[NotifyAction], AlertState]:
    """Return the notifications to send this run and the state to persist next."""
    actions: list[NotifyAction] = []
    new_rank = decision.active_rank

    # Episode high-water mark (observed level), carried across runs while elevated.
    cur = decision.current_elevation
    if cur >= prior.peak_elevation_ft:
        peak_ft, peak_at = cur, decision.generated_at
    else:
        prior_at = prior.peak_elevation_at
        peak_ft, peak_at = prior.peak_elevation_ft, (
            datetime.fromisoformat(prior_at) if prior_at else None)

    # --- ladder track ---------------------------------------------------------
    if new_rank > prior.level_rank:
        # Crossed up into a new, higher level.
        actions.append(NotifyAction("LEVEL", rank=new_rank, level_name=decision.active_level_name))
        new_max = max(prior.max_rank_reached, new_rank)
    elif new_rank == 0 and prior.level_rank > 0:
        # Returned to normal: one-shot all-clear to the broadest audience reached, carrying
        # the episode high-water mark so the notice can report how high the lake got.
        if config.send_all_clear:
            clear_rank = max(prior.max_rank_reached, prior.level_rank)
            actions.append(NotifyAction("ALL_CLEAR", rank=clear_rank, level_name=prior.level_name,
                                        episode_peak_ft=peak_ft, episode_peak_at=peak_at))
        new_max = 0
    else:
        # Same level (no repeat) or a silent downgrade to a still-elevated level.
        new_max = max(prior.max_rank_reached, new_rank) if new_rank > 0 else 0

    # --- test track (independent of the ladder) -------------------------------
    if decision.test_active and not prior.test_active:
        actions.append(NotifyAction("TEST", rank=0))
    elif not decision.test_active and prior.test_active and config.send_all_clear:
        actions.append(NotifyAction("TEST_CLEAR", rank=0))

    # --- monthly test track ---------------------------------------------------
    # Gated on local calendar day/hour (config.timezone, e.g. Pacific) rather than
    # decision.generated_at's raw UTC values -- UTC's "day 1" starts 7-8 hours before
    # local midnight, so a raw-UTC gate would fire the notice the evening *before* the
    # 1st, local time, misattributed to the wrong month.
    new_monthly_ym = prior.last_monthly_test_ym
    if config.monthly_test_enabled:
        local_now = decision.generated_at.astimezone(ZoneInfo(config.timezone))
        if local_now.day >= config.monthly_test_dom and local_now.hour >= config.monthly_test_hour:
            current_ym = local_now.strftime("%Y-%m")
            if prior.last_monthly_test_ym != current_ym:
                actions.append(NotifyAction("MONTHLY_TEST", rank=0))
                new_monthly_ym = current_ym

    # Carry the high-water mark while elevated; reset once back to normal.
    keep_peak = new_rank > 0
    new_state = AlertState(
        level_rank=new_rank,
        level_name=decision.active_level_name,
        max_rank_reached=new_max,
        test_active=decision.test_active,
        last_monthly_test_ym=new_monthly_ym,
        last_drill_ym=prior.last_drill_ym,   # decide_notifications never sends a drill; preserve it
        updated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        peak_elevation_ft=peak_ft if keep_peak else 0.0,
        peak_elevation_at=(peak_at.isoformat() if (keep_peak and peak_at) else None),
        observed_eap_rank=prior.observed_eap_rank,
        observed_clear_since=prior.observed_clear_since,
    )
    return actions, new_state


def decide_observed_notifications(
    observation: GaugeObservation,
    prior: AlertState,
) -> tuple[list[NotifyAction], AlertState]:
    """Advance the independent, latched observed-EAP track.

    Invalid/stale observations leave the track untouched. A partial recession never
    lowers the latched rank; only a sustained fall below the reset threshold re-arms it.
    """
    gauge = observation.gauge_ft
    if gauge is None:
        return [], prior

    current_rank = active_eap_rank(gauge)
    clear_since: str | None = None
    notified_rank = prior.observed_eap_rank
    actions: list[NotifyAction] = []

    if notified_rank > 0 and gauge < EAP_RESET_GAUGE_FT:
        started = (datetime.fromisoformat(prior.observed_clear_since)
                   if prior.observed_clear_since else observation.detected_at)
        if observation.detected_at - started >= timedelta(minutes=EAP_RESET_MINUTES):
            notified_rank = 0
        else:
            clear_since = started.isoformat()

    if current_rank > notified_rank:
        level = eap_level(current_rank)
        actions.append(NotifyAction(
            "EAP_CROSSING",
            rank=current_rank,
            level_name=level.title if level else None,
            observed_gauge_ft=gauge,
            observed_detected_at=observation.detected_at,
            observed_degraded=observation.degraded,
            observed_degraded_reason=observation.degraded_reason,
            observed_previous_rank=notified_rank,
        ))
        notified_rank = current_rank
        clear_since = None

    if (not actions and notified_rank == prior.observed_eap_rank
            and clear_since == prior.observed_clear_since):
        return [], prior

    new_state = replace(
        prior,
        observed_eap_rank=notified_rank,
        observed_clear_since=clear_since,
        updated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    return actions, new_state


# Which persisted fields each notify track "commits". A track whose notice was not delivered must
# not commit its fields (they roll back to `prior`) so the crossing retries on the next tick.
_LADDER_KINDS = {"LEVEL", "ALL_CLEAR"}
_TEST_KINDS = {"TEST", "TEST_CLEAR"}
_OBSERVED_KINDS = {"EAP_CROSSING"}
_LADDER_FIELDS = ("level_rank", "level_name", "max_rank_reached",
                  "peak_elevation_ft", "peak_elevation_at")
_OBSERVED_FIELDS = ("observed_eap_rank", "observed_clear_since")


def hold_undelivered(new_state: AlertState, prior: AlertState,
                     undelivered_kinds: set[str]) -> AlertState:
    """Roll back only the tracks whose notice was *not* delivered, so a lost escalation re-fires
    next tick instead of being silently swallowed. Delivered tracks and delivery-independent
    accrual (e.g. steady-state peak with no notice this tick) keep their advanced values."""
    reverts: dict[str, object] = {}
    if undelivered_kinds & _LADDER_KINDS:
        reverts.update({f: getattr(prior, f) for f in _LADDER_FIELDS})
    if undelivered_kinds & _TEST_KINDS:
        reverts["test_active"] = prior.test_active
    if "MONTHLY_TEST" in undelivered_kinds:
        reverts["last_monthly_test_ym"] = prior.last_monthly_test_ym
    if undelivered_kinds & _OBSERVED_KINDS:
        reverts.update({f: getattr(prior, f) for f in _OBSERVED_FIELDS})
    return replace(new_state, **reverts) if reverts else new_state
