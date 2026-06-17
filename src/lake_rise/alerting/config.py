"""Environment-driven configuration for the alerting layer.

Everything operational lives here: the adjustable escalation ladder, the per-level
audiences (with cumulative resolution as severity rises), the independent test-level
trigger, channel credentials, and template overrides. Like ``settings.py`` this reads
from real env vars (with a ``.env`` fallback) so nothing is hard-coded in the model.

The model itself stays pure; this module is the only place alert *policy* is defined.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..settings import _load_dotenv_once

# Default ladder, used when ALERT_LEVELS is unset. Increasing severity. Each step:
#   name : threshold_label : min_prob : audience
# threshold_label references a level in the artifact's thresholds (early_warning / dam_crest /
# bridge_deck); min_prob is that step's probability cutoff; audience names the recipient group.
# dam_crest = initial dam overtopping (EAP bridge-closure); bridge_deck = bridge-deck overtopping,
# the EAP "imminent failure" / evacuate trigger -> its own top level and audience.
_DEFAULT_LEVELS = (
    "ADVISORY:early_warning:0.30:ops,"
    "WARNING:early_warning:0.60:ops,"
    "WATCH:dam_crest:0.10:ops,"
    "DANGER:dam_crest:0.30:emergency,"
    "CRITICAL:dam_crest:0.60:road,"
    "EVACUATE:bridge_deck:0.30:evacuate"
)


@dataclass(frozen=True)
class Recipients:
    """Resolved destinations for one dispatch, per channel."""
    emails: tuple[str, ...] = ()
    sms: tuple[str, ...] = ()

    def union(self, other: "Recipients") -> "Recipients":
        # Preserve order, de-duplicate across the union.
        return Recipients(
            emails=tuple(dict.fromkeys((*self.emails, *other.emails))),
            sms=tuple(dict.fromkeys((*self.sms, *other.sms))),
        )

    @property
    def is_empty(self) -> bool:
        return not self.emails and not self.sms


@dataclass(frozen=True)
class AlertLevel:
    rank: int            # 1-based; higher = more severe
    name: str
    threshold_label: str  # references art.thresholds_abs_ft.<label>
    min_prob: float       # cutoff on P(cross that threshold within horizon)
    audience: str         # recipient group name


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    user: str | None
    password: str | None
    sender: str
    starttls: bool

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)


@dataclass(frozen=True)
class TwilioConfig:
    account_sid: str | None
    auth_token: str | None
    from_number: str | None

    @property
    def configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number)


@dataclass(frozen=True)
class AlertConfig:
    enabled: bool
    interval_minutes: int
    horizon_hours: int
    timezone: str
    levels: tuple[AlertLevel, ...]
    audiences: dict[str, Recipients]
    test_enabled: bool
    test_rain_in: float
    test_audience: str
    monthly_test_enabled: bool
    monthly_test_dom: int       # day-of-month to send (1–28)
    monthly_test_audience: str
    drill_enabled: bool
    drill_dom: int              # day-of-month to send (1–28)
    drill_audience: str
    template_dir: Path | None
    send_all_clear: bool
    state_path: Path
    channels: tuple[str, ...]
    ui_base_url: str
    smtp: SMTPConfig
    twilio: TwilioConfig
    # Shared secret required to trigger a REAL send via the HTTP endpoint
    # (POST /alert/run?dry_run=false). None -> the HTTP send path is disabled entirely
    # (preview/dry-run stays open). The in-process scheduler and local CLI never need it.
    api_token: str | None = None

    # --- lookups --------------------------------------------------------------
    @property
    def max_rank(self) -> int:
        return max((lv.rank for lv in self.levels), default=0)

    def level_by_rank(self, rank: int) -> AlertLevel | None:
        return next((lv for lv in self.levels if lv.rank == rank), None)

    def audience_recipients(self, name: str) -> Recipients:
        return self.audiences.get(name.lower(), Recipients())

    def resolve_recipients(self, rank: int) -> Recipients:
        """Cumulative union of the audiences of levels 1..rank — so the small initial
        audience always gets it and broader (emergency / road-closure) contacts are
        added only once the severe levels are reached."""
        out = Recipients()
        for lv in self.levels:
            if lv.rank <= rank:
                out = out.union(self.audience_recipients(lv.audience))
        return out


def _csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _parse_levels(raw: str) -> tuple[AlertLevel, ...]:
    levels: list[AlertLevel] = []
    for i, entry in enumerate(_csv(raw), start=1):
        parts = [p.strip() for p in entry.split(":")]
        if len(parts) != 4:
            raise ValueError(
                f"ALERT_LEVELS entry {entry!r} must be name:threshold_label:min_prob:audience"
            )
        name, label, prob, audience = parts
        levels.append(AlertLevel(
            rank=i, name=name, threshold_label=label,
            min_prob=float(prob), audience=audience.lower(),
        ))
    return tuple(levels)


def _collect_audiences(group_names: set[str]) -> dict[str, Recipients]:
    """Read ALERT_AUDIENCE_<GROUP>_EMAIL / _SMS for each referenced group."""
    out: dict[str, Recipients] = {}
    for group in group_names:
        env = group.upper()
        emails = _csv(os.getenv(f"ALERT_AUDIENCE_{env}_EMAIL"))
        sms = _csv(os.getenv(f"ALERT_AUDIENCE_{env}_SMS"))
        out[group.lower()] = Recipients(emails=emails, sms=sms)
    return out


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def alert_config_from_env() -> AlertConfig:
    """Build the AlertConfig from environment (with the shared ``.env`` fallback)."""
    _load_dotenv_once()

    levels = _parse_levels(os.getenv("ALERT_LEVELS") or _DEFAULT_LEVELS)
    test_audience = (os.getenv("ALERT_TEST_AUDIENCE") or "test").lower()
    monthly_test_audience = (os.getenv("ALERT_MONTHLY_TEST_AUDIENCE") or "ops").lower()
    drill_audience = (os.getenv("ALERT_DRILL_AUDIENCE") or "ops").lower()
    group_names = ({lv.audience for lv in levels}
                   | {test_audience} | {monthly_test_audience} | {drill_audience})
    audiences = _collect_audiences(group_names)

    tmpl = os.getenv("ALERT_TEMPLATE_DIR")
    state = os.getenv("ALERT_STATE_PATH") or "artifacts/alert_state.json"

    smtp = SMTPConfig(
        host=os.getenv("SMTP_HOST", ""),
        port=int(os.getenv("SMTP_PORT", "587")),
        user=os.getenv("SMTP_USER"),
        password=os.getenv("SMTP_PASSWORD"),
        sender=os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")),
        starttls=_bool("SMTP_STARTTLS", True),
    )
    twilio = TwilioConfig(
        account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
        from_number=os.getenv("TWILIO_FROM"),
    )

    return AlertConfig(
        enabled=_bool("ALERT_ENABLED", False),
        interval_minutes=int(os.getenv("ALERT_INTERVAL_MINUTES", "60")),
        horizon_hours=int(os.getenv("ALERT_HORIZON_HOURS", "72")),
        timezone=os.getenv("ALERT_TIMEZONE", "America/Los_Angeles"),
        levels=levels,
        audiences=audiences,
        test_enabled=_bool("ALERT_TEST_ENABLED", False),
        test_rain_in=float(os.getenv("ALERT_TEST_RAIN_IN", "0.10")),
        test_audience=test_audience,
        monthly_test_enabled=_bool("ALERT_MONTHLY_TEST_ENABLED", False),
        monthly_test_dom=int(os.getenv("ALERT_MONTHLY_TEST_DOM", "1")),
        monthly_test_audience=monthly_test_audience,
        drill_enabled=_bool("ALERT_DRILL_ENABLED", False),
        drill_dom=int(os.getenv("ALERT_DRILL_DOM", "1")),
        drill_audience=drill_audience,
        template_dir=Path(tmpl) if tmpl else None,
        send_all_clear=_bool("ALERT_SEND_ALL_CLEAR", True),
        state_path=Path(state),
        channels=_csv(os.getenv("ALERT_CHANNELS", "email,sms")),
        ui_base_url=(os.getenv("ALERT_UI_BASE_URL", "")).rstrip("/"),
        smtp=smtp,
        twilio=twilio,
        api_token=os.getenv("ALERT_API_TOKEN") or None,
    )
