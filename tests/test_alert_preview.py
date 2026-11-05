"""Local alert-validation helpers (alerting/preview.py): config summary, per-tier
rendering, storm-driven preview, file output, and the email-to-self guard."""

from pathlib import Path

import pytest

from lake_rise.alerting.config import _DEFAULT_LEVELS, AlertLevel, _parse_levels
from lake_rise.alerting.preview import (
    email_self,
    preview_storm,
    render_all_tiers,
    summarize_config,
    write_rendered,
)


def test_summarize_config_table_and_warnings(art, make_alert_config):
    cfg = make_alert_config()  # default ladder, empty audiences, SMTP/Twilio unconfigured
    s = summarize_config(art, cfg)
    assert [r.name for r in s.rows] == [lv.name for lv in cfg.levels]
    # threshold elevations resolve from the artifact
    ew = next(r for r in s.rows if r.threshold_label == "early_warning")
    assert ew.threshold_abs_ft == art.thresholds_abs_ft.early_warning
    # every level's audience is empty here -> a warning each, plus channel warnings
    assert any("has no recipients" in w for w in s.warnings)
    assert any("SMTP" in w for w in s.warnings)  # email channel enabled but unconfigured


def test_summarize_config_flags_unknown_threshold(art, make_alert_config):
    # The env parser now refuses such a level outright (below); a config built in code can still
    # carry one, and the preview must flag it.
    cfg = make_alert_config(levels=(AlertLevel(1, "BOGUS", "not_a_threshold", 0.5, "ops"),))
    s = summarize_config(art, cfg)
    assert s.rows[0].threshold_abs_ft is None
    assert any("not_a_threshold" in w and "never fire" in w for w in s.warnings)


def test_alert_levels_reject_a_threshold_with_no_forecast_probability():
    """A level on a label predict() never scores (e.g. dam_crest_low) would silently never fire;
    reject it when ALERT_LEVELS is parsed, not just in the preview."""
    for label in ("not_a_threshold", "dam_crest_low", "crest"):
        with pytest.raises(ValueError, match="never fire"):
            _parse_levels(f"X:{label}:0.5:ops")
    assert len(_parse_levels(_DEFAULT_LEVELS)) >= 3          # the default ladder is valid


def test_render_all_tiers_renders_every_level_plus_test_and_clear(art, make_alert_config):
    cfg = make_alert_config()
    tiers = render_all_tiers(art, cfg)
    labels = [t.label for t in tiers]
    assert labels == [lv.name for lv in cfg.levels] + ["TEST", "ALL_CLEAR"]
    for t in tiers:
        assert t.alert.subject and t.alert.text_body and t.alert.html_body and t.alert.sms_body
    # routing is cumulative: EVACUATE (top) resolves the union up its rank
    evac = next(t for t in tiers if t.label == "EVACUATE")
    assert evac.recipients == cfg.resolve_recipients(evac.rank)


def test_preview_storm_fires_and_is_side_effect_free(art, make_alert_config, tmp_path):
    state = tmp_path / "state.json"
    cfg = make_alert_config(state_path=state)
    pv = preview_storm(
        art, cfg, current_elevation_abs_ft=342.0, stop_log_count=3, month=1,
        preset="atmospheric_river")
    assert pv.decision.active_rank >= 1 and pv.fired is not None
    assert pv.fired.label == pv.decision.active_level_name
    assert pv.fired.recipients == cfg.resolve_recipients(pv.fired.rank)
    # a preview must never read or write the persisted alert state
    assert not state.exists()


def test_preview_storm_force_tier_overrides_firing(art, make_alert_config):
    cfg = make_alert_config()
    pv = preview_storm(
        art, cfg, current_elevation_abs_ft=339.7, stop_log_count=3, month=7,
        preset="light_rain", force_tier="EVACUATE")
    assert pv.fired is not None and pv.fired.label == "EVACUATE"


def test_write_rendered_emits_four_files(art, make_alert_config, tmp_path):
    cfg = make_alert_config()
    tier = render_all_tiers(art, cfg)[0]
    d = write_rendered(tmp_path, tier.label, tier.alert)
    assert {p.name for p in d.iterdir()} == {"subject.txt", "body.txt", "body.html", "sms.txt"}
    assert (d / "body.html").read_text() == tier.alert.html_body


def test_email_self_requires_smtp(art, make_alert_config):
    cfg = make_alert_config()  # SMTP unconfigured
    tier = render_all_tiers(art, cfg)[0]
    with pytest.raises(RuntimeError):
        email_self(cfg, tier.alert)
