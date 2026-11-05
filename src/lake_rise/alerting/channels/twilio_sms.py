"""Twilio SMS channel."""

from __future__ import annotations

import logging

from ..config import Recipients, TwilioConfig
from ..render import RenderedAlert

log = logging.getLogger("lake_rise.alerting")


class TwilioNotifier:
    name = "sms"

    def __init__(self, config: TwilioConfig):
        self.config = config
        self._client = None  # lazily created so importing this module needs no creds

    def _get_client(self):
        if self._client is None:
            from twilio.rest import Client
            self._client = Client(self.config.account_sid, self.config.auth_token)
        return self._client

    def send(self, alert: RenderedAlert, recipients: Recipients) -> None:
        if not recipients.sms:
            return
        client = self._get_client()
        failed: list[str] = []
        for number in recipients.sms:          # one bad number must not stop the rest
            try:
                client.messages.create(
                    body=alert.sms_body,
                    from_=self.config.from_number,
                    to=number,
                )
            except Exception as exc:  # noqa: BLE001
                log.error("alert SMS to %s failed: %s", number, exc)
                failed.append(number)
        log.info("alert SMS sent to %d of %d recipient(s)", len(recipients.sms) - len(failed),
                 len(recipients.sms))
        if failed and len(failed) == len(recipients.sms):
            raise RuntimeError(f"alert SMS failed for every recipient ({len(failed)})")
