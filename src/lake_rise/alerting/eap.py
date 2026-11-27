"""Observed Crystal Lake Emergency Action Plan levels.

These gauge-stick thresholds are operational policy, not forecast probabilities.  They
are shared by the observed crossing state machine, recipient routing, and rendering so
the page trigger and the actions printed in the notice cannot drift apart.

Source: the EAP's Water Level / Action Table (KI08-0195), as revised 2026-11. Each action
row above the pool levels is one level here, low to high. The rows the table names
"Emergency Level 1/2/3" (2.30 / 3.30 / 4.40 ft) are the ones that alert outside
agencies; the 2.60 ft and 3.90 ft rows are operator actions between them. ``pole_color``
is the colour the table (and the WQ pole) gives the row. The pool rows below the levels
(summer 1.30, winter 0.97, winter with one extra log removed 0.65 ft) are stop-log
heights, not alert levels. The model's crests come from the artifact's ``stop_logs`` (the
2026-11-25 laser survey: winter 1 log ~0.98 ft, summer 2 logs ~1.40, 0 logs = aux log out),
not from these pool numbers; ``geometry.default_stop_log_count`` picks the seasonal count.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EAPLevel:
    rank: int
    gauge_ft: float
    severity: str
    color: str            # banner colour (white text on it)
    pole_color: str       # the EAP table's colour for the row: "yellow" | "red"
    title: str
    audience: str
    contacts: str
    actions: tuple[str, ...]
    short_actions: tuple[str, ...]


EMERGENCY_LEVEL_1 = EAPLevel(
    rank=1,
    gauge_ft=2.30,
    severity="advisory",
    color="#7a5c00",
    pole_color="yellow",
    title="Emergency Level 1",
    audience="ops",
    contacts="DSO",
    actions=(
        "Alert DSO.",
        "Water is up one foot: monitor daily and track the lake's rate of rise.",
        "If the forecast is bad, optionally lower the stop logs 4 inches or more.",
    ),
    short_actions=(
        "Alert DSO",
        "Monitor daily · track rate of rise",
        "Bad forecast: lower logs 4\"+",
    ),
)

LOWER_STOP_LOGS = EAPLevel(
    rank=2,
    gauge_ft=2.60,
    severity="advisory",
    color="#7a5c00",
    pole_color="yellow",
    title="Lower Stop Logs",
    audience="ops",
    contacts="DSO",
    actions=(
        "Lower the stop logs 4 inches or more, if not already done.",
    ),
    short_actions=(
        "Lower stop logs 4\"+",
    ),
)

EMERGENCY_LEVEL_2 = EAPLevel(
    rank=3,
    gauge_ft=3.30,
    severity="warning",
    color="#b42318",
    pole_color="red",
    title="Emergency Level 2",
    audience="emergency",
    contacts="NORCOM, KCOEM, DSO",
    actions=(
        "Alert NORCOM, KCOEM and DSO.",
        "Water is up two feet: contact Crystal Lake residents on the east side of the dam who"
        " might want to have their cars on the west side, in case the road or bridge is"
        " damaged.",
    ),
    short_actions=(
        "Alert NORCOM · KCOEM · DSO",
        "East-side cars to west side",
    ),
)

BRIDGE_CLOSURE = EAPLevel(
    rank=4,
    gauge_ft=3.90,
    severity="critical",
    color="#9c1f24",
    pole_color="red",
    title="Bridge Closure",
    audience="road",
    contacts="NORCOM, KCOEM, DSO",
    actions=(
        "Crystal Lake bridge SHALL be closed to all vehicle traffic.",
        "Overtopping starts 25’ east of the bridge.",
        "Start covering the downstream slope with tarps and plastic, secured with sandbags,"
        " at the point of overtopping.",
        "Remove the remaining stop logs from the primary spillway, if not already done.",
    ),
    short_actions=(
        "Close the bridge",
        "Overtopping east of bridge",
        "Tarps · plastic · sandbags where it overtops",
        "Pull remaining stop logs",
    ),
)

EMERGENCY_LEVEL_3 = EAPLevel(
    rank=5,
    gauge_ft=4.40,
    severity="emergency",
    color="#6d1220",
    pole_color="red",
    title="Emergency Level 3",
    audience="evacuate",
    contacts="NORCOM, KCOEM, DSO",
    actions=(
        "Bridge deck is overtopped.",
        'Alert NORCOM, KCOEM and DSO: overtopping by 6" for 12 hours is “imminent failure of'
        ' the dam.”',
    ),
    short_actions=(
        "Bridge deck overtopped",
        "Alert NORCOM · KCOEM · DSO: imminent failure",
    ),
)

EAP_LEVELS: tuple[EAPLevel, ...] = (
    EMERGENCY_LEVEL_1, LOWER_STOP_LOGS, EMERGENCY_LEVEL_2, BRIDGE_CLOSURE, EMERGENCY_LEVEL_3,
)

# The table's pool rows (green): stop-log heights, not alert levels. Shown on the spillway
# drawing only; the model's crests come from the survey (artifact ``stop_logs``).
EAP_POOL_ROWS: tuple[tuple[float, str], ...] = (
    (0.65, "Winter, one extra stop log removed"),
    (0.97, "Winter, normal stop logs"),
    (1.30, "Summer, normal stop logs"),
)

# Re-arm 0.05 ft below the lowest level, so a lake hovering at it can't flap.
EAP_RESET_GAUGE_FT = 2.25
EAP_RESET_MINUTES = 30
EAP_AUDIENCES = tuple(dict.fromkeys(("ops", *(level.audience for level in EAP_LEVELS))))


def active_eap_rank(gauge_ft: float) -> int:
    """Highest EAP level active at ``gauge_ft``; zero means below 2.30 ft."""
    return max((level.rank for level in EAP_LEVELS if gauge_ft >= level.gauge_ft), default=0)


def eap_level(rank: int) -> EAPLevel | None:
    return next((level for level in EAP_LEVELS if level.rank == rank), None)
