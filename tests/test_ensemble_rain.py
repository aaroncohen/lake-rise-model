"""The calibrated ensemble as the live forecast's rain: member calibration, mapping a run's
days onto the forecast hours, and the predictor's low/median/high and crossing chances."""

from datetime import datetime, timedelta, timezone

import pytest

from lake_rise import forecast_log
from lake_rise.artifact import EnsembleDayCalibration, EnsembleRain
from lake_rise.bundle import InputBundle, ScenarioRain
from lake_rise.predict import predict
from lake_rise.scenarios import calibrate_members, ensemble_members
from lake_rise.sources.snapshot import bundle_from_snapshot

CYCLE = datetime(2026, 11, 14, 0, tzinfo=timezone.utc)
IDENTITY = EnsembleDayCalibration(mean_scale=1.0, spread_scale=1.0, added_spread=0.0)


def _run(day_totals: list[list[float]]) -> dict:
    """An archived run whose member m has day_totals[d][m] inches on day d+1."""
    return {"source": "ecmwf_ens", "cycle": CYCLE.isoformat(),
            "periods": [{"lead_h": 24 * d, "hours": 24, "members_in": totals}
                        for d, totals in enumerate(day_totals)]}


@pytest.fixture
def identity(art):
    art.ensemble_rain = EnsembleRain(source="ecmwf_ens", max_run_age_h=30,
                                     calibration_by_day={"1": IDENTITY})
    return art


def test_identity_calibration_leaves_members_alone():
    assert calibrate_members(IDENTITY, [0.0, 0.5, 2.0]) == pytest.approx([0.0, 0.5, 2.0])


def test_fitted_calibration_widens_a_too_confident_ensemble(art):
    raw = [1.0 + 0.002 * m for m in range(50)]           # members that barely disagree
    for cal in art.ensemble_rain.calibration_by_day.values():
        out = calibrate_members(cal, raw)
        assert max(out) - min(out) > 5 * (max(raw) - min(raw))
        assert out.index(max(out)) == raw.index(max(raw))   # the wettest member stays wettest


def test_run_days_map_onto_forecast_hours(identity):
    """12 h after the cycle, half of day 1 is left. The point forecast's shower stays where it
    is and the member's extra rain is spread over the day; a day the point forecast has dry
    is spread evenly."""
    as_of = CYCLE + timedelta(hours=12)
    shape = [0.0] * 72
    shape[2] = 0.1
    (m,) = ensemble_members(identity, _run([[2.0], [2.4], [0.0], [4.8]]), as_of, 72, shape)
    r = m.hourly_in
    assert len(r) == 72
    assert sum(r[:12]) == pytest.approx(1.0)
    assert r[2] == pytest.approx(0.1 + 0.9 / 12) and r[0] == pytest.approx(0.9 / 12)
    assert r[12:36] == pytest.approx([0.1] * 24)
    assert sum(r[36:60]) == 0.0
    assert r[60:72] == pytest.approx([0.2] * 12)        # day 4, cut off by the horizon


def test_a_drier_member_scales_the_point_forecast_down(identity):
    as_of = CYCLE
    shape = [0.0] * 24
    shape[5], shape[6] = 0.3, 0.1
    (m,) = ensemble_members(identity, _run([[0.2]]), as_of, 24, shape)
    assert m.hourly_in[5] == pytest.approx(0.15) and m.hourly_in[6] == pytest.approx(0.05)
    assert sum(m.hourly_in) == pytest.approx(0.2)


def test_hours_past_the_runs_last_day_take_the_point_forecast(identity):
    as_of = CYCLE + timedelta(hours=12)
    (m,) = ensemble_members(identity, _run([[0.0], [0.0]]), as_of, 72, [0.05] * 72)
    assert m.hourly_in[36:] == pytest.approx([0.05] * 36)


def test_a_stale_run_is_not_used(identity):
    assert ensemble_members(identity, _run([[1.0]]), CYCLE + timedelta(hours=31), 72) == []
    assert ensemble_members(identity, _run([[1.0]]), CYCLE - timedelta(hours=1), 72) == []


def _bundle(members: list[list[float]]) -> InputBundle:
    return InputBundle(
        as_of=CYCLE + timedelta(hours=12), current_elevation_abs_ft=339.0, stop_log_count=3,
        ensemble_members=[ScenarioRain(name=f"member {i}", hourly_in=r)
                          for i, r in enumerate(members)],
        ensemble_cycle=CYCLE)


def _storm(total_in: float) -> list[float]:
    return [0.0] * 3 + [total_in / 12] * 12 + [0.0] * 57


def test_scenarios_are_the_members_at_the_10th_50th_90th_percentile(art):
    result = predict(_bundle([_storm(0.2 * j) for j in range(50)]), art)
    by = {s.name: s for s in result.scenarios}
    assert [by[n].ensemble_member for n in ("low", "median", "high")] == [5, 24, 44]
    assert by["low"].peak_elevation < by["median"].peak_elevation < by["high"].peak_elevation
    assert result.input_summary["rain_source"] == "ensemble"
    assert result.input_summary["ensemble_members"] == 50


def test_elevation_bands_nest_around_the_middle_hour_by_hour(art):
    result = predict(_bundle([_storm(0.2 * j) for j in range(50)]), art)
    b = result.elevation_bands_ft
    assert set(b) == {"5", "10", "25", "50", "75", "90", "95"}
    assert all(len(v) == len(result.scenarios[0].trajectory) for v in b.values())
    for hour in range(len(b["50"])):
        col = [b[k][hour] for k in ("5", "10", "25", "50", "75", "90", "95")]
        assert col == sorted(col)
    assert b["95"][-1] > b["5"][-1]                     # the storms leave the lake spread out


