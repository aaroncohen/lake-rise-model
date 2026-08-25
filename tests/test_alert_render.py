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


def test_email_header_color_tracks_alert_severity(make_alert_config):
    """The email masthead should visually distinguish every step in the alert ladder.

    Lower tiers start green/olive, then progress through amber/orange to red for the
    highest tiers.  In particular, an advisory must no longer look like an evacuation.
    """
    cfg = make_alert_config()
    start = datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc)
    expected = {
        "ADVISORY": "#2f7a52",
        "WARNING": "#7a7126",
        "WATCH": "#996020",
        "DANGER": "#b34a1a",
        "CRITICAL": "#9c1f24",
        "EVACUATE": "#6d1220",
    }

    for level_name, color in expected.items():
        out = render(_decision(start), cfg, kind="LEVEL", level_name=level_name)
        # The accent bar and the alert name at the very top use the tier color.
        assert out.html_body.count(color) >= 2

    assert len(expected.values()) == len(set(expected.values()))


def test_peak_rain_uses_absolute_local_date_and_time(make_alert_config):
    """Rain timing should not make recipients add a relative hour to the issue time."""
    from dataclasses import replace

    cfg = make_alert_config()
    # 11:00 PM PDT, with the heaviest rain in forecast hour 3.  The absolute label
    # therefore needs to cross midnight and show the following calendar date.
    start = datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc)
    decision = replace(_decision(start), peak_rain_hour=3)
    out = render(decision, cfg, kind="LEVEL", level_name="DANGER")

    for body in (out.text_body, out.html_body):
        assert "heaviest around Thu Jul 16, 1:00 AM PDT" in body
        assert "heaviest around hour" not in body


def test_observed_eap_email_leads_with_actions_and_retains_forecast(make_alert_config):
    cfg = make_alert_config()
    detected = datetime(2026, 7, 16, 6, 5, tzinfo=timezone.utc)
    out = render(
        _decision(detected), cfg, kind="EAP_CROSSING", level_name="Evacuate Downstream",
        observed_rank=3, observed_gauge_ft=4.45, observed_detected_at=detected,
        observed_previous_rank=0,
    )

    assert "EAP THRESHOLD CROSSED" in out.subject
    assert "Evacuate Downstream" in out.subject and "4.45 ft" in out.subject
    for body in (out.text_body, out.html_body):
        assert body.index("REQUIRED EAP STEPS") < body.index("2. STATUS")
        assert "Mandatory Alert" in body and "Bridge Closure" in body
        assert "Evacuate Downstream" in body and "NORCOM" in body
        assert "no level active" not in body.lower()
        assert "Forecast peak" in body and "Threshold likelihoods" in body
        assert "Wed Jul 15, 11:05 PM PDT" in body
    assert "ACTION REQUIRED NOW" in out.sms_body
    assert "See email" in out.sms_body


def test_degraded_observed_email_is_qualified(make_alert_config):
    cfg = make_alert_config()
    detected = datetime(2026, 1, 15, 16, 5, tzinfo=timezone.utc)
    out = render(
        _decision(detected), cfg, kind="EAP_CROSSING", level_name="Mandatory Alert",
        observed_rank=1, observed_gauge_ft=3.35, observed_detected_at=detected,
        observed_degraded=True, observed_degraded_reason="history unavailable",
    )
    assert "THRESHOLD INDICATED" in out.subject
    for body in (out.text_body, out.html_body):
        assert "instantaneous gauge reading" in body
        assert "history unavailable" in body


def test_test_and_all_clear_banners(make_alert_config):
    cfg = make_alert_config()
    start = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc)
    test = render(_decision(start), cfg, kind="TEST")
    assert "[TEST]" in test.subject and "TEST notification" in test.text_body

    clear = render(_decision(start), cfg, kind="ALL_CLEAR", level_name="DANGER")
    assert "All clear" in clear.subject
    assert "clearing the prior DANGER notice" in clear.text_body


def test_test_clear_is_labeled_test_and_names_its_own_track(make_alert_config):
    """TEST_CLEAR closes out a TEST notice and must keep the [TEST] labeling all the way
    through -- otherwise the closing message reads as an unrelated, unlabeled ALL CLEAR
    with no obvious tie back to the earlier test notice. It also must not literally print
    the word "None" for the level it's clearing (it isn't tied to any ladder level)."""
    cfg = make_alert_config()
    start = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc)

    clear = render(_decision(start), cfg, kind="TEST_CLEAR")
    assert "[TEST]" in clear.subject and "All clear" in clear.subject
    assert "[TEST]" in clear.sms_body
    for body in (clear.text_body, clear.html_body):
        assert "None" not in body
        assert "clearing the prior Test Rain Advisory notice" in body
        assert "TEST notification" in body


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
        assert "Event peak" in body
        assert "Dam Overtop" in body          # the highest threshold the peak exceeded
        assert "Next 24" in body
        assert "1.93" in body                  # current gauge: 340.3 - 338.375
        assert "SAFETY INSPECTION" in body     # peak (342.4) overtopped the dam crest
    # SMS is compact but still carries peak + 24h + the road-reopening note.
    assert "peak 4.02ft" in out.sms_body and "24h" in out.sms_body
    assert "safety inspection" in out.sms_body.lower()


def test_downstream_evac_level_is_named_for_what_it_is(art, make_alert_config):
    """The bridge-deck level must not read as 'you, evacuate' — it tells the dam contacts
    to evacuate the DOWNSTREAM zone, and the EAP action spelling that out must come with
    it."""
    from lake_rise.alerting.preview import synthetic_decision

    cfg = make_alert_config()
    d = synthetic_decision(art, cfg)   # forecast peak overtops the bridge deck
    out = render(d, cfg, kind="LEVEL", level_name="EVACUATE")
    assert "Downstream Evacuation" in out.subject and "EVACUATE" not in out.subject
    for body in (out.text_body, out.html_body):
        assert "Downstream Evacuation" in body
        assert "Evacuate downstream." in body             # the EAP action itself
        assert "NORCOM" in body                           # ...and who to call
    assert out.sms_body.startswith("Crystal Lake Downstream Evacuation")


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


