"""Storm-truth records + offline scoring. Pure: no network."""

from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from lake_rise import model, storm_record as SR
from lake_rise.artifact import load_artifact
from lake_rise.cli import app
from lake_rise.geometry import control_elev_for_stop_logs

runner = CliRunner()


def _self_truth_record(art, label="synthetic", perturb_truth=0.0) -> SR.StormRecord:
    """Build a StormRecord whose 'observed' gauge IS the model's own forward trajectory
    (optionally offset by ``perturb_truth`` ft), so scoring the same artifact gives ~0 error."""
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    rain_start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    t0 = rain_start + timedelta(hours=24)
    now = t0 + timedelta(hours=24)
    rain = [0.05] * 24 + [0.1] * 24
    state, _ = model.hindcast(art, rain[:24], h0=control, start=rain_start, control_elev=control)
    state.h = control
    _, recs = model.run(art, state, rain[24:], start=t0, control_elev=control)
    truth = {t0.isoformat(): round(control + perturb_truth, 3)}
    truth.update({r.t.isoformat(): round(r.h + perturb_truth, 3) for r in recs})
    return SR.StormRecord(
        label=label, captured_at=now.isoformat(), source="synthetic", notes="model self-truth",
        rain_start=rain_start.isoformat(), rain_hourly=rain, level_by_hour=truth,
        t0=t0.isoformat(), now=now.isoformat(), control_elev=control,
    )


def test_storm_record_roundtrip(art, tmp_path):
    rec = _self_truth_record(art)
    p = tmp_path / "s.json"
    SR.save(rec, p)
    assert SR.load(p) == rec


def test_score_reproduces_self_truth_and_is_artifact_sensitive(art):
    rec = _self_truth_record(art)
    m_same = SR.score(art, rec)["metrics"]
    assert m_same["rmse_ft"] == pytest.approx(0.0, abs=1e-6)      # model predicts its own truth
    perturbed = load_artifact()
    perturbed.hspf.PERC_coeff = 0.15                              # move a tunable parameter
    m_diff = SR.score(perturbed, rec)["metrics"]
    assert m_diff["rmse_ft"] > m_same["rmse_ft"]                  # scoring reacts to the artifact


def test_score_dataset_aggregate(art):
    recs = [_self_truth_record(art, "a"), _self_truth_record(art, "b", perturb_truth=0.05)]
    out = SR.score_dataset(art, recs)
    assert out["aggregate"]["n_records"] == 2
    assert out["aggregate"]["n_scored"] == 2
    assert out["aggregate"]["mean_rmse_ft"] >= 0.0
    assert {s["label"] for s in out["per_storm"]} == {"a", "b"}


def test_load_dataset_reads_a_directory(art, tmp_path):
    SR.save(_self_truth_record(art, "one"), tmp_path / "one.json")
    SR.save(_self_truth_record(art, "two"), tmp_path / "two.json")
    (tmp_path / "not-a-record.json").write_text("{}")            # junk is skipped, not fatal
    labels = {r.label for r in SR.load_dataset(tmp_path)}
    assert labels == {"one", "two"}


def test_cli_backtest_offline_scores_a_record(art, tmp_path):
    SR.save(_self_truth_record(art, "cli-storm"), tmp_path / "storm.json")
    result = runner.invoke(app, ["backtest-offline", str(tmp_path / "storm.json")])
    assert result.exit_code == 0
    assert "cli-storm" in result.stdout
    assert "RMSE" in result.stdout


def test_cli_backtest_offline_scores_a_directory(art, tmp_path):
    SR.save(_self_truth_record(art, "s1"), tmp_path / "s1.json")
    SR.save(_self_truth_record(art, "s2"), tmp_path / "s2.json")
    result = runner.invoke(app, ["backtest-offline", str(tmp_path)])
    assert result.exit_code == 0
    assert "aggregate" in result.stdout


def test_cli_backtest_offline_missing_path_fails(tmp_path):
    result = runner.invoke(app, ["backtest-offline", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
