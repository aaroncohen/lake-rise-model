"""Observed Crystal Lake Emergency Action Plan levels.

These gauge-stick thresholds are operational policy, not forecast probabilities.  They
are shared by the observed crossing state machine, recipient routing, and rendering so
the page trigger and the actions printed in the notice cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EAPLevel:
    rank: int
    gauge_ft: float
    severity: str
    color: str
    title: str
    audience: str
    contacts: str
    actions: tuple[str, ...]

EAP_LEVELS: tuple[EAPLevel, ...] = (
    EAPLevel(
        rank=1,
        gauge_ft=3.30,
        severity="warning",
        color="#996020",
        title="Mandatory Alert",
        audience="emergency",
        contacts="DSO, SMO, RCEC",
        actions=(
            "Follow the EAP flowchart (Appendix A) and notify all contacts (EAP p. 25).",
            "Direct east-side residents to move vehicles to the west side ahead of possible"
            " road or bridge damage.",
            "Begin sandbagging and cover the downstream slope with plastic.",
        ),
    ),
    EAPLevel(
        rank=2,
        gauge_ft=3.90,
        severity="critical",
        color="#9c1f24",
        title="Bridge Closure",
        audience="road",
        contacts="DSO, SMO, RCEC",
        actions=(
            "Overtopping begins 25’ east of the bridge.",
            "Crystal Lake bridge SHALL be closed to all vehicle traffic.",
        ),
    ),
    EAPLevel(
        rank=3,
        gauge_ft=4.40,
        severity="emergency",
        color="#6d1220",
        title="Evacuate Downstream",
        audience="evacuate",
        contacts="NORCOM, KCDOT; re-contact DSO, SMO, RCEC",
        actions=(
            "Bridge deck is overtopped.",
            'Notify NORCOM and KCDOT of “imminent failure of the dam.”',
            "Evacuate downstream.",
        ),
    ),
)

EAP_RESET_GAUGE_FT = 3.25
EAP_RESET_MINUTES = 30
EAP_AUDIENCES = ("ops", *(level.audience for level in EAP_LEVELS))


def active_eap_rank(gauge_ft: float) -> int:
    """Highest EAP level active at ``gauge_ft``; zero means below 3.30 ft."""
    return max((level.rank for level in EAP_LEVELS if gauge_ft >= level.gauge_ft), default=0)


def eap_level(rank: int) -> EAPLevel | None:
    return next((level for level in EAP_LEVELS if level.rank == rank), None)
