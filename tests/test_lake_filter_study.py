"""The lake filter's tuning study (docs/plans/lake-filter.md, T3): the real-storm finder, the P12
bar, and --write's refusal when nothing passes. Synthetic data; stub estimators stand in for the
filter so the bar itself is what's tested."""

import shutil
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from typer.testing import CliRunner

from lake_rise import gauge_minute, lake_filter_study as LFS
from lake_rise.artifact import DEFAULT_ARTIFACT, load_artifact
from lake_rise.calibration.archive import HourSample
from lake_rise.cli import app
from lake_rise.lake_filter import LakeEstimate
from lake_rise.sources.live_ha import HAConfig

ART = load_artifact()
T0 = datetime(2026, 11, 6, tzinfo=timezone.utc)


def _hours(levels, rain):
    return [HourSample(hour=(T0 + timedelta(hours=i)).isoformat(), elev_ft=e, rain_in=r)
            for i, (e, r) in enumerate(zip(levels, rain))]


def _relock(rain_at_step):
    """72 h at 339.0 ft, then the gauge reads 0.17 ft higher for 72 h."""
    levels = [339.0] * 72 + [339.17] * 72
    rain = [0.0] * 144
    rain[72] = rain_at_step
    return _hours(levels, rain)


def test_the_storm_finder_ignores_a_rise_without_rain():
    assert LFS.find_storms(_relock(0.0)) == []


def test_the_storm_finder_lists_the_same_rise_with_rain_once():
    storms = LFS.find_storms(_relock(0.6))
    assert [round(s["rise_ft"], 2) for s in storms] == [0.17]


