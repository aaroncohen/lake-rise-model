"""The lake filter's tuning study (docs/plans/lake-filter.md, T3): the real-storm finder, the P12
bar, and --write's refusal when nothing passes. Synthetic data; stub estimators stand in for the
filter so the bar itself is what's tested."""

import shutil
from datetime import datetime, timedelta, timezone

import numpy as np
from typer.testing import CliRunner

from lake_rise import lake_filter_study as LFS
from lake_rise.artifact import DEFAULT_ARTIFACT, load_artifact
from lake_rise.calibration.archive import HourSample
from lake_rise.cli import app
from lake_rise.lake_filter import LakeEstimate

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


def _flat_data(days=1.5):
    """A flat 1.0 ft lake with noise, dry, the raw and smoothed sensors equal."""
    n = int(days * 24 * 60)
    readings = 1.0 + np.random.default_rng(0).normal(0.0, 0.05, n)
    return LFS.StudyData(start=T0, raw=readings, smoothed=readings.copy(), truth=np.full(n, 1.0),
                         rain_states=[(T0, 0.0)], hourly_rain=[0.0] * (n // 60 + 2),
                         inflow_cfs=np.zeros(n), control_elev_abs=np.full(n, ART.stop_logs.control_elev(2)))


def _late_by(lag_ft):
    """A stub estimator: the median of the last 30 readings (today's anchor here), minus lag_ft."""
    def estimate(art, readings, rain, **_):
        return LakeEstimate(float(np.median([r for r in readings[-30:] if r is not None])) - lag_ft,
                            0.0, None)
    return estimate


def test_the_bar_rejects_a_combination_that_lags_a_blind_ramp_more_than_p12_allows():
    storms = LFS.storm_table(ART, _flat_data(), _late_by(0.05))
    assert not LFS.meets_p12(storms)


def test_the_bar_passes_an_estimator_no_further_behind_than_todays_anchor():
    storms = LFS.storm_table(ART, _flat_data(), _late_by(0.0))
    assert LFS.meets_p12(storms)


def test_write_leaves_the_artifact_untouched_when_nothing_passes(tmp_path, monkeypatch):
    art_path = tmp_path / "artifact.json"
    shutil.copy(DEFAULT_ARTIFACT, art_path)
    before = art_path.read_bytes()
    monkeypatch.setenv("LAKE_RISE_ARCHIVE_DIR", str(tmp_path / "archive"))   # no real storms
    monkeypatch.setattr(LFS, "load", lambda art, cfg, minute_dir: _flat_data())
    monkeypatch.setattr(LFS, "estimate_lake", _late_by(0.05))
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
    assert len([a for a, b in zip(before, art_path.read_text().splitlines()) if a != b]) == 3
