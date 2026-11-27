"""The field view's summary: levels and their chances, peaks, status, mode, timeline and rain,
all in stick height."""

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import pytest

from lake_rise import forecast_log
from lake_rise.alerting.rules import evaluate
from lake_rise.bundle import ScenarioRain
from lake_rise.field import summarize
from lake_rise.observed import GaugeObservation
from lake_rise.predict import predict, scenario_rain

from test_ensemble_rain import _bundle, _storm

NOW = datetime(2026, 11, 14, 13, tzinfo=timezone.utc)
OFF = 338.375


def _summary(art, bundle, config, *, result=None, observation=None, trend=None, **s2):
    result = result or predict(bundle, art)
    decision = evaluate(result, bundle, art, config)
    return summarize(result, bundle, decision, art, observation=observation,
                     trend_ft_per_h=trend, gauge_problem=None, computed_at=NOW, **s2)


def _with_peaks(art, bundle, peaks_abs):
    """A real ensemble prediction whose member peaks are replaced by ``peaks_abs``."""
    return predict(bundle, art).model_copy(update={"ensemble_peaks_ft": sorted(peaks_abs)})


def _with_crest_chance(result, p):
    tps = [tp.model_copy(update={"p_cross_within_horizon": p}) if tp.label == "dam_crest" else tp
           for tp in result.threshold_probabilities]
    return result.model_copy(update={"threshold_probabilities": tps, "p_cross_crest": p})


@pytest.fixture
def cfg(make_alert_config):
    return make_alert_config()


@pytest.fixture
def spread():
    return _bundle([_storm(0.2 * j) for j in range(50)])


def _levels(summary):
    return {lv.key: lv for lv in summary.levels}


def test_percentages_are_cumulative_and_match_the_alert_probabilities(art, cfg):
    bundle = _bundle([_storm(8.0)] * 11 + [_storm(4.0)] * 10 + [_storm(0.0)] * 29)
    result = predict(bundle, art)
    summary = _summary(art, bundle, cfg, result=result)
    levels = _levels(summary)
    by = {tp.label: tp.p_cross_within_horizon for tp in result.threshold_probabilities}
    assert levels["early_warning"].pct == round(100 * by["early_warning"])
    assert levels["overtop"].pct == round(100 * by["dam_crest"]) == 22
    assert levels["bridge"].pct == round(100 * by["bridge_deck"])
    pcts = [lv.pct for lv in summary.levels]
    assert pcts == sorted(pcts, reverse=True)
    # The EAP's Emergency Levels 1 and 2 (2.30 and 3.30 ft) read "EAP 1" and "EAP 2" (2026-11 EAP).
    assert [lv.label for lv in summary.levels] == ["EAP 1", "Warning", "EAP 2", "Overtop", "Bridge"]



def test_an_artifact_without_a_bridge_deck_has_no_bridge_level(art, cfg, spread):
    art = art.model_copy(update={"thresholds_abs_ft": art.thresholds_abs_ft.model_copy(
        update={"bridge_deck": None})})
    high = art.thresholds_abs_ft.dam_crest + 1.0
    result = _with_peaks(art, spread, [339.0] * 49 + [high])
    summary = _summary(art, spread, cfg, result=result,
                       options=[(k, predict(spread.model_copy(update={"stop_log_count": k}), art))
                                for k in (2, 1, 0)])
    assert [lv.key for lv in summary.levels] == ["eap1", "early_warning", "eap2", "overtop"]
    assert summary.peak_levels[-1] == "overtop"

def test_a_member_peaking_exactly_at_the_crest_counts_as_overtop(art, cfg, spread):
    crest = art.thresholds_abs_ft.dam_crest
    summary = _summary(art, spread, cfg, result=_with_peaks(art, spread, [339.0] * 49 + [crest]))
    assert summary.peak_levels[-1] == "overtop"
    assert summary.peaks_ft[-1] == 3.83