def _flat_data(days=1.5, noise_sd=0.05, rain_hour=None):
    """A flat 1.0 ft lake with noise, the raw and smoothed sensors equal; dry but for 0.1 in in
    `rain_hour`."""
    n = int(days * 24 * 60)
    readings = 1.0 + np.random.default_rng(0).normal(0.0, noise_sd, n)
    hourly = [0.1 if h == rain_hour else 0.0 for h in range(n // 60 + 2)]
    return LFS.StudyData(start=T0, raw=readings, smoothed=readings.copy(), truth=np.full(n, 1.0),
                         rain_states=[(T0, 0.0)], hourly_rain=hourly,
                         inflow_hourly_cfs=[0.0] * (n // 60),
                         inflow_as_of=T0 + timedelta(minutes=n), control_elev_abs=np.full(n, ART.stop_logs.control_elev(2)))


def _anchor_minus(bias_ft):
    """A stub estimator: the median of the last 30 readings (today's anchor here), minus bias_ft."""
    def estimate(art, readings, rain, **_):
        return LakeEstimate(float(np.median([r for r in readings[-30:] if r is not None])) - bias_ft,
                            0.0, None)
    return estimate


def _reading_minutes_ago(minutes, bias_ft=0.0):
    """A stub estimator: the reading `minutes` ago (0.05 ft late on a 0.1 ft/h ramp at 30), minus bias_ft."""
    def estimate(art, readings, rain, **_):
        return LakeEstimate(readings[-1 - minutes] - bias_ft, 0.0, None)
    return estimate


def test_the_bar_rejects_a_combination_that_lags_a_blind_ramp_more_than_p12_allows():
    storms = LFS.storm_table(ART, _flat_data(), _reading_minutes_ago(30))
    assert not LFS.meets_p12(storms)


def test_the_bar_passes_an_estimator_no_further_behind_than_todays_anchor():
    storms = LFS.storm_table(ART, _flat_data(), _anchor_minus(0.0))
    assert LFS.meets_p12(storms)


def test_the_bar_passes_a_constant_bias_with_no_storm_lag():
    # 0.05 ft low at every call, but it follows the ramp at once: P12 gates the storm's lag only
    storms = LFS.storm_table(ART, _flat_data(noise_sd=0.0), _reading_minutes_ago(0, bias_ft=0.05))
    assert LFS.meets_p12(storms)
    absolute = storms[(0.1, "abrupt", "blind")][30][2]
    assert absolute == pytest.approx(0.05)


def test_the_lag_compares_the_ramped_and_calm_runs_at_the_same_minutes():
    # the gauge has a daily cycle the truth lacks; an estimator that follows the gauge at once is
    # behind by the cycle, ramp or no ramp, so its storm-induced lag is 0 only at matching minutes
    d = _flat_data(noise_sd=0.0)
    d.raw = d.raw + 0.05 * np.sin(2 * np.pi * np.arange(len(d.raw)) / 1440)
    d.smoothed = d.raw.copy()
    rows = [row for by_m in LFS.storm_table(ART, d, _reading_minutes_ago(0)).values() for row in by_m.values()]
    assert all(row[0] == pytest.approx(0.0, abs=1e-9) for row in rows)
    assert max(abs(row[2]) for row in rows) > 0.01


def test_the_calm_run_keeps_the_storms_rain():
    def low_in_rain(art, readings, rain, **_):          # reads 0.05 ft low whenever rain is in view
        return LakeEstimate(readings[-1] - (0.05 if any(rain) else 0.0), 0.0, None)
    rows = [row for by_m in LFS.storm_table(ART, _flat_data(noise_sd=0.0), low_in_rain).values()
            for row in by_m.values()]
    assert all(row[0] == pytest.approx(0.0, abs=1e-9) for row in rows)


def test_the_bar_rejects_an_estimator_that_stiffens_once_the_rain_stops():
    def stiffens(art, readings, rain, **_):     # follows the gauge in rain, a 3 h median after it
        if any(rain[-60:]):
            return LakeEstimate(readings[-1], 0.0, None)
        return LakeEstimate(float(np.median(readings[-180:])), 0.0, None)
    storms = LFS.storm_table(ART, _flat_data(noise_sd=0.0), stiffens)
    in_rain = {key: by_m for key, by_m in storms.items() if key[1] != "outlasts"}
    outlasts = {key: by_m for key, by_m in storms.items() if key[1] == "outlasts"}
    assert LFS.p12_margin(in_rain) >= 0 > LFS.p12_margin(outlasts)
    assert not LFS.meets_p12(storms)


def test_ramps_start_every_15_hours():
    k = LFS.onsets(ART, _flat_data(days=4))
    assert len(k) > 2 and set(np.diff(k)) == {15 * 60}


def test_a_ramp_without_todays_anchor_is_left_out_and_counted():
    d = _flat_data()
    ramps = LFS.onsets(ART, d)
    d.smoothed[ramps[0]:ramps[0] + 200] = np.nan
    row = LFS.storm_table(ART, d, _anchor_minus(0.0))[(0.1, "abrupt", "blind")][30]
    assert row[4:] == (len(ramps) - 1, 0, 1)


def test_the_bar_fails_a_filter_without_estimates_once_the_rain_has_left_its_window():
    # 3 h window: the outlasts check 360 min after the onset (4 h after the rain) is dry
    art = LFS.with_values(ART, {"wet_window_hours": 3})
    assert not LFS.meets_p12(LFS.storm_table(art, _flat_data(), lambda *a, **k: None))


def test_load_rejects_a_minute_archive_without_the_smoothed_sensor(tmp_path):
    cfg = HAConfig(base_url="", token="")
    times = [T0 + timedelta(minutes=10 * i) for i in range(8 * 24 * 6)]
    gauge_minute.append({cfg.lake_raw_sensor: [(t, "1.0") for t in times],
                         cfg.lake_sensor: [(t, "unknown") for t in times]}, tmp_path)
    with pytest.raises(ValueError, match="invalid study input"):
        LFS.load(ART, cfg, str(tmp_path))


def test_write_leaves_the_artifact_untouched_when_nothing_passes(tmp_path, monkeypatch):
    art_path = tmp_path / "artifact.json"
    shutil.copy(DEFAULT_ARTIFACT, art_path)
    before = art_path.read_bytes()
    monkeypatch.setenv("LAKE_RISE_ARCHIVE_DIR", str(tmp_path / "archive"))   # no real storms
    monkeypatch.setattr(LFS, "load", lambda art, cfg, minute_dir: _flat_data(rain_hour=20))
    monkeypatch.setattr(LFS, "estimate_lake", _reading_minutes_ago(30))
    result = CliRunner().invoke(app, ["lake-filter-study", "--write", "--artifact", str(art_path)])
    assert result.exit_code == 1, result.output
    assert "No combination meets P12" in result.output
    assert art_path.read_bytes() == before


def test_write_values_changes_only_those_lake_filter_values(tmp_path):
    art_path = tmp_path / "artifact.json"
    shutil.copy(DEFAULT_ARTIFACT, art_path)
    before = art_path.read_text().splitlines()
    values = {"rate_sd_wet_ft_per_min": 5e-5, "wet_window_hours": 3, "offset_minutes": 15}
    LFS.write_values(art_path, values)
    f = load_artifact(art_path).lake_filter
    assert (f.rate_sd_wet_ft_per_min, f.wet_window_hours, f.offset_minutes) == (5e-5, 3, 15)
    changed = [b for a, b in zip(before, art_path.read_text().splitlines()) if a != b]
    assert changed and all(any(f'"{key}"' in line for key in values) for line in changed)


def test_a_call_sees_the_previous_hours_inflow_over_the_current_hour_as_live():
    # live, the replay runs to the top of the hour and its last hour is held over the current one
    d = _flat_data()
    d.inflow_hourly_cfs = [float(h) for h in range(len(d.inflow_hourly_cfs))]   # clock hour h: h cfs
    seen = []

    def spy(art, readings, rain, *, inflow_cfs_by_minute, **_):
        seen.append(inflow_cfs_by_minute[-1])
        return None
    LFS.noise_table(ART, d, spy)
    calls = range(ART.lake_filter.history_hours * 60 - 1, len(d.raw), 60)
    assert seen == [k // 60 - 1 for k in calls]


def test_the_noise_table_reports_no_dry_pairs_on_a_window_without_a_dry_hour():
    d = _flat_data()
    d.hourly_rain = [0.1] * len(d.hourly_rain)
    noise = LFS.noise_table(ART, d, _anchor_minus(0.0))
    assert noise["filter"]["dry"] is None and noise["filter"]["pairs"][0] == 0
    assert noise["filter"]["wet"] is not None


def test_rain_starts_onsets_are_every_3_h_after_a_dry_spell():
    d = _flat_data(days=4, rain_hour=5)          # rain in hour 5; the grid starts at hour 12
    k = LFS.onsets(ART, d, "rain_starts")
    assert k[0] == 18 * 60 and set(np.diff(k)) == {3 * 60}     # 12 h after hour 5 ends
    storms = LFS.storm_table(ART, d, _anchor_minus(0.0))
    assert storms[(0.1, "rain_starts", "blind")][30][4] == len(k)


def test_the_bar_fails_when_no_onset_has_the_dry_spell_a_rain_starts_ramp_needs():
    d = _flat_data()
    d.hourly_rain = [0.1] * len(d.hourly_rain)
    storms = LFS.storm_table(ART, d, _anchor_minus(0.0))
    assert not any(shape == "rain_starts" for _, shape, _ in storms)
    assert LFS.p12_margin(storms) >= 0 and not LFS.meets_p12(storms)


def test_a_rain_starts_storm_rains_from_the_ramps_first_rise():
    gaps = []

    def spy(art, readings, rain, **_):
        rise = next((i for i in range(1, len(readings)) if readings[i] > readings[i - 1]), None)
        wet = next((i for i, r in enumerate(rain) if r), None)
        if rise is not None and wet is not None:
            gaps.append(rise - wet)
        return LakeEstimate(readings[-1], 0.0, None)
    LFS.storm_table(ART, _flat_data(noise_sd=0.0), spy)
    assert min(gaps) == 0                     # rain_starts; the others rain 2 h before


def test_in_rain_the_live_anchor_is_todays_median():
    # a filter 30 min late, which yields to the median in rain (P16): no storm lag while it rains
    def yields(art, readings, rain, **_):
        return None if LFS._in_rain(art, rain) else LakeEstimate(readings[-31], 0.0, None)
    storms = LFS.storm_table(ART, _flat_data(noise_sd=0.0), yields)
    row = storms[(0.1, "abrupt", "blind")][30]
    assert row[0] == pytest.approx(row[1]) and row[5] == 0


def _row(p99, on_filter, dry=0.01, passes=True):
    return {"passes": passes, "values": {"p99": p99, "on": on_filter, "dry": dry},
            "noise": {"filter": {"dry": dry, "wet": None, "p99": p99},
                      "today": {"dry": 0.025, "wet": None, "p99": 0.040},
                      "live": {"filter": on_filter, "median_in_rain": 0, "median_no_estimate": 0}}}


def test_pick_takes_the_most_coverage_among_rows_with_todays_tail():
    rows = [_row(0.039, 90), _row(0.038, 110, dry=0.012), _row(0.038, 110), _row(0.030, 120, passes=False),
            _row(0.045, 130)]
    assert LFS.pick(rows)["values"] == {"p99": 0.038, "on": 110, "dry": 0.01}


def test_pick_falls_back_to_the_lowest_p99_without_todays_tail():
    best = LFS.pick([_row(0.048, 120), _row(0.044, 90)])
    assert best["values"]["p99"] == 0.044 and not LFS.tail_ok(best)
