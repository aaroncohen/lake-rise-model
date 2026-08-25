"""Template-based content builder.

Two stages: build a Pacific-time-formatted context dict (pure), then fill the
editable Jinja2 templates. Email and SMS have separate templates; the same set
serves real, test, and all-clear notices (a banner flag distinguishes them).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import AlertConfig
from .eap import EAP_LEVELS, EAPLevel, eap_level
from .rules import AlertDecision

_BUILTIN_TEMPLATES = Path(__file__).resolve().parent / "templates"

def _preventative_actions(*, stop_logs: int | None, eap_active: bool, eap_likely: bool,
                          p_crest_pct: int) -> list[str]:
    """Preventative measures scaled to the situation, ahead of the formal EAP thresholds.

    A fixed list reads as boilerplate and gets skipped: pulling boards is an over-reaction
    at a 0%-overtop advisory and impossible when they are already out. Each measure is
    emitted only when it is the right call, so the ones that appear are the ones that mean
    something.
    """
    acts: list[str] = []

    # Boards only come out when a real rise is coming -- and only if any are still in.
    if eap_active or eap_likely or p_crest_pct >= 10:
        if stop_logs is None:
            acts.append("Pull stop logs from the primary spillway to increase outflow capacity"
                        " and draw the lake down ahead of peak inflow.")
        elif stop_logs > 0:
            acts.append(f"Pull the {stop_logs} stop log{'s' if stop_logs > 1 else ''} from the"
                        " primary spillway to increase outflow capacity and draw the lake down"
                        " ahead of peak inflow.")
        else:
            acts.append("Stop logs are already out: the primary spillway is at bare sill, so"
                        " there is no further outflow capacity to gain there.")

    acts.append("Inspect both spillways and clear debris.")
    acts.append("Increase monitoring frequency.")
    return acts

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
                  level_name: str | None, *, observed_rank: int = 0,
                  observed_gauge_ft: float | None = None,
                  observed_detected_at: datetime | None = None,
                  observed_degraded: bool = False,
                  observed_degraded_reason: str | None = None,
                  observed_previous_rank: int = 0) -> dict:
    tz = config.timezone
    # TEST_CLEAR closes out a TEST notice, so it must carry the same [TEST] labeling as
    # the notice that opened it -- otherwise the closing message reads as an unrelated,
    # unlabeled ALL CLEAR with no obvious tie back to the test track.
    is_test = kind in ("TEST", "TEST_CLEAR")
    is_all_clear = kind in ("ALL_CLEAR", "TEST_CLEAR")
    is_observed = kind == "EAP_CROSSING"
    observed_level = eap_level(observed_rank) if is_observed else None

    # The bridge-deck level is not an instruction for the recipient to evacuate: recipients
    # are dam operators / EAP contacts, and the level tells them to evacuate the downstream
    # zone. Name it for what it is so it can't be misread.
    _LEVEL_DISPLAY = {"EVACUATE": "Downstream Evacuation"}
    level_display = _LEVEL_DISPLAY.get(level_name, level_name)
    if level_display is None and kind == "TEST_CLEAR":
        # TEST_CLEAR carries no ladder level (it closes the independent rain-test track,
        # not an escalation), so the generic "This clears the prior {{ level_display }}
        # notice" line needs its own label instead of literally rendering the word "None".
        level_display = "Test Rain Advisory"
    banner = (
        (("EAP THRESHOLD INDICATED" if observed_degraded else "EAP THRESHOLD CROSSED")
         + f" — {level_display}") if is_observed else
        # is_all_clear first: TEST_CLEAR is both is_test and is_all_clear, and the
        # "returned to normal" meaning must win the banner headline over the generic
        # "TEST" one; the [TEST] subject/body labeling (driven by is_test) still applies.
        "ALL CLEAR" if is_all_clear else
        "TEST" if is_test else
        (level_display or "ALERT")
    )

    # Header colour follows the ladder position rather than a flat red -- an ADVISORY and a
    # downstream evacuation must not look identical in the inbox. Keyed off the level's rank
    # as a fraction of the configured ladder, so a re-tuned ALERT_LEVELS still ramps cleanly.
    _SEVERITY_RAMP = ("#2f7a52", "#7a7126", "#996020", "#b34a1a", "#9c1f24", "#6d1220")
    _rank = next((lv.rank for lv in config.levels if lv.name == level_name), None)
    _top_rank = max((lv.rank for lv in config.levels), default=1)
    if is_observed and observed_level:
        banner_color = observed_level.color
    elif is_all_clear:
        banner_color = "#1c5d80"          # calm, and distinct from every alert step
    elif _rank is None:
        banner_color = "#555555"          # TEST and anything off-ladder
    else:
        _i = 0 if _top_rank <= 1 else round((_rank - 1) / (_top_rank - 1) * (len(_SEVERITY_RAMP) - 1))
        banner_color = _SEVERITY_RAMP[_i]

    _LABEL_DISPLAY = {
        "dam_crest": "Dam Overtop",
        "early_warning": "Early Warning",
        "bridge_deck": "Bridge Deck / Road Closure",
    }

    offset = decision.datum_offset_ft

    def _gauge(abs_ft: float) -> float:
        """Convert absolute elevation to gauge stick reading."""
        return round(abs_ft - offset, 2)

    def _ft(x: float | None) -> str | None:
        """Every foot value the notices print goes through here. Bare floats drop the
        trailing zero (4.80 -> "4.8", 1.30 -> "1.3"), which reads as sloppy precision
        next to the EAP's own 2-dp gauge levels (3.30, 3.90) -- so all of them are
        formatted the same way. Templates only ever test these for None."""
        return None if x is None else f"{x:.2f}"

    thresholds = []
    for t in decision.thresholds:
        thresholds.append({
            "label": t.label,
            "label_pretty": _LABEL_DISPLAY.get(t.label, t.label.replace("_", " ").title()),
            "gauge_reading_ft": _ft(_gauge(t.elevation)),
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
        return {"label_pretty": label_pretty, "gauge_ft": _ft(gauge), "delta_ft": _ft(delta)}

    below = [l for l in levels_sorted if l[1] <= decision.current_elevation]
    above = [l for l in levels_sorted if l[1] > decision.current_elevation]
    pos_below = _pos(below[-1][0], below[-1][2], decision.current_elevation - below[-1][1]) if below else None
    pos_above = _pos(above[0][0], above[0][2], above[0][1] - decision.current_elevation) if above else None

    # Episode high-water mark + the highest named threshold it exceeded (ALL_CLEAR only).
    ep_abs = decision.episode_peak_elevation
    episode_peak_ft = _gauge(ep_abs) if ep_abs is not None else None
    crossed = [l for l in levels_sorted if ep_abs is not None and l[1] <= ep_abs]
    peak_threshold = ({"label_pretty": crossed[-1][0], "gauge_ft": _ft(crossed[-1][2])}
                      if crossed else None)

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
    forecast24_line_extra = (f" (up to {_ft(f24h_ft)} ft in the wettest scenario)"
                             if f24h_ft is not None and f24h_ft != f24_ft else "")

    # EAP levels: active now, vs. reachable by the forecast peak. "Likely" = the median
    # scenario reaches it; "Possible" = only the wettest scenario does.
    peak_high_ft = _gauge(decision.peak_elevation_high)

    def _nearest_modelled(gauge_ft: float) -> dict | None:
        """The modelled threshold that lines up with this EAP gauge level, within 0.2 ft.
        EAP levels are quoted off the gauge stick and don't all coincide with a modelled
        threshold, so only report a probability when one genuinely lines up — and carry
        its name, so the number is never read as a probability of the EAP level itself."""
        if not thresholds:
            return None
        near = min(thresholds, key=lambda t: abs(float(t["gauge_reading_ft"]) - gauge_ft))
        if abs(float(near["gauge_reading_ft"]) - gauge_ft) > 0.2:
            return None
        return {"label_pretty": near["label_pretty"], "gauge_ft": near["gauge_reading_ft"],
                "probability_pct": near["probability_pct"]}

    def _eap_entry(lv: EAPLevel, likelihood: str) -> dict:
        # gauge_str keeps the EAP's own 2-dp gauge readings (3.30, 3.90) intact; a bare
        # float renders them as 3.3 / 3.9, which no longer matches the EAP document.
        return {
            "rank": lv.rank, "gauge_ft": lv.gauge_ft, "severity": lv.severity,
            "color": lv.color, "title": lv.title, "audience": lv.audience,
            "contacts": lv.contacts, "actions": lv.actions, "likelihood": likelihood,
            "gauge_str": f"{lv.gauge_ft:.2f}",
            "nearest": _nearest_modelled(lv.gauge_ft),
        }

    eap_active = [_eap_entry(lv, "Active") for lv in EAP_LEVELS
                  if (lv.rank <= observed_rank if is_observed else current_ft >= lv.gauge_ft)]
    eap_forecast = [_eap_entry(lv, "Likely" if lv.gauge_ft <= peak_ft else "Possible")
                    for lv in EAP_LEVELS if current_ft < lv.gauge_ft <= peak_high_ft]
    # Split by how solid the reach is. A level only the wettest scenario touches is a
    # heads-up, not a job list: printing its full EAP actions (sandbag, notify everyone,
    # move vehicles) next to a 7% overtop probability reads as a call to act and buries
    # the levels that genuinely need staging.
    eap_likely = [lv for lv in eap_forecast if lv["likelihood"] == "Likely"]
    eap_possible = [lv for lv in eap_forecast if lv["likelihood"] == "Possible"]

    # Bridge/road is physically closed once a critical or emergency EAP level is active.
    bridge_closed = any(lv["severity"] in ("critical", "emergency") for lv in eap_active)

    # Whole numbers of days render as "3", not "3.0".
    _days = decision.horizon_hours / 24
    horizon_days = int(_days) if _days == int(_days) else round(_days, 1)
    freeboard_str = (
        f"{_ft(decision.freeboard_ft)} ft below dam overtop"
        if decision.freeboard_ft > 0 else
        f"overtopping by {_ft(-decision.freeboard_ft)} ft"
        if decision.freeboard_ft < 0 else
        "at dam overtop"
    )
    p_crest_pct = round(decision.probabilities.get("dam_crest", 0.0) * 100)
    p_bridge_deck_pct = round(decision.probabilities.get("bridge_deck", 0.0) * 100)
    has_bridge_deck = "bridge_deck" in decision.probabilities

    def _titles(levels: list[dict], with_gauge: bool = False) -> str:
        return ", ".join(f"{lv['title']} ({lv['gauge_str']} ft)" if with_gauge else lv["title"]
                         for lv in levels)

    # The action verdict — the one line a reader must take away. Every state answers
    # "is action needed, and what": either an EAP level or the preventative measures.
    # EAP states are tested before the all-clear ones: an all-clear says the ladder has
    # dropped back to normal, not that the forecast is empty, so it must never assert
    # "nothing forecast" over an eap_forecast the reader can see in section 3 below.
    if eap_active:
        action_state = "now"
        action_headline = "ACTION REQUIRED NOW"
        action_detail = (f"EAP {_titles(eap_active)} active. Notify {eap_active[-1]['contacts']}"
                         " and carry out the required actions below.")
    elif eap_likely:
        action_state = "prepare"
        action_headline = "ACTION LIKELY REQUIRED"
        action_detail = (f"The forecast peak reaches EAP {_titles(eap_likely, True)}."
                         " Take the preventative measures now and stage the EAP response.")
    elif eap_forecast:
        action_state = "monitor"
        # "No EAP action" rather than "action possible": the preventative measures below
        # are not EAP action, and at this reach the honest instruction is to watch it.
        action_headline = "MONITOR — NO EAP ACTION NOW"
        action_detail = (f"EAP {_titles(eap_possible, True)} is reached only if the storm runs"
                         " at the high end of the forecast. Take the preventative measures and"
                         " re-check at the next update.")
    elif is_all_clear:
        action_state = "none"
        action_headline = "NO ACTION REQUIRED"
        action_detail = "The lake has receded and no EAP level is active or forecast."
    else:
        action_state = "preventative"
        action_headline = "NO EAP ACTION FORECAST"
        action_detail = "No EAP level is active or forecast. Take the preventative measures below."

    # The bridge stays shut after an overtopping event regardless of what else is in play,
    # so this requirement rides on top of whichever verdict was reached.
    if road_closure_cleared:
        if action_state == "none":
            action_state, action_headline = "inspect", "ACTION REQUIRED — BRIDGE INSPECTION"
            action_detail = ""
        action_detail = (action_detail + " The lake overtopped the dam crest this event; the"
                         " bridge stays closed to traffic until a safety inspection clears"
                         " it.").lstrip()

    # Summary: the facts that set up the verdict, in two lines.
    peak_when = f" expected {to_pacific(decision.peak_at, tz)}" if decision.peak_at else ""
    if is_all_clear:
        summary_lines = [
            f"Lake has receded to {_ft(current_ft)} ft, clearing the prior {level_display} notice."
            + (f" Event peak was {_ft(episode_peak_ft)} ft." if episode_peak_ft is not None else ""),
            (f"Dam overtop probability {p_crest_pct}% over the next {horizon_days} days."
             if eap_active or eap_forecast else
             f"No alert level is expected in the next {horizon_days} days."),
        ]
    else:
        risk = f"Dam overtop probability {p_crest_pct}% over the next {horizon_days} days."
        if has_bridge_deck:
            risk += f" Bridge deck / road closure {p_bridge_deck_pct}%."
        summary_lines = [
            f"Lake is at {_ft(current_ft)} ft, {freeboard_str}. Forecast peak {_ft(peak_ft)} ft"
            f"{peak_when}, up to {_ft(peak_high_ft)} ft in the wettest scenario.",
            risk,
        ]
    if not decision.data_fresh:
        summary_lines.append("Lake gauge is not reporting — readings may be stale.")

    observed_new_levels = [lv for lv in eap_active if lv["rank"] > observed_previous_rank]
    if is_observed:
        detected = to_pacific(observed_detected_at, tz)
        signal = "instantaneous gauge reading" if observed_degraded else "15-minute gauge average"
        crossed_names = ", ".join(
            f"{lv['title']} ({lv['gauge_str']} ft)" for lv in observed_new_levels)
        qualifier = (
            f" The rolling average was unavailable: {observed_degraded_reason or 'insufficient data'}."
            if observed_degraded else ""
        )
        summary_lines = [
            f"OBSERVED: the {signal} is {_ft(observed_gauge_ft)} ft, detected {detected}."
            f"{qualifier}",
            f"Newly active EAP threshold{'s' if len(observed_new_levels) != 1 else ''}:"
            f" {crossed_names}.",
            f"Forecast peak {_ft(peak_ft)} ft{peak_when}, up to {_ft(peak_high_ft)} ft in the"
            " wettest scenario.",
        ]
        if not decision.data_fresh:
            summary_lines.append(
                "Forecast inputs are degraded — gauge or rainfall data may be stale.")
        action_state = "now"
        action_headline = "ACTION REQUIRED NOW"
        action_detail = (f"Observed EAP {eap_active[-1]['title']} is active. Notify"
                         f" {eap_active[-1]['contacts']} and complete every required EAP step"
                         " listed below.")

    return {
        "kind": kind,
        "is_test": is_test,
        "is_all_clear": is_all_clear,
        "is_observed": is_observed,
        "banner": banner,
        "level_name": level_name,
        "level_display": level_display,
        "generated_at": to_pacific(decision.generated_at, tz),
        "horizon_hours": decision.horizon_hours,
        "horizon_days": horizon_days,
        # 1. Summary + verdict.
        "summary_lines": summary_lines,
        "observed_gauge_ft": _ft(observed_gauge_ft),
        "observed_detected_at": to_pacific(observed_detected_at, tz),
        "observed_degraded": observed_degraded,
        "observed_degraded_reason": observed_degraded_reason,
        "observed_new_levels": observed_new_levels,
        "action_state": action_state,
        "action_headline": action_headline,
        "action_detail": action_detail,
        "action_needed": action_state in ("now", "prepare", "inspect"),
        # 2. Status.
        "current_reading_ft": _ft(current_ft),
        "height_to_overtop": _ft(decision.freeboard_ft),
        "freeboard_str": freeboard_str,
        "freeboard_str_sms": (
            f"{_ft(decision.freeboard_ft)}ft to overtop"
            if decision.freeboard_ft > 0 else
            f"overtopping {_ft(-decision.freeboard_ft)}ft"
            if decision.freeboard_ft < 0 else
            "at overtop"
        ),
        "bridge_closed": bridge_closed,
        "data_fresh": decision.data_fresh,
        # 3. Forecast + required action.
        "eap_active": eap_active,
        "eap_forecast": eap_forecast,   # both groups, for the compact SMS line
        "eap_likely": eap_likely,
        "eap_possible": eap_possible,
        "preventative_actions": _preventative_actions(
            stop_logs=decision.stop_log_count, eap_active=bool(eap_active),
            eap_likely=bool(eap_likely), p_crest_pct=p_crest_pct),
        # Preventative measures are worth listing whenever something is still forecast --
        # including on an all-clear whose forward look has already picked up the next rise.
        "show_preventative": bool(not is_all_clear or eap_active or eap_forecast),
        "p_early_warning_pct": round(decision.probabilities.get("early_warning", 0.0) * 100),
        "p_crest_pct": p_crest_pct,
        "p_bridge_deck_pct": p_bridge_deck_pct,
        "has_bridge_deck": has_bridge_deck,
        "thresholds": thresholds,
        "peak_reading_ft": _ft(peak_ft),
        "peak_reading_high_ft": _ft(peak_high_ft),
        "peak_at": to_pacific(decision.peak_at, tz),
        # ALL_CLEAR context: how high the lake got, where it sits now vs. nearest thresholds,
        # and where it's headed over the next 24 h.
        "episode_peak_ft": _ft(episode_peak_ft),
        "episode_peak_at": ep_at_str,
        "peak_threshold": peak_threshold,
        "pos_below": pos_below,
        "pos_above": pos_above,
        "forecast_24h_ft": _ft(f24_ft),
        "forecast_24h_high_ft": _ft(f24h_ft),
        "peak_line_extra": peak_line_extra,
        "current_line_extra": current_line_extra,
        "forecast24_line_extra": forecast24_line_extra,
        "road_closure_cleared": road_closure_cleared,
        # Pre-flattened with a leading newline so the compact SMS keeps it on its own line.
        "road_note_sms": ("\nBRIDGE: closed until a safety inspection clears it."
                          if road_closure_cleared else ""),
        "forecast_total_in": decision.forecast_total_in,
        "peak_rain_hour": decision.peak_rain_hour,
        # "hour 18" makes the reader do arithmetic against the issue time; give the clock
        # time instead. peak_rain_hour is a 1-based index into the series from generated_at.
        "peak_rain_at": (to_pacific(decision.generated_at
                                    + timedelta(hours=decision.peak_rain_hour - 1), tz)
                         if decision.peak_rain_hour else None),
        "confidence_pct": decision.confidence_pct,
        "confidence_label": decision.confidence_label,
        "banner_color": banner_color,
        "ui_url": f"{config.ui_base_url}/?mode=live" if config.ui_base_url else "",
        "timezone": tz,
    }


def render(decision: AlertDecision, config: AlertConfig, kind: str,
           level_name: str | None = None, *, observed_rank: int = 0,
           observed_gauge_ft: float | None = None,
           observed_detected_at: datetime | None = None,
           observed_degraded: bool = False,
           observed_degraded_reason: str | None = None,
           observed_previous_rank: int = 0) -> RenderedAlert:
    ctx = build_context(
        decision, config, kind, level_name,
        observed_rank=observed_rank,
        observed_gauge_ft=observed_gauge_ft,
        observed_detected_at=observed_detected_at,
        observed_degraded=observed_degraded,
        observed_degraded_reason=observed_degraded_reason,
        observed_previous_rank=observed_previous_rank,
    )
    env = _env(config)
    return RenderedAlert(
        subject=env.get_template("email_subject.txt").render(**ctx).strip(),
        text_body=env.get_template("email_body.txt").render(**ctx),
        html_body=env.get_template("email_body.html").render(**ctx),
        sms_body=env.get_template("sms_body.txt").render(**ctx).strip(),
    )


# ---------------------------------------------------------------------------
# Monthly drill rendering — static simulated context, no live HA data needed
# ---------------------------------------------------------------------------

# Per-step offsets relative to named artifact thresholds (absolute ft).
# ADVISORY puts the lake *below* early_warning (approaching it); higher steps put it
# progressively closer to / above dam_crest.
_DRILL_OFFSETS: dict[str | None, tuple[str, float]] = {
    "ADVISORY":  ("early_warning", -0.50),  # below early_warning, approaching
    "DANGER":    ("dam_crest",     -0.40),  # above early_warning, below dam_crest
    "CRITICAL":  ("dam_crest",     -0.15),  # close to dam_crest
    "EVACUATE":  ("dam_crest",     +0.05),  # just overtopping
    None:        ("early_warning", -0.80),  # All Clear: well below every threshold
}

# Simulated crossing probabilities per step. Thresholds the simulated lake has already
# passed carry 1.00 (certain); thresholds still ahead carry realistic fractional values.
_DRILL_PROBS: dict[str | None, dict[str, float]] = {
    "ADVISORY": {"early_warning": 0.35, "dam_crest": 0.05, "bridge_deck": 0.01},
    "DANGER":   {"early_warning": 1.00, "dam_crest": 0.35, "bridge_deck": 0.05},
    "CRITICAL": {"early_warning": 1.00, "dam_crest": 0.65, "bridge_deck": 0.15},
    "EVACUATE": {"early_warning": 1.00, "dam_crest": 1.00, "bridge_deck": 0.35},
    None:       {"early_warning": 0.02, "dam_crest": 0.00, "bridge_deck": 0.00},
}

# Hours-to-crossing (median, earliest) relative to now. Negative = already crossed
# (renders as a past time, which is correct for thresholds the lake has surpassed).
_DRILL_CROSS_HOURS: dict[str | None, dict[str, tuple[int, int]]] = {
    "ADVISORY": {"early_warning": (12,  5), "dam_crest": (30, 14), "bridge_deck": (48, 24)},
    "DANGER":   {"early_warning": ( -6,-10), "dam_crest": (12,  4), "bridge_deck": (26, 12)},
    "CRITICAL": {"early_warning": (-10,-16), "dam_crest": ( 6,  2), "bridge_deck": (20,  9)},
    "EVACUATE": {"early_warning": (-18,-26), "dam_crest": ( -3, -7), "bridge_deck": ( 9,  4)},
    None:       {},
}


def render_drill(level_name: str | None, kind: str, config: AlertConfig,
                 art: "Artifact") -> RenderedAlert:  # noqa: F821 (Artifact imported below)
    """Render one step of the monthly drill using simulated (artifact-relative) values."""
    from datetime import datetime, timedelta, timezone

    from ..artifact import Artifact  # noqa: F811

    now = datetime.now(timezone.utc)

    thr_model = art.thresholds_abs_ft
    offset: float = art.datum.sensor_to_absolute_offset_ft

    # Build a plain label->elevation dict from the Pydantic Thresholds model,
    # keeping only the numeric threshold fields (skip helpers like freeboard_alert_below_ft).
    _THRESHOLD_LABELS = ("early_warning", "dam_crest", "bridge_deck")
    thresholds_abs: dict[str, float] = {
        lbl: getattr(thr_model, lbl)
        for lbl in _THRESHOLD_LABELS
        if getattr(thr_model, lbl, None) is not None
    }

    # ALL_CLEAR uses the None offset key (lake returning to below-normal) regardless of
    # what level_name is set to (which carries the prior-level name for template rendering).
    offset_key = None if kind == "ALL_CLEAR" else level_name
    anchor_label, delta = _DRILL_OFFSETS[offset_key]
    anchor_abs = thresholds_abs.get(anchor_label, next(iter(thresholds_abs.values())))
    current_abs = anchor_abs + delta
    freeboard = thresholds_abs.get("dam_crest", current_abs) - current_abs

    probs = _DRILL_PROBS[offset_key]
    peak_abs = current_abs + 0.20  # simulated peak slightly above current

    # Build TriggeredThreshold objects for every threshold in the artifact.
    # Thresholds with a cross_hours entry get simulated future crossing times;
    # those already passed by the simulated lake level get None (shown as already crossed).
    from .rules import TriggeredThreshold
    cross_hours = _DRILL_CROSS_HOURS[offset_key]
    thr_objs = tuple(
        TriggeredThreshold(
            label=lbl, elevation=elev,
            probability=probs.get(lbl, 0.0),
            median_cross_at=(now + timedelta(hours=cross_hours[lbl][0])
                             if lbl in cross_hours else None),
            earliest_cross_at=(now + timedelta(hours=cross_hours[lbl][1])
                               if lbl in cross_hours else None),
        )
        for lbl, elev in sorted(thresholds_abs.items(), key=lambda x: x[1])
    )

    # Determine active rank for the simulated level (used only to set context fields).
    active_rank = 0
    active_name = level_name
    if level_name is not None:
        for lv in config.levels:
            if lv.name == level_name:
                active_rank = lv.rank
                break

    from .rules import AlertDecision
    decision = AlertDecision(
        generated_at=now,
        horizon_hours=config.horizon_hours,
        current_elevation=current_abs,
        freeboard_ft=freeboard,
        datum_offset_ft=offset,
        data_fresh=True,
        active_rank=active_rank,
        active_level_name=active_name,
        probabilities=probs,
        thresholds=thr_objs,
        peak_elevation=peak_abs,
        peak_at=None,
        peak_elevation_high=peak_abs + 0.10,
        forecast_total_in=1.20,
        peak_rain_hour=6,
        confidence_pct=75,
        confidence_label="Medium",
        test_active=False,
        forecast_elev_24h=current_abs - 0.05,
        forecast_elev_24h_high=current_abs + 0.10,
        # For the All Clear step, surface a simulated episode peak.
        episode_peak_elevation=(thresholds_abs.get("dam_crest", anchor_abs) + 0.05
                                if kind == "ALL_CLEAR" else None),
        episode_peak_at=now if kind == "ALL_CLEAR" else None,
    )

    ctx = build_context(decision, config, kind=kind, level_name=level_name)

    # Patch context to identify this as a drill (templates branch on is_drill first).
    _LEVEL_DISPLAY = {"EVACUATE": "Downstream Evacuation"}
    level_display = _LEVEL_DISPLAY.get(level_name or "", level_name or "ALL CLEAR")
    drill_banner = f"{level_display} — MONTHLY DRILL"
    ctx.update({
        "is_drill": True,
        "is_test": True,   # keeps existing [TEST] SMS prefix and test-track guards happy
        "banner": drill_banner,
    })

    env = _env(config)
    return RenderedAlert(
        subject=env.get_template("email_subject.txt").render(**ctx).strip(),
        text_body=env.get_template("email_body.txt").render(**ctx),
        html_body=env.get_template("email_body.html").render(**ctx),
        sms_body=env.get_template("sms_body.txt").render(**ctx).strip(),
    )
