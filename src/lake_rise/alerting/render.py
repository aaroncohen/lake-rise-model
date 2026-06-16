"""Template-based content builder.

Two stages: build a Pacific-time-formatted context dict (pure), then fill the
editable Jinja2 templates. Email and SMS have separate templates; the same set
serves real, test, and all-clear notices (a banner flag distinguishes them).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import AlertConfig
from .rules import AlertDecision

_BUILTIN_TEMPLATES = Path(__file__).resolve().parent / "templates"

# Proactive operator actions to recommend for any active alert, before or alongside
# the formal EAP thresholds.  These are good-practice steps that apply whenever a
# significant rise is forecast; dam operators use their judgment on timing.
_PROACTIVE_OPERATOR_ACTIONS = [
    "Remove stop logs from the primary spillway to increase outflow capacity and lower"
    " the lake level before peak inflow arrives.",
    "Inspect both spillways for debris and clear any obstructions.",
    "Increase monitoring to at least every 2 hours; every hour if the level is rising rapidly.",
]

# EAP action levels (Crystal Lake Emergency Action Plan).
# Gauge readings (ft above stick zero) with required actions.
_EAP_LEVELS = [
    {
        "gauge_ft": 3.30,
        "severity": "warning",
        "title": "Mandatory Alert",
        "contacts": "DSO, SMO, RCEC",
        "actions": [
            "Follow EAP flowchart (Appendix A) and notify all contacts (EAP p. 25).",
            "Crystal Lake residents on the east side of the lake should move cars to the west side"
            " in case of road or bridge damage.",
            "Begin sandbagging and cover the downstream slope with plastic.",
        ],
    },
    {
        "gauge_ft": 3.90,
        "severity": "critical",
        "title": "Bridge Closure",
        "contacts": "DSO, SMO, RCEC",
        "actions": [
            "Overtopping begins 25’ east of the bridge.",
            "Crystal Lake bridge SHALL be closed to all vehicle traffic.",
        ],
    },
    {
        "gauge_ft": 4.40,
        "severity": "emergency",
        "title": "Evacuate Downstream",
        "contacts": "NORCOM, KCDOT; re-contact DSO, SMO, RCEC",
        "actions": [
            "Bridge deck is overtopped.",
            'Notify NORCOM and KCDOT of “imminent failure of the dam.”',
            "Evacuate downstream.",
        ],
    },
]


@dataclass(frozen=True)
class RenderedAlert:
    subject: str
    text_body: str
    html_body: str
    sms_body: str


def _env(config: AlertConfig) -> Environment:
    # Operator override dir first (if any), then the packaged defaults.
    search = [str(_BUILTIN_TEMPLATES)]
    if config.template_dir is not None:
        search.insert(0, str(config.template_dir))
    return Environment(
        loader=FileSystemLoader(search),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def to_pacific(dt: datetime | None, tzname: str) -> str | None:
    """Format a tz-aware (UTC) datetime in the configured zone, e.g.
    'Sat Jun 13, 3:00 PM PDT'. Returns None for None so templates can branch."""
    if dt is None:
        return None
    local = dt.astimezone(ZoneInfo(tzname))
    # Strip the leading zero from the hour for readability (cross-platform).
    return local.strftime("%a %b %-d, %-I:%M %p %Z")


def build_context(decision: AlertDecision, config: AlertConfig, kind: str,
                  level_name: str | None) -> dict:
    tz = config.timezone
    is_test = kind == "TEST"
    is_all_clear = kind in ("ALL_CLEAR", "TEST_CLEAR")

    # Human-facing level names. The bridge-deck level is NOT an instruction for the
    # recipient to evacuate — recipients are dam operators / EAP contacts, and the level
    # tells them to alert and evacuate the downstream zone. Name it so it can't be misread.
    _LEVEL_DISPLAY = {"EVACUATE": "Downstream Evac Notice"}
    level_display = _LEVEL_DISPLAY.get(level_name, level_name)
    banner = (
        "TEST" if is_test else
        "ALL CLEAR" if is_all_clear else
        (level_display or "ALERT")
    )

    _LABEL_DISPLAY = {
        "dam_crest": "Dam Overtop",
        "early_warning": "Early Warning",
        "bridge_deck": "Bridge Deck / Road Closure",
    }

    offset = decision.datum_offset_ft

    def _gauge(abs_ft: float) -> float:
        """Convert absolute elevation to gauge stick reading."""
        return round(abs_ft - offset, 2)

    thresholds = []
    for t in decision.thresholds:
        thresholds.append({
            "label": t.label,
            "label_pretty": _LABEL_DISPLAY.get(t.label, t.label.replace("_", " ").title()),
            "gauge_reading_ft": _gauge(t.elevation),
            "probability_pct": round(t.probability * 100),
            "median_cross_at": to_pacific(t.median_cross_at, tz),
            "earliest_cross_at": to_pacific(t.earliest_cross_at, tz),
        })

    current_ft = _gauge(decision.current_elevation)
    peak_ft = _gauge(decision.peak_elevation)

    # Named threshold levels, sorted low->high, for relative-position context.
    levels_sorted = sorted(
        ((_LABEL_DISPLAY.get(t.label, t.label.replace("_", " ").title()), t.elevation, _gauge(t.elevation))
         for t in decision.thresholds),
        key=lambda x: x[1])

    def _pos(label_pretty: str, gauge: float, delta: float) -> dict:
        return {"label_pretty": label_pretty, "gauge_ft": gauge, "delta_ft": round(delta, 2)}

    below = [l for l in levels_sorted if l[1] <= decision.current_elevation]
    above = [l for l in levels_sorted if l[1] > decision.current_elevation]
    pos_below = _pos(below[-1][0], below[-1][2], decision.current_elevation - below[-1][1]) if below else None
    pos_above = _pos(above[0][0], above[0][2], above[0][1] - decision.current_elevation) if above else None

    # Episode high-water mark + the highest named threshold it exceeded (ALL_CLEAR only).
    ep_abs = decision.episode_peak_elevation
    episode_peak_ft = _gauge(ep_abs) if ep_abs is not None else None
    crossed = [l for l in levels_sorted if ep_abs is not None and l[1] <= ep_abs]
    peak_threshold = {"label_pretty": crossed[-1][0], "gauge_ft": crossed[-1][2]} if crossed else None

    # Road/bridge was closed if the lake overtopped the dam crest this event (EAP: the bridge
    # is closed to traffic once overtopping begins). Reopening then needs a safety inspection.
    dam_crest = decision.threshold("dam_crest")
    road_closure_cleared = (ep_abs is not None and dam_crest is not None
                            and ep_abs >= dam_crest.elevation)

    f24, f24h = decision.forecast_elev_24h, decision.forecast_elev_24h_high

    # Pre-flatten the optional ALL_CLEAR qualifiers into strings so each template line ends
    # with a value (Jinja's trim_blocks otherwise collapses lines that end in {% endif %}).
    ep_at_str = to_pacific(decision.episode_peak_at, tz)
    peak_line_extra = (f" — above {peak_threshold['label_pretty']} ({peak_threshold['gauge_ft']} ft)"
                       if peak_threshold else " — stayed below every alert threshold")
    if ep_at_str:
        peak_line_extra += f", reached {ep_at_str}"
    _pos_parts = []
    if pos_below:
        _pos_parts.append(f"{pos_below['delta_ft']} ft above {pos_below['label_pretty']} "
                          f"({pos_below['gauge_ft']} ft)")
    if pos_above:
        _pos_parts.append(f"{pos_above['delta_ft']} ft below {pos_above['label_pretty']} "
                          f"({pos_above['gauge_ft']} ft)")
    current_line_extra = (", " + ", ".join(_pos_parts)) if _pos_parts else ""
    f24_ft = _gauge(f24) if f24 is not None else None
    f24h_ft = _gauge(f24h) if f24h is not None else None
    # Only show the wettest-scenario figure when it actually differs (a dry forecast
    # collapses the band, and "up to X" repeating the median reads oddly).
    forecast24_line_extra = (f" (up to {f24h_ft} ft in the wettest scenario)"
                             if f24h_ft is not None and f24h_ft != f24_ft else "")

    # EAP road-closure / action warnings: split into currently active vs. forecast-only.
    eap_active = [lv for lv in _EAP_LEVELS if current_ft >= lv["gauge_ft"]]
    eap_forecast = [lv for lv in _EAP_LEVELS if current_ft < lv["gauge_ft"] <= peak_ft]

    # Bridge/road is physically closed once a critical or emergency EAP level is active.
    bridge_closed = any(lv["severity"] in ("critical", "emergency") for lv in eap_active)

    # Suggest residents move vehicles before the road closes: when dam overtop is meaningfully
    # probable or EAP levels are forecast, but the bridge is not yet closed.
    suggest_vehicle_relocation = not bridge_closed and (
        round(decision.probabilities.get("dam_crest", 0.0) * 100) >= 15
        or bool(eap_active)
        or bool(eap_forecast)
    )

    # Downstream evacuation is in play (now or forecast) -> recipients must understand the
    # message tells them to alert/evacuate the DOWNSTREAM zone, not themselves.
    downstream_evac = any(lv["severity"] == "emergency" for lv in (*eap_active, *eap_forecast))
    downstream_note_sms = ("\nDam operators are notifying downstream residents through the proper "
                           "channels. Not an instruction for you to evacuate."
                           if downstream_evac else "")

    return {
        "kind": kind,
        "is_test": is_test,
        "is_all_clear": is_all_clear,
        "banner": banner,
        "level_name": level_name,
        "level_display": level_display,
        "downstream_evac": downstream_evac,
        "downstream_note_sms": downstream_note_sms,
        "generated_at": to_pacific(decision.generated_at, tz),
        "horizon_hours": decision.horizon_hours,
        "horizon_days": round(decision.horizon_hours / 24, 1),
        "current_reading_ft": current_ft,
        "height_to_overtop": round(decision.freeboard_ft, 2),
        "freeboard_str": (
            f"{round(decision.freeboard_ft, 2)} ft below dam overtop"
            if decision.freeboard_ft > 0 else
            f"overtopping by {round(-decision.freeboard_ft, 2)} ft"
            if decision.freeboard_ft < 0 else
            "at dam overtop"
        ),
        "freeboard_str_sms": (
            f"{round(decision.freeboard_ft, 2)}ft to overtop"
            if decision.freeboard_ft > 0 else
            f"overtopping {round(-decision.freeboard_ft, 2)}ft"
            if decision.freeboard_ft < 0 else
            "at overtop"
        ),
        "eap_active": eap_active,
        "eap_forecast": eap_forecast,
        "proactive_actions": _PROACTIVE_OPERATOR_ACTIONS,
        "bridge_closed": bridge_closed,
        "suggest_vehicle_relocation": suggest_vehicle_relocation,
        "data_fresh": decision.data_fresh,
        "p_early_warning_pct": round(decision.probabilities.get("early_warning", 0.0) * 100),
        "p_crest_pct": round(decision.probabilities.get("dam_crest", 0.0) * 100),
        "p_bridge_deck_pct": round(decision.probabilities.get("bridge_deck", 0.0) * 100),
        "has_bridge_deck": "bridge_deck" in decision.probabilities,
        "thresholds": thresholds,
        "peak_reading_ft": peak_ft,
        "peak_reading_high_ft": _gauge(decision.peak_elevation_high),
        "peak_at": to_pacific(decision.peak_at, tz),
        # ALL_CLEAR context: how high the lake got, where it sits now vs. nearest thresholds,
        # and where it's headed over the next 24 h.
        "episode_peak_ft": episode_peak_ft,
        "episode_peak_at": ep_at_str,
        "peak_threshold": peak_threshold,
        "pos_below": pos_below,
        "pos_above": pos_above,
        "forecast_24h_ft": f24_ft,
        "forecast_24h_high_ft": f24h_ft,
        "peak_line_extra": peak_line_extra,
        "current_line_extra": current_line_extra,
        "forecast24_line_extra": forecast24_line_extra,
        "road_closure_cleared": road_closure_cleared,
        # Pre-flattened with a leading newline so the compact SMS keeps it on its own line.
        "road_note_sms": ("\nROAD/BRIDGE: closed — do not drive on it until safety inspection clears it."
                          if road_closure_cleared else ""),
        "forecast_total_in": decision.forecast_total_in,
        "peak_rain_hour": decision.peak_rain_hour,
        "confidence_pct": decision.confidence_pct,
        "confidence_label": decision.confidence_label,
        "ui_url": f"{config.ui_base_url}/?mode=live" if config.ui_base_url else "",
        "timezone": tz,
    }


def render(decision: AlertDecision, config: AlertConfig, kind: str,
           level_name: str | None = None) -> RenderedAlert:
    ctx = build_context(decision, config, kind, level_name)
    env = _env(config)
    return RenderedAlert(
        subject=env.get_template("email_subject.txt").render(**ctx).strip(),
        text_body=env.get_template("email_body.txt").render(**ctx),
        html_body=env.get_template("email_body.html").render(**ctx),
        sms_body=env.get_template("sms_body.txt").render(**ctx).strip(),
    )