def test_eap_percentages_use_2_30_and_3_30_ft_on_the_stick(art, cfg, spread):
    peaks = [3.30 + OFF] * 10 + [3.29 + OFF] * 10 + [2.30 + OFF] * 5 + [2.29 + OFF] * 25
    levels = _levels(_summary(art, spread, cfg, result=_with_peaks(art, spread, peaks)))
    assert levels["eap1"].stick_ft == 2.30 and levels["eap2"].stick_ft == 3.30
    assert levels["eap1"].pct == 50
    assert levels["eap2"].pct == 20


def test_peak_levels_names_the_highest_level_each_peak_reaches(art, cfg, spread):
    peaks = [340.0, 340.8, 341.0, 341.7, 342.3, 342.8]
    summary = _summary(art, spread, cfg, result=_with_peaks(art, spread, peaks))
    assert summary.peak_levels == [None, "eap1", "early_warning", "eap2", "overtop", "bridge"]


def test_fallback_gives_percentages_and_no_peaks(art, cfg, make_bundle):
    summary = _summary(art, make_bundle(total_in=2.0), cfg)
    levels = _levels(summary)
    assert summary.mode == "fallback"
    assert summary.peaks_ft == [] and summary.peak_levels == []
    assert levels["eap1"].pct is None and levels["eap2"].pct is None
    assert all(isinstance(levels[k].pct, int) for k in ("early_warning", "overtop", "bridge"))
    assert set(summary.timeline.bands) == {"low", "high"}


def test_no_forecast_gives_no_timeline_and_no_percentages(art, cfg, make_bundle):
    bundle = make_bundle()
    bundle.forecast_scenarios = [ScenarioRain(name=n, hourly_in=[]) for n in ("low", "median", "high")]
    summary = _summary(art, bundle, cfg)
    assert summary.mode == "no_forecast"
    assert summary.timeline is None and summary.cases is None and summary.rain_6h is None
    assert summary.peaks_ft == [] and summary.ensemble_cycle is None
    assert [lv.pct for lv in summary.levels] == [None] * 5


def test_ensemble_without_an_apple_forecast_is_ensemble_mode(art, cfg, spread):
    assert spread.forecast_scenarios == []
    summary = _summary(art, spread, cfg)
    assert summary.mode == "ensemble"
    assert len(summary.peaks_ft) == 50
    assert set(summary.timeline.bands) == {"10", "25", "75", "90"}
    assert summary.ensemble_cycle == spread.ensemble_cycle


def test_observed_eap_level_leads_at_3_95_ft(art, cfg, spread):
    seen = GaugeObservation(detected_at=NOW, gauge_ft=3.95, confirmed=True, sample_count=5)
    status = _summary(art, spread, cfg, observation=seen).status
    assert status.observed_eap == {"title": "Bridge Closure", "gauge_ft": 3.95, "color": "#9c1f24"}


def test_normal_gives_level_none_and_rank_0(art, cfg):
    status = _summary(art, _bundle([_storm(0.0)] * 50), cfg).status
    assert (status.level, status.rank, status.headline, status.observed_eap) == (None, 0, None, None)


def test_headline_follows_the_active_levels_threshold(art, cfg, make_bundle):
    ens = _bundle([_storm(8.0)] * 11 + [_storm(0.0)] * 39)
    ens_result = predict(ens, art)
    assert ens_result.p_cross_crest == 0.22
    fallback = make_bundle(total_in=1.0)
    fb_result = _with_crest_chance(predict(fallback, art), 0.22)
    for bundle, result in ((ens, ens_result), (fallback, fb_result)):
        status = _summary(art, bundle, cfg, result=result).status
        assert status.level == "WATCH"
        assert status.headline == "Overtop 22%"


def test_every_ft_value_is_absolute_minus_the_offset(art, cfg, spread):
    summary = _summary(art, spread, cfg)
    expected = float(Decimal(str(spread.current_elevation_abs_ft - OFF))
                     .quantize(Decimal("0.01"), ROUND_HALF_UP))
    assert summary.lake.now_ft == expected == 0.63

    def stick_values(node, under_ft=False):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from stick_values(v, under_ft or k.endswith("_ft") or k in ("cases", "bands"))
        elif isinstance(node, list):
            for v in node:
                yield from stick_values(v, under_ft)
        elif under_ft and isinstance(node, float):
            yield node

    values = list(stick_values(summary.model_dump()))
    assert len(values) > 100
    assert all(v <= 10 for v in values)


