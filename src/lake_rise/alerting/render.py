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
    banner = (
        "TEST" if is_test else
        "ALL CLEAR" if is_all_clear else
        (level_name or "ALERT")
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

    # EAP road-closure / action warnings: split into currently active vs. forecast-only.
    eap_active = [lv for lv in _EAP_LEVELS if current_ft >= lv["gauge_ft"]]
    eap_forecast = [lv for lv in _EAP_LEVELS if current_ft < lv["gauge_ft"] <= peak_ft]

    return {
        "kind": kind,
        "is_test": is_test,
        "is_all_clear": is_all_clear,
        "banner": banner,
        "level_name": level_name,
        "generated_at": to_pacific(decision.generated_at, tz),
        "horizon_hours": decision.horizon_hours,
        "horizon_days": round(decision.horizon_hours / 24, 1),
        "current_reading_ft": current_ft,
        "height_to_overtop": round(decision.freeboard_ft, 2),
        "eap_active": eap_active,
        "eap_forecast": eap_forecast,
        "data_fresh": decision.data_fresh,
        "p_early_warning_pct": round(decision.probabilities.get("early_warning", 0.0) * 100),
        "p_crest_pct": round(decision.probabilities.get("dam_crest", 0.0) * 100),
        "p_bridge_deck_pct": round(decision.probabilities.get("bridge_deck", 0.0) * 100),
        "has_bridge_deck": "bridge_deck" in decision.probabilities,
        "thresholds": thresholds,
        "peak_reading_ft": peak_ft,
        "peak_reading_high_ft": _gauge(decision.peak_elevation_high),
        "peak_at": to_pacific(decision.peak_at, tz),
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
