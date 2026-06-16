"""Local, send-safe helpers for validating the alerting config and templates.

Composes the existing predictor + evaluator + renderer so the CLI can (a) summarize the
escalation ladder and its routing, (b) render every configured tier's email/SMS, and
(c) preview the alert a what-if / historical storm would actually fire — with an opt-in
to email the rendered content to ``SMTP_USER``. SMS is never sent here, and nothing in
this module loads or writes the persisted alert state (``artifacts/alert_state.json``),
so previews are side-effect-free and safe to run repeatedly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..artifact import Artifact
from ..predict import PredictionResult, predict
from ..storms import bundle_for_storm, storm_series
from .channels.email_smtp import SMTPNotifier
from .config import AlertConfig, AlertLevel, Recipients
from .render import RenderedAlert, render
from .rules import AlertDecision, TriggeredThreshold, evaluate


# --- config / routing summary -------------------------------------------------------

@dataclass(frozen=True)
class TierRow:
    rank: int
    name: str
    threshold_label: str
    threshold_abs_ft: float | None   # None if the label isn't in the artifact
    gauge_ft: float | None           # stick reading = abs - datum offset
    min_prob: float
    audience: str
    recipients: Recipients           # cumulative (levels 1..rank)


@dataclass(frozen=True)
class ConfigSummary:
    rows: list[TierRow]
    warnings: list[str]
    channels: tuple[str, ...]
    smtp_configured: bool
    twilio_configured: bool
    test_audience: str
    test_recipients: Recipients


def summarize_config(art: Artifact, config: AlertConfig) -> ConfigSummary:
    """Build the tier-by-tier ladder/routing table plus a list of misconfiguration
    warnings. Pure inspection — no prediction is run."""
    offset = art.datum.sensor_to_absolute_offset_ft
    th = art.thresholds_abs_ft
    rows: list[TierRow] = []
    warnings: list[str] = []

    for lv in config.levels:
        abs_ft = getattr(th, lv.threshold_label, None)
        gauge = round(abs_ft - offset, 2) if abs_ft is not None else None
        rows.append(TierRow(
            rank=lv.rank, name=lv.name, threshold_label=lv.threshold_label,
            threshold_abs_ft=abs_ft, gauge_ft=gauge, min_prob=lv.min_prob,
            audience=lv.audience, recipients=config.resolve_recipients(lv.rank),
        ))
        if abs_ft is None:
            warnings.append(
                f"level {lv.name}: threshold '{lv.threshold_label}' is not defined in the "
                f"artifact thresholds — this tier can never fire")
        if config.audience_recipients(lv.audience).is_empty:
            warnings.append(
                f"level {lv.name}: audience '{lv.audience}' has no recipients "
                f"(set ALERT_AUDIENCE_{lv.audience.upper()}_EMAIL / _SMS)")

    # Channels enabled but not actually configured.
    if "email" in config.channels and not config.smtp.configured:
        warnings.append("channel 'email' is enabled but SMTP is not configured (SMTP_HOST/SMTP_FROM)")
    if "sms" in config.channels and not config.twilio.configured:
        warnings.append("channel 'sms' is enabled but Twilio is not configured "
                        "(TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM)")

    # Templates: render a synthetic decision so a broken template surfaces here.
    try:
        render(synthetic_decision(art, config), config, kind="LEVEL",
               level_name=config.levels[0].name if config.levels else None)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"template render failed: {exc}")

    return ConfigSummary(
        rows=rows, warnings=warnings, channels=config.channels,
        smtp_configured=config.smtp.configured, twilio_configured=config.twilio.configured,
        test_audience=config.test_audience,
        test_recipients=config.audience_recipients(config.test_audience),
    )


# --- synthetic decision (for rendering every tier without a real crossing) ----------

def synthetic_decision(art: Artifact, config: AlertConfig,
                       generated_at: datetime | None = None) -> AlertDecision:
    """A plausible high-risk decision used to render templates for every tier: current
    level near summer pool, forecast peak above the bridge deck, every threshold crossed
    with high probability and a crossing time. Deterministic; touches no live data."""
    start = generated_at or datetime.now(timezone.utc).replace(microsecond=0)
    th = art.thresholds_abs_ft
    top = th.bridge_deck if th.bridge_deck is not None else th.dam_crest
    current = art.stop_logs.control_elev(3)          # ~summer normal pool
    peak = round(top + 0.3, 2)

    # One TriggeredThreshold per defined threshold label, with descending probabilities.
    spec = [("early_warning", th.early_warning, 0.95, 18.0, 10.0),
            ("dam_crest", th.dam_crest, 0.70, 30.0, 20.0),
            ("bridge_deck", th.bridge_deck, 0.40, 42.0, 30.0)]
    thresholds = tuple(
        TriggeredThreshold(label=label, elevation=elev, probability=p,
                           median_cross_at=start + timedelta(hours=med),
                           earliest_cross_at=start + timedelta(hours=early))
        for label, elev, p, med, early in spec if elev is not None)
    probabilities = {t.label: t.probability for t in thresholds}

    return AlertDecision(
        generated_at=start, horizon_hours=config.horizon_hours,
        current_elevation=current, freeboard_ft=round(th.dam_crest - current, 2),
        datum_offset_ft=art.datum.sensor_to_absolute_offset_ft, data_fresh=True,
        active_rank=config.max_rank, active_level_name=(config.levels[-1].name if config.levels else None),
        probabilities=probabilities, thresholds=thresholds,
        peak_elevation=peak, peak_at=start + timedelta(hours=30),
        peak_elevation_high=round(peak + 0.3, 2),
        forecast_total_in=4.0, peak_rain_hour=6,
        confidence_pct=70, confidence_label="Medium", test_active=False,
    )


# --- render one / all tiers ---------------------------------------------------------

@dataclass(frozen=True)
class TierRender:
    label: str                 # "ADVISORY", ..., "TEST", "ALL_CLEAR"
    kind: str                  # LEVEL | TEST | ALL_CLEAR
    rank: int                  # 0 for TEST
    alert: RenderedAlert
    recipients: Recipients


def render_all_tiers(art: Artifact, config: AlertConfig,
                     decision: AlertDecision | None = None) -> list[TierRender]:
    """Render every configured ladder tier (banner + recipients per tier) plus the TEST
    and ALL_CLEAR notices, from one synthetic high-risk decision. The body context is
    shared across tiers by design; the per-tier differences are the banner and the
    cumulative recipients."""
    decision = decision or synthetic_decision(art, config)
    out: list[TierRender] = []
    for lv in config.levels:
        out.append(TierRender(
            label=lv.name, kind="LEVEL", rank=lv.rank,
            alert=render(decision, config, kind="LEVEL", level_name=lv.name),
            recipients=config.resolve_recipients(lv.rank)))
    out.append(TierRender(
        label="TEST", kind="TEST", rank=0,
        alert=render(decision, config, kind="TEST", level_name=None),
        recipients=config.audience_recipients(config.test_audience)))
    if config.levels:
        top = config.levels[-1]
        out.append(TierRender(
            label="ALL_CLEAR", kind="ALL_CLEAR", rank=top.rank,
            alert=render(decision, config, kind="ALL_CLEAR", level_name=top.name),
            recipients=config.resolve_recipients(top.rank)))
    return out


# --- storm-driven preview (real predict + evaluate) ---------------------------------

@dataclass(frozen=True)
class StormPreview:
    decision: AlertDecision
    result: PredictionResult
    fired: TierRender | None   # the tier the storm actually fires (None if rank 0)


def _level_by_name(config: AlertConfig, name: str) -> AlertLevel | None:
    return next((lv for lv in config.levels if lv.name.lower() == name.lower()), None)


def preview_storm(
    art: Artifact,
    config: AlertConfig,
    *,
    current_elevation_abs_ft: float,
    stop_log_count: int = 3,
    month: int = 1,
    preset: str | None = None,
    historical_id: str | None = None,
    hourly_in: list[float] | None = None,
    rate_in_per_hr: float | None = None,
    duration_h: int | None = None,
    start_offset_h: int = 0,
    horizon_h: int = 72,
    force_tier: str | None = None,
    as_of: datetime | None = None,
) -> StormPreview:
    """Run a what-if/historical storm through predict -> evaluate and render the tier it
    fires (highest-rank level whose threshold probability meets its cutoff). With
    ``force_tier`` the named tier is rendered against this storm's decision even if it
    didn't actually fire (so you can see any tier's content in a real storm context).
    Side-effect-free: no state is read or written."""
    as_of = as_of or datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    series = storm_series(
        art, preset=preset, historical_id=historical_id, hourly_in=hourly_in,
        rate_in_per_hr=rate_in_per_hr, duration_h=duration_h,
        start_offset_h=start_offset_h, horizon_h=horizon_h)
    bundle = bundle_for_storm(
        art, series, current_elevation_abs_ft=current_elevation_abs_ft,
        stop_log_count=stop_log_count, month=month, as_of=as_of)
    result = predict(bundle, art)
    decision = evaluate(result, bundle, art, config)

    level = _level_by_name(config, force_tier) if force_tier else config.level_by_rank(decision.active_rank)
    fired: TierRender | None = None
    if level is not None:
        fired = TierRender(
            label=level.name, kind="LEVEL", rank=level.rank,
            alert=render(decision, config, kind="LEVEL", level_name=level.name),
            recipients=config.resolve_recipients(level.rank))
    return StormPreview(decision=decision, result=result, fired=fired)


# --- outputs: write to disk / email to self -----------------------------------------

def write_rendered(out_dir: Path, label: str, alert: RenderedAlert) -> Path:
    """Write the rendered notice to ``out_dir/<label>/{subject.txt,body.txt,body.html,sms.txt}``
    and return that directory. Open ``body.html`` in a browser to validate the email."""
    d = out_dir / label
    d.mkdir(parents=True, exist_ok=True)
    (d / "subject.txt").write_text(alert.subject + "\n")
    (d / "body.txt").write_text(alert.text_body)
    (d / "body.html").write_text(alert.html_body)
    (d / "sms.txt").write_text(alert.sms_body + "\n")
    return d


def email_self(config: AlertConfig, alert: RenderedAlert) -> str:
    """Send the rendered email to SMTP_USER (yourself) via the real SMTP path, regardless
    of the configured alert audiences, so it can't page real contacts. SMS is never sent.
    Returns the address emailed; raises RuntimeError if SMTP isn't configured."""
    if not config.smtp.configured:
        raise RuntimeError("SMTP is not configured (set SMTP_HOST/SMTP_FROM, and SMTP_USER).")
    to = config.smtp.user or config.smtp.sender
    if not to:
        raise RuntimeError("No self address available (set SMTP_USER or SMTP_FROM).")
    SMTPNotifier(config.smtp).send(alert, Recipients(emails=(to,)))
    return to