def test_early_warning_shows_2_63(art, cfg, spread):
    assert _levels(_summary(art, spread, cfg))["early_warning"].stick_ft == 2.63


def test_rain_blocks_sum_to_the_scenario_total(art, cfg, spread):
    result = predict(spread, art)
    summary = _summary(art, spread, cfg, result=result)
    rain = {s.name: s.hourly_in for s in scenario_rain(spread, result)}
    assert len(summary.rain_6h["middle"]) == 12
    assert sum(summary.rain_6h["middle"]) == pytest.approx(sum(rain["median"]), abs=0.01)
    assert sum(summary.rain_6h["wet"]) == pytest.approx(sum(rain["high"]), abs=0.01)


def test_rain_total_is_the_horizon_sum_of_each_case(art, cfg, spread):
    result = predict(spread, art)
    summary = _summary(art, spread, cfg, result=result)
    rain = {s.name: s.hourly_in for s in scenario_rain(spread, result)}
    assert summary.rain_total_in == {k: round(sum(rain[n]), 2)
                                     for k, n in (("dry", "low"), ("middle", "median"), ("wet", "high"))}
    assert summary.rain_total_in["dry"] <= summary.rain_total_in["middle"] <= summary.rain_total_in["wet"]


def test_no_forecast_has_no_rain_total(art, cfg, make_bundle):
    bundle = make_bundle()
    bundle.forecast_scenarios = [ScenarioRain(name=n, hourly_in=[]) for n in ("low", "median", "high")]
    assert _summary(art, bundle, cfg).rain_total_in is None


def test_the_rain_rate_passes_through_to_the_lake(art, cfg, spread):
    assert _summary(art, spread, cfg, rain_rate_in_per_h=0.12).lake.rain_rate_in_per_h == 0.12
    assert _summary(art, spread, cfg).lake.rain_rate_in_per_h is None


@pytest.mark.parametrize("trend, label", [
    (0.0, "steady"), (0.029, "steady"), (-0.029, "steady"),
    (0.03, "rising"), (-0.05, "falling"), (None, None),
])
def test_trend_reads_steady_under_0_03_ft_per_hour(art, cfg, spread, trend, label):
    assert _summary(art, spread, cfg, trend=trend).lake.trend == label


@pytest.mark.parametrize("gauge_ft, confirmed", [(3.95, False), (2.29, True)])
def test_no_observed_eap_level_when_unconfirmed_or_below_2_30(art, cfg, spread, gauge_ft, confirmed):
    seen = GaugeObservation(detected_at=NOW, gauge_ft=gauge_ft, confirmed=confirmed, sample_count=5)
    assert _summary(art, spread, cfg, observation=seen).status.observed_eap is None


# What changed, actions and stop logs. ``spread`` is a WATCH state: middle 3.04,
# overtop 34%, with 2 stop logs in (summer normal); ``quiet`` is a dry forecast with every chance 0.

@pytest.fixture
def quiet():
    return _bundle([_storm(0.0)] * 50)


def _record(art, bundle, **changes):
    """The forecast-log record of ``bundle``'s prediction, made an hour before it (the usual
    previous record), with ``changes`` applied."""
    record = forecast_log.forecast_record(bundle, predict(bundle, art), artifact_version="t",
                                          art=art)
    return {**record, "as_of": (bundle.as_of - timedelta(hours=1)).isoformat(), **changes}


def _options(art, bundle):
    return [(k, predict(bundle.model_copy(update={"stop_log_count": k}), art))
            for k in range(bundle.stop_log_count - 1, -1, -1)]


