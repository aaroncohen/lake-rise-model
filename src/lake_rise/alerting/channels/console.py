"""Dry-run channel: prints the rendered alert instead of sending it.

Used by ``lake-rise alert --dry-run`` and in tests so the whole pipeline can be
exercised without SMTP/Twilio credentials or network access.
"""

from __future__ import annotations

from ..config import Recipients
from ..render import RenderedAlert


class ConsoleNotifier:
    name = "console"

    def __init__(self, writer=print):
        self._write = writer

    def send(self, alert: RenderedAlert, recipients: Recipients) -> None:
        bar = "=" * 72
        self._write(bar)
        self._write(f"SUBJECT: {alert.subject}")
        self._write(f"EMAIL TO: {', '.join(recipients.emails) or '(none)'}")
        self._write(f"SMS TO:   {', '.join(recipients.sms) or '(none)'}")
        self._write("-" * 72)
        self._write(alert.text_body)
        self._write("-- SMS --")
        self._write(alert.sms_body)
        self._write(bar)
