"""Render a calibration proposal to text/HTML and (optionally) email it. Reuses the alerting
``RenderedAlert`` value object and SMTP channel; builds its own Jinja environment."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..alerting.render import RenderedAlert
from .config import CalibrationConfig
from .state import Candidate

_BUILTIN = Path(__file__).resolve().parent / "templates"


def _jinja(template_path: Path | None) -> Environment:
    search = ([str(template_path)] if template_path else []) + [str(_BUILTIN)]
    return Environment(loader=FileSystemLoader(search),
                       autoescape=select_autoescape(["html"]),
                       trim_blocks=True, lstrip_blocks=True)


def render(candidate: Candidate, config: CalibrationConfig) -> RenderedAlert:
    env = _jinja(config.template_path)
    ctx = {"c": candidate, "config": config, "changed": candidate.changed_params}
    return RenderedAlert(
        subject=env.get_template("calibration_subject.txt").render(**ctx).strip(),
        text_body=env.get_template("calibration_body.txt").render(**ctx),
        html_body=env.get_template("calibration_body.html").render(**ctx),
        sms_body="",
    )


def email(candidate: Candidate, config: CalibrationConfig) -> str | None:
    """Send the proposal to ``CALIB_RECIPIENT`` via SMTP. Returns the recipient, or None if
    email is not configured (SMTP + recipient)."""
    if not (config.smtp.configured and config.recipient):
        return None
    from ..alerting.channels.email_smtp import SMTPNotifier
    from ..alerting.config import Recipients

    SMTPNotifier(config.smtp).send(render(candidate, config),
                                   Recipients(emails=(config.recipient,)))
    return config.recipient