def test_change_shows_when_the_middle_moved_0_05_ft(art, cfg, spread):
    previous = _record(art, spread, middle_peak_ft=2.99 + OFF)
    summary = _summary(art, spread, cfg, previous=previous)
    assert summary.cases["middle"] == 3.04
    assert summary.change.middle_was_ft == 2.99
    assert summary.change.pct_was == {}
    assert summary.change.worse is True
    assert summary.change.since == datetime.fromisoformat(previous["generated_at"])


@pytest.mark.parametrize("was_stick", [None, 3.02])
def test_no_change_when_nothing_moved(art, cfg, spread, was_stick):
    changes = {} if was_stick is None else {"middle_peak_ft": was_stick + OFF}
    assert _summary(art, spread, cfg, previous=_record(art, spread, **changes)).change is None


def test_change_ignored_when_the_stop_log_count_differs(art, cfg, spread):
    previous = _record(art, spread, middle_peak_ft=2.5 + OFF, stop_log_count=1)
    assert _summary(art, spread, cfg, previous=previous).change is None


def test_change_ignored_for_a_record_without_levels_pct(art, cfg, spread):
    previous = _record(art, spread, middle_peak_ft=2.5 + OFF)
    del previous["levels_pct"]
    assert _summary(art, spread, cfg, previous=previous).change is None


def test_change_ignored_when_the_rain_source_differs(art, cfg, spread):
    previous = _record(art, spread, middle_peak_ft=2.5 + OFF, rain_source="point_forecast")
    assert _summary(art, spread, cfg, previous=previous).change is None


def test_no_change_in_the_normal_state(art, cfg, quiet):
    previous = _record(art, quiet, middle_peak_ft=0.5 + OFF)
    assert _summary(art, quiet, cfg, previous=previous).change is None


@pytest.mark.parametrize("overtop_was, worse", [(30, True), (40, False)])
def test_worse_when_a_percentage_rose(art, cfg, spread, overtop_was, worse):
    previous = _record(art, spread)
    previous["levels_pct"] = {**previous["levels_pct"], "overtop": overtop_was}
    change = _summary(art, spread, cfg, previous=previous).change
    assert change.pct_was == {"overtop": overtop_was}
    assert change.worse is worse


def test_a_record_from_before_the_2026_11_eap_compares_its_3_30_level(art, cfg, spread):
    """Older records have no "eap1" and call the 3.30 ft level "eap_plastic"."""
    previous = _record(art, spread)
    pcts = dict(previous["levels_pct"])
    del pcts["eap1"]
    pcts["eap_plastic"] = pcts.pop("eap2") - 10
    previous["levels_pct"] = pcts
    change = _summary(art, spread, cfg, previous=previous).change
    assert change.pct_was == {"eap2": pcts["eap_plastic"]} and change.worse is True


def test_change_ignores_a_record_of_the_same_forecast_hour(art, cfg, spread):
    same = _record(art, spread, middle_peak_ft=2.5 + OFF, as_of=spread.as_of.isoformat())
    assert _summary(art, spread, cfg, previous=same).change is None
    earlier = _record(art, spread, middle_peak_ft=2.5 + OFF)    # an hour before
    assert _summary(art, spread, cfg, previous=earlier).change is not None


@pytest.mark.parametrize("now, was, label", [
    (0.07, 0.05, "faster"), (0.05, 0.07, "slower"), (-0.07, -0.05, "faster"),
    (-0.12, -0.10, "faster"), (0.06, 0.05, None),
    (0.06, 0.03, "faster"), (0.028, 0.005, None), (0.03, 0.05, "slower"),
])
def test_trend_faster_and_slower_by_0_02_ft_per_hour(art, cfg, spread, now, was, label):
    assert _summary(art, spread, cfg, trend=now, trend_was_ft_per_h=was).trend_change == label


@pytest.mark.parametrize("now, was", [(None, 0.05), (0.05, None), (0.05, -0.07), (0.0, 0.05)])
def test_no_trend_change_when_either_trend_is_missing_or_the_directions_differ(
        art, cfg, spread, now, was):
    assert _summary(art, spread, cfg, trend=now, trend_was_ft_per_h=was).trend_change is None


