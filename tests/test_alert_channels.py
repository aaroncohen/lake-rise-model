"""Channel factory + each channel dispatching to the passed-in recipients (no network)."""

import smtplib

from lake_rise.alerting.channels import ConsoleNotifier, build_notifiers
from lake_rise.alerting.channels.email_smtp import SMTPNotifier
from lake_rise.alerting.channels.twilio_sms import TwilioNotifier
from lake_rise.alerting.config import Recipients, SMTPConfig, TwilioConfig
from lake_rise.alerting.render import RenderedAlert

ALERT = RenderedAlert(subject="S", text_body="T", html_body="<p>H</p>", sms_body="SMS")


def test_factory_skips_unconfigured_channels(make_alert_config):
    # Neither SMTP nor Twilio configured -> no notifiers built.
    assert build_notifiers(make_alert_config()) == []

    smtp = SMTPConfig("mail.x.org", 587, "u", "p", "from@x.org", True)
    twilio = TwilioConfig("AC", "tok", "+1000")
    cfg = make_alert_config(smtp=smtp, twilio=twilio)
    names = {n.name for n in build_notifiers(cfg)}
    assert names == {"email", "sms"}


def test_console_writes_recipients_and_bodies():
    lines = []
    ConsoleNotifier(writer=lines.append).send(
        ALERT, Recipients(emails=("a@x.org",), sms=("+1",)))
    blob = "\n".join(lines)
    assert "a@x.org" in blob and "+1" in blob and "SMS" in blob


def test_smtp_sends_multipart_to_recipients(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            sent["host"] = host
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, msg): sent["msg"] = msg

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    cfg = SMTPConfig("mail.x.org", 587, "u", "pw", "from@x.org", True)
    SMTPNotifier(cfg).send(ALERT, Recipients(emails=("a@x.org", "b@x.org")))

    msg = sent["msg"]
    assert sent["tls"] and sent["login"] == ("u", "pw")
    assert msg["To"] == "a@x.org, b@x.org" and msg["Subject"] == "S"
    assert msg.is_multipart()  # text + html alternative


def test_smtp_no_email_recipients_is_noop(monkeypatch):
    def boom(*a, **k):  # must not be called
        raise AssertionError("SMTP should not connect with no recipients")
    monkeypatch.setattr(smtplib, "SMTP", boom)
    SMTPNotifier(SMTPConfig("h", 587, None, None, "f@x.org", True)).send(
        ALERT, Recipients(emails=(), sms=("+1",)))


def test_twilio_sends_one_message_per_number(monkeypatch):
    created = []

    class FakeMessages:
        def create(self, body, from_, to): created.append((to, from_, body))

    class FakeClient:
        def __init__(self, sid, tok): self.messages = FakeMessages()

    import twilio.rest
    monkeypatch.setattr(twilio.rest, "Client", FakeClient)

    TwilioNotifier(TwilioConfig("AC", "tok", "+1000")).send(
        ALERT, Recipients(sms=("+1111", "+2222")))
    assert [c[0] for c in created] == ["+1111", "+2222"]
    assert all(c[1] == "+1000" and c[2] == "SMS" for c in created)
