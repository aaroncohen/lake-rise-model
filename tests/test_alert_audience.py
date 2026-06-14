"""Cumulative audience resolution and env parsing of audience groups."""

from lake_rise.alerting.config import Recipients, alert_config_from_env


def _audiences():
    return {
        "ops": Recipients(emails=("ops@x.org",), sms=("+1000",)),
        "emergency": Recipients(emails=("eoc@x.org",), sms=("+2000",)),
        "road": Recipients(emails=("roads@x.org",), sms=("+3000",)),
    }


def test_advisory_reaches_only_ops(make_alert_config):
    cfg = make_alert_config(audiences=_audiences())
    r = cfg.resolve_recipients(1)  # ADVISORY -> ops
    assert r.emails == ("ops@x.org",) and r.sms == ("+1000",)


def test_critical_is_cumulative_union(make_alert_config):
    cfg = make_alert_config(audiences=_audiences())
    r = cfg.resolve_recipients(5)  # CRITICAL: ops (1-3) + emergency (4) + road (5)
    assert set(r.emails) == {"ops@x.org", "eoc@x.org", "roads@x.org"}
    assert set(r.sms) == {"+1000", "+2000", "+3000"}


def test_union_dedupes_shared_contacts(make_alert_config):
    aud = _audiences()
    aud["emergency"] = Recipients(emails=("ops@x.org", "eoc@x.org"), sms=())  # shares ops@
    cfg = make_alert_config(audiences=aud)
    r = cfg.resolve_recipients(4)
    assert r.emails.count("ops@x.org") == 1  # de-duplicated across the union


def test_env_parses_levels_and_audience_groups(monkeypatch):
    monkeypatch.setenv("ALERT_LEVELS",
                       "ADVISORY:early_warning:0.30:ops,CRITICAL:dam_crest:0.60:road")
    monkeypatch.setenv("ALERT_AUDIENCE_OPS_EMAIL", "a@x.org, b@x.org")
    monkeypatch.setenv("ALERT_AUDIENCE_ROAD_SMS", "+19998887777")
    cfg = alert_config_from_env()

    assert [lv.name for lv in cfg.levels] == ["ADVISORY", "CRITICAL"]
    assert cfg.levels[1].rank == 2 and cfg.levels[1].audience == "road"
    assert cfg.audience_recipients("ops").emails == ("a@x.org", "b@x.org")
    # Cumulative at the top includes both groups.
    top = cfg.resolve_recipients(2)
    assert top.emails == ("a@x.org", "b@x.org") and top.sms == ("+19998887777",)
