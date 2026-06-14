"""SMTP email channel (stdlib only)."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from ..config import Recipients, SMTPConfig
from ..render import RenderedAlert

log = logging.getLogger("lake_rise.alerting")


class SMTPNotifier:
    name = "email"

    def __init__(self, config: SMTPConfig):
        self.config = config

    def send(self, alert: RenderedAlert, recipients: Recipients) -> None:
        if not recipients.emails:
            return
        msg = EmailMessage()
        msg["Subject"] = alert.subject
        msg["From"] = self.config.sender
        msg["To"] = ", ".join(recipients.emails)
        msg.set_content(alert.text_body)
        msg.add_alternative(alert.html_body, subtype="html")

        with smtplib.SMTP(self.config.host, self.config.port, timeout=30) as smtp:
            if self.config.starttls:
                smtp.starttls()
            if self.config.user and self.config.password:
                smtp.login(self.config.user, self.config.password)
            smtp.send_message(msg)
        log.info("alert email sent to %d recipient(s): %s",
                 len(recipients.emails), alert.subject)
