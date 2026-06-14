"""Persisted alert state and the fire-on-crossing decision.

The core rule: an alert is sent only when the situation crosses **up** into a higher
level than the one last alerted — never repeated hourly while the level is unchanged.
A downgrade is silent but lowers the stored rank, so a later re-escalation fires again.
An independent test track fires once when rain enters the forecast.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import AlertConfig
from .rules import AlertDecision


@dataclass
class AlertState:
    level_rank: int = 0          # last *alerted* ladder rank (tracks current active rank)
    level_name: str | None = None
    max_rank_reached: int = 0    # high-water mark of the current episode (for all-clear audience)
    test_active: bool = False
    last_monthly_test_ym: str | None = None   # "YYYY-MM" of the last monthly test sent
    updated_at: str | None = None


# kinds: "LEVEL" (escalation up), "ALL_CLEAR", "TEST", "TEST_CLEAR"
@dataclass(frozen=True)
class NotifyAction:
    kind: str
    rank: int                    # rank used to resolve recipients (ladder actions)
    level_name: str | None = None


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
        updated_at=data.get("updated_at"),
    )


def save_state(path: Path, state: AlertState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "level_rank": state.level_rank,
        "level_name": state.level_name,
        "max_rank_reached": state.max_rank_reached,
        "test_active": state.test_active,
        "last_monthly_test_ym": state.last_monthly_test_ym,
        "updated_at": state.updated_at,
    }, indent=2))


def decide_notifications(
    decision: AlertDecision,
    prior: AlertState,
    config: AlertConfig,
) -> tuple[list[NotifyAction], AlertState]:
    """Return the notifications to send this run and the state to persist next."""
    actions: list[NotifyAction] = []
    new_rank = decision.active_rank

    # --- ladder track ---------------------------------------------------------
    if new_rank > prior.level_rank:
        # Crossed up into a new, higher level.
        actions.append(NotifyAction("LEVEL", rank=new_rank, level_name=decision.active_level_name))
        new_max = max(prior.max_rank_reached, new_rank)
    elif new_rank == 0 and prior.level_rank > 0:
        # Returned to normal: one-shot all-clear to the broadest audience reached.
        if config.send_all_clear:
            clear_rank = max(prior.max_rank_reached, prior.level_rank)
            actions.append(NotifyAction("ALL_CLEAR", rank=clear_rank, level_name=prior.level_name))
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

    new_state = AlertState(
        level_rank=new_rank,
        level_name=decision.active_level_name,
        max_rank_reached=new_max,
        test_active=decision.test_active,
        last_monthly_test_ym=new_monthly_ym,
        updated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    return actions, new_state