@pytest.mark.parametrize("p_crest, ids", [
    (0.10, ["pull_logs", "inspect", "monitor"]), (0.09, []),
])
def test_actions_follow_the_email_rule_at_10_percent_overtop(art, cfg, quiet, p_crest, ids):
    result = _with_crest_chance(predict(quiet, art), p_crest)
    actions = _summary(art, quiet, cfg, result=result).actions
    assert [a.id for a in actions] == ids
    assert [a.short for a in actions][:1] == (["Pull 2 stop logs"] if ids else [])


def test_no_actions_when_normal(art, cfg, quiet):
    summary = _summary(art, quiet, cfg)
    assert summary.actions == [] and summary.next_level is None


def test_next_level_is_the_lowest_eap_level_the_wet_case_reaches(art, cfg, spread):
    above_plastic = spread.model_copy(update={"current_elevation_abs_ft": 3.5 + OFF})
    summary = _summary(art, above_plastic, cfg)
    assert summary.lake.now_ft == 3.5 and summary.cases["wet"] >= 3.90
    assert (summary.next_level.title, summary.next_level.gauge_ft) == ("Bridge Closure", 3.90)
    assert [a.id for a in summary.next_level.actions] == ["eap4-0", "eap4-1", "eap4-2", "eap4-3"]
    assert [a.short for a in summary.next_level.actions] == [
        "Close the bridge", "Overtopping east of bridge",
        "Tarps · plastic · sandbags where it overtops", "Pull remaining stop logs"]


def test_next_level_skips_an_observed_eap_level(art, cfg, spread):
    below_plastic = spread.model_copy(update={"current_elevation_abs_ft": 3.28 + OFF})
    seen = GaugeObservation(detected_at=NOW, gauge_ft=3.35, confirmed=True, sample_count=5)
    summary = _summary(art, below_plastic, cfg, observation=seen)
    assert summary.lake.now_ft == 3.28 and summary.cases["wet"] >= 3.90
    assert summary.next_level.title == "Bridge Closure"


def test_an_observed_eap_level_turns_on_the_actions(art, cfg, quiet):
    seen = GaugeObservation(detected_at=NOW, gauge_ft=3.35, confirmed=True, sample_count=5)
    actions = _summary(art, quiet, cfg, observation=seen).actions
    assert [a.id for a in actions] == ["pull_logs", "inspect", "monitor"]


def test_no_forecast_gives_actions_from_an_active_eap_level_and_no_next_level(
        art, cfg, make_bundle):
    bundle = make_bundle()
    bundle.forecast_scenarios = [ScenarioRain(name=n, hourly_in=[]) for n in ("low", "median", "high")]
    quiet = _summary(art, bundle, cfg)
    above = bundle.model_copy(update={"current_elevation_abs_ft": 3.5 + OFF})
    active = _summary(art, above, cfg)
    assert quiet.mode == active.mode == "no_forecast"
    assert quiet.actions == [] and quiet.next_level is None
    assert [a.id for a in active.actions] == ["pull_logs", "inspect", "monitor"]
    assert active.next_level is None


def test_stop_log_options_hidden_when_pulling_changes_nothing(art, cfg, spread):
    result = predict(spread, art)
    options = [(k, result) for k in (2, 1, 0)]
    assert _summary(art, spread, cfg, result=result, options=options).stop_logs is None


def test_stop_log_options_hidden_in_the_normal_state(art, cfg, quiet):
    # Pulling all 3 logs lowers the quiet day's middle by 0.13 ft, yet a quiet day shows none.
    assert _summary(art, quiet, cfg, options=_options(art, quiet)).stop_logs is None


