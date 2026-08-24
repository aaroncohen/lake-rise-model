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

        smtp = smtplib.SMTP(self.config.host, self.config.port, timeout=30)
        try:
            if self.config.starttls:
                smtp.starttls()
            if self.config.user and self.config.password:
                smtp.login(self.config.user, self.config.password)
            smtp.send_message(msg)
        finally:
            # send_message() already blocked for the server's final "250 OK" -- the
            # message is delivered by this point, full stop. QUIT is best-effort
            # cleanup; some servers (Gmail included) occasionally drop the connection
            # before acking it. Letting that exception escape `send()` would make a
            # successful delivery look like a failure to the caller, which re-fires
            # the notice next tick (see service.hold_undelivered) and produces a
            # genuine duplicate email instead of a real retry.
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001 - cleanup only; delivery already happened above
                log.debug("SMTP quit() failed after a successful send (harmless)", exc_info=True)
        log.info("alert email sent to %d recipient(s): %s",
                 len(recipients.emails), alert.subject)
