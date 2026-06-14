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
        for number in recipients.sms:
            client.messages.create(
                body=alert.sms_body,
                from_=self.config.from_number,
                to=number,
            )
        log.info("alert SMS sent to %d recipient(s)", len(recipients.sms))
