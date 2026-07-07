"""Parameter sensitivity sweep."""

from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from lake_rise import model, storm_record as SR, sweep as SW
from lake_rise.artifact import load_artifact
from lake_rise.cli import app
from lake_rise.geometry import control_elev_for_stop_logs
from lake_rise.registry import load_registry

runner = CliRunner()


@pytest.fixture
def reg():
    return load_registry()


def _self_truth_record(art, label="s") -> SR.StormRecord:
    """A storm whose observed gauge is the model's own trajectory at the CURRENT parameters,
    so scoring at the current value gives ~0 error and any other value is worse."""
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    rs = datetime(2026, 3, 1, tzinfo=timezone.utc)
    t0 = rs + timedelta(hours=24)
    now = t0 + timedelta(hours=24)
    rain = [0.05] * 24 + [0.1] * 24
    state, _ = model.hindcast(art, rain[:24], h0=control, start=rs, control_elev=control)
    state.h = control
    _, recs = model.run(art, state, rain[24:], start=t0, control_elev=control)
    truth = {t0.isoformat(): round(control, 3)}
    truth.update({r.t.isoformat(): round(r.h, 3) for r in recs})
    return SR.StormRecord(label=label, captured_at=now.isoformat(), source="synthetic",
                          rain_start=rs.isoformat(), rain_hourly=rain, level_by_hour=truth,
                          t0=t0.isoformat(), now=now.isoformat(), control_elev=control)


def test_sweep_structure_and_marks_current_and_prior(art, reg):
    res = SW.sweep_parameter(art, reg, [], "hspf.PERC_coeff", steps=5)
    assert res["path"] == "hspf.PERC_coeff"
    assert res["range"] == [0.10, 0.50]
    assert any(r["is_current"] for r in res["rows"])       # current value evaluated + marked
    assert any(r["is_prior"] for r in res["rows"])         # research prior evaluated + marked
    vals = [r["value"] for r in res["rows"]]
    assert vals == sorted(vals) and all(0.10 <= v <= 0.50 for v in vals)
    assert res["couples_with"]                             # coupling surfaced


def test_sweep_current_value_minimizes_error_on_self_truth(art, reg):
    rec = _self_truth_record(art)
    res = SW.sweep_parameter(art, reg, [rec], "hspf.PERC_coeff", steps=5)
    best = min(res["rows"], key=lambda r: r["mean_rmse_ft"])
    assert best["is_current"]                              # the sweep recovers the truth's value
    assert best["mean_rmse_ft"] == pytest.approx(0.0, abs=1e-6)


def _unscored_record(art, label="u") -> SR.StormRecord:
    """A record that LOADS and anchors (an observation 2 h before T0 satisfies the anchor
    window) but has no observed level inside [T0, now], so it shares no predicted/actual hours
    and contributes no metrics -- the silently-empty case the n_scored count must expose."""
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    rs = datetime(2026, 3, 1, tzinfo=timezone.utc)
    t0 = rs + timedelta(hours=24)
    now = t0 + timedelta(hours=24)
    truth = {(t0 - timedelta(hours=2)).isoformat(): round(control, 3),      # anchors, pre-window
             (t0 + timedelta(days=400)).isoformat(): round(control, 3)}     # far outside window
    return SR.StormRecord(label=label, captured_at=now.isoformat(), source="synthetic",
                          rain_start=rs.isoformat(), rain_hourly=[0.05] * 48, level_by_hour=truth,
                          t0=t0.isoformat(), now=now.isoformat(), control_elev=control)


def test_sweep_reports_n_scored(art, reg):
    """n_scored counts storms that actually contributed metrics, distinct from n_records: a
    scored self-truth storm gives 1/1; an in-window-empty storm gives 0/1 with blank accuracy."""
    scored = SW.sweep_parameter(art, reg, [_self_truth_record(art)], "hspf.PERC_coeff", steps=3)
    assert (scored["n_records"], scored["n_scored"]) == (1, 1)

    empty = SW.sweep_parameter(art, reg, [_unscored_record(art)], "hspf.PERC_coeff", steps=3)
    assert (empty["n_records"], empty["n_scored"]) == (1, 0)
    assert all(r["mean_rmse_ft"] is None for r in empty["rows"])   # every accuracy column blank

    none_loaded = SW.sweep_parameter(art, reg, [], "hspf.PERC_coeff", steps=3)
    assert (none_loaded["n_records"], none_loaded["n_scored"]) == (0, 0)


def test_sweep_reports_anchor_pass_and_does_not_mutate_input(art, reg):
    before = art.hspf.PERC_coeff
    res = SW.sweep_parameter(art, reg, [], "hspf.PERC_coeff", steps=4)
    assert all("anchors_pass" in r for r in res["rows"])
    assert art.hspf.PERC_coeff == before                  # sweep scores deep copies only


def test_sweep_rejects_untunable_and_table_and_unknown(art, reg):
    with pytest.raises(ValueError):
        SW.sweep_parameter(art, reg, [], "spillway.weir_exponent", steps=3)   # not tunable
    with pytest.raises(ValueError):
        SW.sweep_parameter(art, reg, [], "seasonal_agw_default_in", steps=3)  # whole table
    with pytest.raises(ValueError):
        SW.sweep_parameter(art, reg, [], "hspf.not_a_param", steps=3)         # unregistered


def test_cli_sweep_runs_over_a_dataset(art, tmp_path):
    SR.save(_self_truth_record(art), tmp_path / "s.json")
    result = runner.invoke(app, ["sweep", "hspf.PERC_coeff", "--steps", "3", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "PASS" in result.stdout
    assert "current value" in result.stdout


def test_cli_sweep_warns_when_storms_load_but_none_score(art, tmp_path):
    """A sweep over storms that load but share no predicted/actual hours must not read as a real
    result: it reports 0/N scored and flags the blank accuracy columns."""
    SR.save(_unscored_record(art), tmp_path / "u.json")
    result = runner.invoke(app, ["sweep", "hspf.PERC_coeff", "--steps", "3", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "0/1 scored storm(s)" in result.stdout
    assert "NONE share predicted/actual hours" in result.stdout


def test_cli_sweep_rejects_untunable():
    result = runner.invoke(app, ["sweep", "spillway.weir_exponent"])
    assert result.exit_code == 1
