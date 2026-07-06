"""Persisted alert state and the fire-on-crossing decision.

The core rule: an alert is sent only when the situation crosses **up** into a higher
level than the one last alerted — never repeated hourly while the level is unchanged.
A downgrade is silent but lowers the stored rank, so a later re-escalation fires again.
An independent test track fires once when rain enters the forecast.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from ..fsutil import atomic_write_text
from .config import AlertConfig
from .rules import AlertDecision


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


# kinds: "LEVEL" (escalation up), "ALL_CLEAR", "TEST", "TEST_CLEAR"
@dataclass(frozen=True)
class NotifyAction:
    kind: str
    rank: int                    # rank used to resolve recipients (ladder actions)
    level_name: str | None = None
    # Episode high-water mark carried on the ALL_CLEAR so the orchestrator can render it.
    episode_peak_ft: float | None = None
    episode_peak_at: datetime | None = None


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
    new_monthly_ym = prior.last_monthly_test_ym
    if config.monthly_test_enabled and decision.generated_at.day >= config.monthly_test_dom:
        current_ym = decision.generated_at.strftime("%Y-%m")
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
    )
    return actions, new_state


# Which persisted fields each notify track "commits". A track whose notice was not delivered must
# not commit its fields (they roll back to `prior`) so the crossing retries on the next tick.
_LADDER_KINDS = {"LEVEL", "ALL_CLEAR"}
_TEST_KINDS = {"TEST", "TEST_CLEAR"}
_LADDER_FIELDS = ("level_rank", "level_name", "max_rank_reached",
                  "peak_elevation_ft", "peak_elevation_at")


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
    return replace(new_state, **reverts) if reverts else new_state
