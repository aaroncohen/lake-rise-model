"""Template rendering: Pacific-time conversion (PST vs PDT), deep-link, override dir."""

from datetime import datetime, timezone

from lake_rise.alerting.render import render, to_pacific
from lake_rise.alerting.rules import AlertDecision, TriggeredThreshold


def _decision(start, *, rank=4, name="DANGER", p_crest=0.35):
    cross = start.replace(hour=start.hour) if False else None  # placeholder
    th = (
        TriggeredThreshold("early_warning", 341.0, 0.8, start, start),
        TriggeredThreshold("dam_crest", 342.2, p_crest,
                           median_cross_at=start, earliest_cross_at=start),
    )
    return AlertDecision(
        generated_at=start, horizon_hours=72, current_elevation=341.5, freeboard_ft=0.7,
        datum_offset_ft=338.375, data_fresh=True, active_rank=rank, active_level_name=name,
        probabilities={"early_warning": 0.8, "dam_crest": p_crest}, thresholds=th,
        peak_elevation=342.6, peak_at=start, peak_elevation_high=343.1,
        forecast_total_in=2.4, peak_rain_hour=6, confidence_pct=70,
        confidence_label="Medium", test_active=False,
    )


def test_pacific_conversion_pst_and_pdt():
    # January 15 16:00 UTC -> 08:00 PST (UTC-8).
    winter = to_pacific(datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc), "America/Los_Angeles")
    assert "PST" in winter and "8:00 AM" in winter
    # July 15 16:00 UTC -> 09:00 PDT (UTC-7).
    summer = to_pacific(datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc), "America/Los_Angeles")
    assert "PDT" in summer and "9:00 AM" in summer
    assert to_pacific(None, "America/Los_Angeles") is None


def test_email_and_sms_contain_key_facts(make_alert_config):
    cfg = make_alert_config()
    start = datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc)  # PDT
    out = render(_decision(start), cfg, kind="LEVEL", level_name="DANGER")

    assert "DANGER" in out.subject and "35%" in out.subject
    for body in (out.text_body, out.html_body):
        assert "4.23 ft" in body              # peak level (gauge reading)
        assert "PDT" in body                 # Pacific tz on times
        assert "35%" in body                 # crest probability
        assert "http://nas.local:8077/?mode=live" in body  # deep-link
    # SMS is compact but still carries the headline + link.
    assert "Crystal Lake DANGER" in out.sms_body
    assert "http://nas.local:8077/?mode=live" in out.sms_body


def test_test_and_all_clear_banners(make_alert_config):
    cfg = make_alert_config()
    start = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc)
    test = render(_decision(start), cfg, kind="TEST")
    assert "[TEST]" in test.subject and "TEST notification" in test.text_body

    clear = render(_decision(start), cfg, kind="ALL_CLEAR", level_name="DANGER")
    assert "All clear" in clear.subject
    assert "returned to normal" in clear.text_body


def test_all_clear_includes_peak_current_and_24h_context(make_alert_config):
    from dataclasses import replace

    cfg = make_alert_config()
    start = datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc)
    # Post-storm: receded to 340.3 (below early-warning), peaked at 342.4 (above dam crest),
    # forecast holding ~340.0 over the next 24 h.
    d = replace(_decision(start), current_elevation=340.3, episode_peak_elevation=342.4,
                forecast_elev_24h=340.0, forecast_elev_24h_high=340.6)
    out = render(d, cfg, kind="ALL_CLEAR", level_name="DANGER")
    for body in (out.text_body, out.html_body):
        assert "Peak this event" in body
        assert "Dam Overtop" in body          # the highest threshold the peak exceeded
        assert "Next 24" in body
        assert "1.93" in body                  # current gauge: 340.3 - 338.375
        assert "SAFETY INSPECTION" in body     # peak (342.4) overtopped the dam crest
    # SMS is compact but still carries peak + 24h + the road-reopening note.
    assert "Peak 4.0" in out.sms_body and "24h" in out.sms_body
    assert "safety inspection" in out.sms_body.lower()


def test_downstream_evac_level_is_reframed_for_recipients(art, make_alert_config):
    """The bridge-deck level must not read as 'you, evacuate' — it's a notice to dam
    contacts to alert/evacuate the downstream zone."""
    from lake_rise.alerting.preview import synthetic_decision

    cfg = make_alert_config()
    d = synthetic_decision(art, cfg)   # forecast peak overtops the bridge deck
    out = render(d, cfg, kind="LEVEL", level_name="EVACUATE")
    assert "Downstream Evac Notice" in out.subject and "EVACUATE" not in out.subject
    for body in (out.text_body, out.html_body):
        assert "Downstream Evac Notice" in body
        assert "instruction for you" in body.lower()      # "...not an instruction for you..."
        assert "operators are notifying downstream" in body.lower()
    assert out.sms_body.startswith("Crystal Lake Downstream Evac Notice")
    assert "instruction for you" in out.sms_body.lower()


def test_all_clear_road_note_only_after_dam_crest(make_alert_config):
    from dataclasses import replace

    cfg = make_alert_config()
    start = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc)
    # Peaked at early-warning only (341.3 ft, below the dam crest) -> road was never closed.
    d = replace(_decision(start), current_elevation=339.5, episode_peak_elevation=341.3)
    out = render(d, cfg, kind="ALL_CLEAR", level_name="WARNING")
    assert "SAFETY INSPECTION" not in out.text_body
    assert "safety inspection" not in out.sms_body.lower()


def test_template_dir_override_is_honored(make_alert_config, tmp_path):
    # Provide just the subject template in an override dir; the rest fall back to built-ins.
    (tmp_path / "email_subject.txt").write_text("CUSTOM {{ banner }} {{ p_crest_pct }}")
    cfg = make_alert_config()
    object.__setattr__(cfg, "template_dir", tmp_path)  # frozen dataclass; set for the test

    start = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc)
    out = render(_decision(start), cfg, kind="LEVEL", level_name="DANGER")
    assert out.subject == "CUSTOM DANGER 35"
    # Body still rendered from the packaged default.
    assert "Crystal Lake Dam".upper() in out.text_body.upper()
