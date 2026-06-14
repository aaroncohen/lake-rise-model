"""Notifier protocol + the factory that builds the enabled channels.

Channels receive the resolved recipients per dispatch (audience is computed upstream
from the alert level), so a channel never owns a recipient list. Adding a new channel
is one module plus one line in ``build_notifiers``.
"""

from __future__ import annotations

import logging
from typing import Protocol

from ..config import AlertConfig, Recipients
from ..render import RenderedAlert

log = logging.getLogger("lake_rise.alerting")


class Notifier(Protocol):
    name: str

    def send(self, alert: RenderedAlert, recipients: Recipients) -> None: ...


def build_notifiers(config: AlertConfig) -> list[Notifier]:
    """Instantiate the channels named in ALERT_CHANNELS that are actually configured."""
    from .email_smtp import SMTPNotifier
    from .twilio_sms import TwilioNotifier

    notifiers: list[Notifier] = []
    for name in config.channels:
        if name == "email":
            if config.smtp.configured:
                notifiers.append(SMTPNotifier(config.smtp))
            else:
                log.warning("alert channel 'email' enabled but SMTP is not configured; skipping")
        elif name == "sms":
            if config.twilio.configured:
                notifiers.append(TwilioNotifier(config.twilio))
            else:
                log.warning("alert channel 'sms' enabled but Twilio is not configured; skipping")
        else:
            log.warning("unknown alert channel %r; skipping", name)
    return notifiers