def test_notice_is_three_sections_and_carries_no_resident_content(make_alert_config):
    """Recipients are the dam management team and EAP contacts. Every notice is a summary
    with an action verdict, then status, then forecast — and nothing addressed to lake
    residents, who are not notified automatically."""
    cfg = make_alert_config()
    start = datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc)
    out = render(_decision(start), cfg, kind="LEVEL", level_name="DANGER")

    for body in (out.text_body.lower(), out.html_body.lower()):
        assert "1. summary" in body and "2. status" in body and "3. forecast" in body
        assert "action" in body                       # the verdict
        assert "for lake residents" not in body       # no resident-audience section
        assert "move your vehicles" not in body       # ...and no resident-directed advice
        assert "not an instruction for you" not in body
    assert "ACTION" in out.sms_body


def test_action_verdict_tracks_the_eap_levels_in_play(make_alert_config):
    """The verdict is the one line a reader must take away, so it has to follow the EAP
    levels the lake is at or forecast to reach — gauge 3.30 ft is the lowest (Mandatory
    Alert), i.e. 341.675 ft absolute."""
    from dataclasses import replace

    cfg = make_alert_config()
    start = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc)

    def verdict(current, peak, peak_high):
        d = replace(_decision(start), current_elevation=current, peak_elevation=peak,
                    peak_elevation_high=peak_high, freeboard_ft=342.2 - current)
        return render(d, cfg, kind="LEVEL", level_name="ADVISORY").subject

    # Not even the wettest scenario reaches an EAP level.
    assert "NO EAP ACTION FORECAST" in verdict(339.5, 340.5, 341.0)
    # Only the wettest scenario reaches Mandatory Alert.
    assert "MONITOR — NO EAP ACTION NOW" in verdict(341.0, 341.5, 341.8)
    # The median scenario reaches it.
    assert "ACTION LIKELY REQUIRED" in verdict(341.0, 341.8, 342.0)
    # The lake is already above it.
    assert "ACTION REQUIRED NOW" in verdict(341.8, 342.0, 342.3)


def test_high_end_only_eap_levels_are_a_heads_up_not_a_job_list(make_alert_config):
    """A level only the wettest scenario touches must not print its full EAP action list.
    Doing so puts 'begin sandbagging / notify all contacts' next to a single-digit overtop
    probability, which reads as a call to act and buries the levels that need staging."""
    from dataclasses import replace

    cfg = make_alert_config()
    start = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc)
    # Median peak 3.13 ft gauge stays under the 3.30 ft Mandatory Alert; only the wettest
    # scenario (3.43 ft) reaches it.
    d = replace(_decision(start), current_elevation=341.0, peak_elevation=341.5,
                peak_elevation_high=341.8, freeboard_ft=1.2)
    out = render(d, cfg, kind="LEVEL", level_name="WARNING")

    for body in (out.text_body, out.html_body):
        assert "Mandatory Alert" in body            # still surfaced...
        assert "HIGH-END SCENARIO ONLY" in body.upper()
        assert "sandbagging" not in body            # ...but without the action list
        assert "EAP LEVELS TO PREPARE FOR" not in body.upper()

    # Once the median scenario reaches it, the actions do appear.
    d2 = replace(d, peak_elevation=341.8, peak_elevation_high=342.0)
    prep = render(d2, cfg, kind="LEVEL", level_name="WARNING")
    for body in (prep.text_body, prep.html_body):
        assert "EAP LEVELS TO PREPARE FOR" in body.upper()
        assert "sandbagging" in body


def test_preventative_measures_scale_to_the_situation(make_alert_config):
    """A fixed list reads as boilerplate and gets skipped. Pulling boards is an
    over-reaction at a 0%-overtop advisory and impossible when they are already out, and
    the monitoring cadence has to tighten as the lake closes on the crest."""
    from dataclasses import replace

    cfg = make_alert_config()
    start = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc)

    def measures(d, level="ADVISORY"):
        return render(d, cfg, kind="LEVEL", level_name=level).text_body.split(
            "PREVENTATIVE MEASURES")[1]

    # Quiet advisory: nothing near an EAP level, negligible crest risk -> no board pull,
    # relaxed cadence.
    quiet = replace(_decision(start, p_crest=0.0), current_elevation=339.5,
                    peak_elevation=340.2, peak_elevation_high=340.6, freeboard_ft=2.7,
                    probabilities={"early_warning": 0.35, "dam_crest": 0.0},
                    stop_log_count=2)
    assert "stop log" not in measures(quiet)
    assert "every 4 hours" in measures(quiet)

    # Real rise coming, boards still in -> pull them, by count, and tighten the cadence.
    rising = replace(quiet, peak_elevation=341.8, peak_elevation_high=342.1,
                     probabilities={"early_warning": 0.9, "dam_crest": 0.35})
    assert "Pull the 2 stop logs" in measures(rising)
    assert "hourly" in measures(rising)

    # Same situation with the board already out -> say so rather than advise the impossible.
    bare = replace(rising, stop_log_count=0)
    assert "already out" in measures(bare) and "Pull the" not in measures(bare)

    # Over the crest -> continuous watch.
    over = replace(rising, current_elevation=342.4, freeboard_ft=-0.2)
    assert "Monitor continuously" in measures(over, level="CRITICAL")