def test_stop_log_options_list_every_count_down_to_zero(art, cfg, spread):
    summary = _summary(art, spread, cfg, options=_options(art, spread))
    stop_logs = summary.stop_logs
    assert stop_logs.logs_in == 2 and stop_logs.is_default is False
    assert [o.logs_in for o in stop_logs.options] == [2, 1, 0]
    leave = stop_logs.options[0]
    assert (leave.dry_ft, leave.middle_ft, leave.wet_ft) == (
        summary.cases["dry"], summary.cases["middle"], summary.cases["wet"])
    assert leave.pct == {lv.key: lv.pct for lv in summary.levels}
    assert [o.pct["overtop"] for o in stop_logs.options] == [34, 26, 22]


def test_default_count_is_marked(art, cfg, spread):
    seasonal = spread.model_copy(update={"stop_log_count_is_default": True})
    assert _summary(art, seasonal, cfg, options=_options(art, seasonal)).stop_logs.is_default


# Details (F9). In ``spread`` the middle rises from 0.63 to a 3.04 peak and only the wet case
# overtops and covers the bridge; ``high`` starts at 3.90, already over the crest, and falls.

@pytest.fixture
def high(spread):
    return spread.model_copy(update={"current_elevation_abs_ft": 3.90 + OFF})


def _no_forecast(make_bundle, **changes):
    bundle = make_bundle().model_copy(update=changes)
    bundle.forecast_scenarios = [ScenarioRain(name=n, hourly_in=[]) for n in ("low", "median", "high")]
    return bundle


def test_details_factors_map_to_the_five_labels(art, cfg, spread):
    factors = _summary(art, spread, cfg).details.factors
    assert [(f.key, f.label) for f in factors] == [
        ("watershed_runoff", "Surface runoff"), ("direct_rain", "Rain on the lake"),
        ("baseflow", "Groundwater seepage"), ("evaporation", "Evaporation"),
        ("spillway", "Out the spillways")]


def test_factors_to_the_peak_sum_to_the_peak_rise(art, cfg, spread):
    summary = _summary(art, spread, cfg)
    details = summary.details
    assert details.factors_window == "to peak"
    assert sum(f.ft for f in details.factors) == pytest.approx(details.net_ft, abs=0.03)
    assert details.net_ft == pytest.approx(
        round(summary.cases["middle"] - summary.lake.now_ft, 2), abs=0.01)


def test_a_first_hour_peak_gives_factors_to_the_peak(art, cfg, high):
    burst = high.model_copy(update={"ensemble_members": [
        m.model_copy(update={"hourly_in": [3.0 * (0.5 + 0.02 * j)] + [0.0] * 71})
        for j, m in enumerate(high.ensemble_members)]})
    result = predict(burst, art)
    median = next(s for s in result.scenarios if s.name == "median")
    elev = [p.elevation for p in median.trajectory]
    assert max(elev) == elev[0] > result.current_elevation
    summary = _summary(art, burst, cfg, result=result)
    details = summary.details
    assert details.factors_window == "to peak"
    assert sum(f.ft for f in details.factors) == pytest.approx(details.net_ft, abs=0.03)
    assert details.net_ft == pytest.approx(summary.cases["middle"] - summary.lake.now_ft, abs=0.01)


def test_factors_until_is_the_middle_peak_time(art, cfg, spread):
    result = predict(spread, art)
    median = next(s for s in result.scenarios if s.name == "median")
    elev = [p.elevation for p in median.trajectory]
    i_peak = elev.index(max(elev))
    details = _summary(art, spread, cfg, result=result).details
    assert details.factors_until == spread.as_of + timedelta(hours=i_peak + 1)


def test_a_first_hour_peak_has_factors_until_one_hour_on(art, cfg, high):
    burst = high.model_copy(update={"ensemble_members": [
        m.model_copy(update={"hourly_in": [3.0 * (0.5 + 0.02 * j)] + [0.0] * 71})
        for j, m in enumerate(high.ensemble_members)]})
    details = _summary(art, burst, cfg).details
    assert details.factors_until == burst.as_of + timedelta(hours=1)


def test_spillway_and_evaporation_are_negative(art, cfg, spread):
    ft = {f.key: f.ft for f in _summary(art, spread, cfg).details.factors}
    assert ft["spillway"] < 0 and ft["evaporation"] < 0