def test_more_wet_members_mean_a_higher_chance_of_crossing(art):
    def p(n_wet):
        return predict(_bundle([_storm(8.0)] * n_wet + [_storm(0.0)] * (50 - n_wet)), art
                       ).p_cross_341
    assert p(0) < p(10) < p(30) < p(50)


def test_chance_is_the_share_of_member_peaks_that_reach_a_threshold(art):
    result = predict(_bundle([_storm(8.0)] * 11 + [_storm(0.0)] * 39), art)
    crest = art.thresholds_abs_ft.dam_crest
    assert sum(p >= crest for p in result.ensemble_peaks_ft) == 11
    assert result.p_cross_crest == 0.22
    by = {tp.label: tp.p_cross_within_horizon for tp in result.threshold_probabilities}
    assert by["dam_crest"] == 0.22


def test_already_over_a_threshold_reads_1(art):
    bundle = _bundle([_storm(0.0)] * 50).model_copy(update={"current_elevation_abs_ft": 341.5})
    result = predict(bundle, art)
    assert result.p_cross_341 == 1.0
    assert result.p_cross_crest == 0.0


def test_ensemble_peaks_ft_is_sorted_with_one_entry_per_member(art, make_bundle):
    totals = [0.2 * ((j * 17) % 50) for j in range(50)]      # every size once, shuffled
    peaks = predict(_bundle([_storm(t) for t in totals]), art).ensemble_peaks_ft
    assert len(peaks) == 50 and peaks == sorted(peaks) and peaks[0] < peaks[-1]
    assert predict(make_bundle(total_in=1.0), art).ensemble_peaks_ft is None


def test_snapshot_without_a_recent_run_uses_the_point_forecast_band(art):
    snap = {"as_of": (CYCLE + timedelta(hours=40)).isoformat(), "lake_depth_reading_ft": 3.0,
            "forecast_point_in": [0.1] * 72, "ensemble_run": _run([[1.0] * 50] * 4),
            "horizon_hours": 72}
    assert bundle_from_snapshot(art, snap).ensemble_members == []
    snap["as_of"] = (CYCLE + timedelta(hours=12)).isoformat()
    bundle = bundle_from_snapshot(art, snap)
    assert len(bundle.ensemble_members) == 50 and bundle.ensemble_cycle == CYCLE


def test_forecast_log_records_the_members_rain(art):
    bundle = _bundle([_storm(0.2 * j) for j in range(50)])
    result = predict(bundle, art)
    rec = forecast_log.forecast_record(bundle, result, artifact_version=art.version)
    assert rec["rain_source"] == "ensemble" and rec["ensemble_cycle"] == CYCLE.isoformat()
    high = rec["scenarios"]["high"]
    assert high["ensemble_member"] == 44
    assert sum(high["rain_hourly_in"]) == pytest.approx(0.2 * 44, abs=1e-3)


def test_agreement_is_the_share_of_peaks_near_the_middle_case(art, make_bundle):
    assert predict(_bundle([_storm(1.0)] * 50), art).ensemble_agreement_pct == 100
    spread = predict(_bundle([_storm(0.2 * j) for j in range(50)]), art)
    assert 0 < spread.ensemble_agreement_pct < 100
    assert predict(make_bundle(total_in=1.0), art).ensemble_agreement_pct is None


def test_alerts_report_the_ensembles_agreement(art, make_alert_config):
    from lake_rise.alerting.render import render
    from lake_rise.alerting.rules import evaluate

    bundle = _bundle([_storm(0.2 * j) for j in range(50)])
    result = predict(bundle, art)
    d = evaluate(result, bundle, art, make_alert_config())
    assert d.confidence_pct == result.ensemble_agreement_pct
    assert "of the 50 forecasts put the lake's peak within 0.1 ft" in d.confidence_text
    msg = render(d, make_alert_config(), "LEVEL", "Advisory")
    assert d.confidence_text in str(msg)


def _replay(art, peaks):
    from lake_rise.alerting.config import _DEFAULT_LEVELS, _parse_levels
    from lake_rise.cli import replay_levels

    th = art.thresholds_abs_ft
    thresholds = {"early_warning": th.early_warning, "dam_crest": th.dam_crest,
                  "bridge_deck": th.bridge_deck}
    return replay_levels(sorted(peaks), 338.8, thresholds, _parse_levels(_DEFAULT_LEVELS))


def test_old_and_new_ladders_agree_when_every_member_is_far_below_the_thresholds(art):
    assert _replay(art, [339.0 + 0.02 * m for m in range(50)]) == (None, None)


def test_they_can_differ_when_one_member_is_far_above_the_rest(art):
    old, new = _replay(art, [339.0] * 9 + [345.0])
    assert old is None
    assert new.name == "WATCH"                  # 1 of 10 reaches the crest: 10 %


def test_replay_assumes_the_lake_at_the_spill_crest_when_the_hour_has_no_reading(art):
    from lake_rise.cli import replay_lake
    from lake_rise.geometry import control_elev_for_stop_logs, default_stop_log_count

    as_of = datetime(2026, 4, 1, tzinfo=timezone.utc)
    crest = control_elev_for_stop_logs(art.stop_logs, default_stop_log_count(art.stop_logs, 4, 1))
    assert replay_lake(art, None, as_of) == (crest, True)
    assert replay_lake(art, 339.1, as_of) == (339.1, False)
