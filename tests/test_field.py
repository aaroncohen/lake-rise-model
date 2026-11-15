"""The field view's summary: levels and their chances, peaks, status, mode, timeline and rain,
all in stick height."""

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import pytest

from lake_rise.alerting.rules import evaluate
from lake_rise.bundle import ScenarioRain
from lake_rise.field import summarize
from lake_rise.observed import GaugeObservation
from lake_rise.predict import predict, scenario_rain

from test_ensemble_rain import _bundle, _storm

NOW = datetime(2026, 11, 14, 13, tzinfo=timezone.utc)
OFF = 338.375


def _summary(art, bundle, config, *, result=None, observation=None, trend=None):
    result = result or predict(bundle, art)
    decision = evaluate(result, bundle, art, config)
    return summarize(result, bundle, decision, art, observation=observation,
                     trend_ft_per_h=trend, gauge_problem=None, computed_at=NOW)


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


def test_a_member_peaking_exactly_at_the_crest_counts_as_overtop(art, cfg, spread):
    crest = art.thresholds_abs_ft.dam_crest
    summary = _summary(art, spread, cfg, result=_with_peaks(art, spread, [339.0] * 49 + [crest]))
    assert summary.peak_levels[-1] == "overtop"
    assert summary.peaks_ft[-1] == 3.83


def test_plastic_percentage_uses_3_30_ft_on_the_stick(art, cfg, spread):
    peaks = [3.30 + OFF] * 10 + [3.29 + OFF] * 40
    levels = _levels(_summary(art, spread, cfg, result=_with_peaks(art, spread, peaks)))
    assert levels["eap_plastic"].stick_ft == 3.30
    assert levels["eap_plastic"].pct == 20


def test_peak_levels_names_the_highest_level_each_peak_reaches(art, cfg, spread):
    peaks = [340.0, 341.0, 341.7, 342.3, 342.8]
    summary = _summary(art, spread, cfg, result=_with_peaks(art, spread, peaks))
    assert summary.peak_levels == [None, "early_warning", "eap_plastic", "overtop", "bridge"]


def test_fallback_gives_percentages_and_no_peaks(art, cfg, make_bundle):
    summary = _summary(art, make_bundle(total_in=2.0), cfg)
    levels = _levels(summary)
    assert summary.mode == "fallback"
    assert summary.peaks_ft == [] and summary.peak_levels == []
    assert levels["eap_plastic"].pct is None
    assert all(isinstance(levels[k].pct, int) for k in ("early_warning", "overtop", "bridge"))
    assert set(summary.timeline.bands) == {"low", "high"}


def test_no_forecast_gives_no_timeline_and_no_percentages(art, cfg, make_bundle):
    bundle = make_bundle()
    bundle.forecast_scenarios = [ScenarioRain(name=n, hourly_in=[]) for n in ("low", "median", "high")]
    summary = _summary(art, bundle, cfg)
    assert summary.mode == "no_forecast"
    assert summary.timeline is None and summary.cases is None and summary.rain_6h is None
    assert summary.peaks_ft == [] and summary.ensemble_cycle is None
    assert [lv.pct for lv in summary.levels] == [None] * 4


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