def test_factors_fall_back_to_3_day_totals_when_the_lake_doesnt_rise(art, cfg, high):
    result = predict(high, art)
    details = _summary(art, high, cfg, result=result).details
    assert details.factors_window == "3 days"
    assert details.factors_until is None
    totals = result.factors["totals_ft"]
    assert {f.key: f.ft for f in details.factors} == {
        k: pytest.approx(totals[k], abs=0.005) for k in totals if k != "net"}
    assert details.net_ft == pytest.approx(totals["net"], abs=0.005)
    assert details.net_ft < 0


def test_rain_days_sum_to_the_scenario_totals(art, cfg, spread):
    result = predict(spread, art)
    days = _summary(art, spread, cfg, result=result).details.rain_days
    rain = {s.name: s.hourly_in for s in scenario_rain(spread, result)}
    assert [d.start for d in days] == [spread.as_of + timedelta(hours=h) for h in (0, 24, 48)]
    for case, name in (("dry", "low"), ("middle", "median"), ("wet", "high")):
        assert sum(getattr(d, f"{case}_in") for d in days) == pytest.approx(
            sum(rain[name]), abs=0.01)


def test_cases_carry_overtop_and_bridge_times_when_reached(art, cfg, spread):
    result = predict(spread, art)
    cases = {c.name: c for c in _summary(art, spread, cfg, result=result).details.cases}
    wet = {s.name: s for s in result.scenarios}["high"]
    assert cases["wet"].overtop_at == spread.as_of + timedelta(hours=wet.hours_to_crest)
    assert cases["wet"].bridge_at == spread.as_of + timedelta(hours=wet.hours_to_bridge_deck)
    assert not cases["wet"].overtop_reached and not cases["wet"].bridge_reached
    assert (cases["middle"].overtop_at, cases["middle"].bridge_at) == (None, None)


def test_already_over_the_crest_is_reached_not_a_time(art, cfg, high):
    dry = _summary(art, high, cfg).details.cases[0]
    assert dry.name == "dry"
    assert dry.overtop_reached is True and dry.overtop_at is None


def test_no_forecast_keeps_the_data_rows(art, cfg, make_bundle):
    bundle = _no_forecast(make_bundle, trailing_rainfall_in=[0.5] * 6 + [0.1] * 24,
                          stop_log_count=2, stop_log_count_is_default=True)
    summary = _summary(art, bundle, cfg, gauge_age_min=7)
    details = summary.details
    assert summary.mode == "no_forecast"
    assert (details.factors, details.rain_days, details.cases) == ([], [], [])
    assert (details.net_ft, details.factors_window, details.rain_source) == (None, None, None)
    assert (details.agreement_pct, details.agreement_label, details.agreement_tol_ft) == (
        None, None, None)
    assert details.gauge_age_min == 7
    assert details.rain_gauge_24h_in == 2.4
    assert (details.stop_log_count, details.stop_log_default) == (2, True)
    assert 0 <= details.soil_pct <= 100


def test_forecast_rows_name_the_rain_source_and_agreement(art, cfg, spread, make_bundle):
    ens = _summary(art, spread, cfg).details
    assert (ens.rain_source, ens.agreement_label, ens.agreement_tol_ft) == (
        "ensemble", "Low", art.ensemble_rain.agreement_tolerance_ft)
    fallback = _summary(art, make_bundle(total_in=1.0), cfg).details
    assert (fallback.rain_source, fallback.agreement_pct, fallback.agreement_label) == (
        "point_forecast", None, None)


@pytest.mark.parametrize("fraction, pct", [(0.5, 50), (1.3, 100)])
def test_soil_percent_uses_lzsn_and_is_capped_at_100(art, cfg, spread, fraction, pct):
    result = predict(spread, art).model_copy(
        update={"state_sm_in": fraction * art.hspf.LZSN_in})
    assert _summary(art, spread, cfg, result=result).details.soil_pct == pct
