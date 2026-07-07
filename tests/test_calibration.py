"""Calibration pipeline: continuous archive + signature extractors."""

from datetime import datetime, timedelta, timezone

import pytest

from lake_rise.artifact import load_artifact
from lake_rise.calibration import archive as A, signatures as S
from lake_rise.geometry import control_elev_for_stop_logs, default_stop_log_count
from lake_rise.registry import load_registry
from lake_rise.spillway import spillway_outflow_cfs


@pytest.fixture
def reg():
    return load_registry()


# --- continuous archive ------------------------------------------------------------------

def _samples(start, n, elev=340.0, rain=0.0):
    return [A.HourSample(hour=(start + timedelta(hours=h)).isoformat(), elev_ft=elev, rain_in=rain)
            for h in range(n)]


def test_archive_append_is_idempotent_by_hour(tmp_path):
    p = tmp_path / "c.json"
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    A.append_samples(_samples(start, 5), p)
    A.append_samples(_samples(start, 5), p)          # same window again
    rec = A.load(p)
    assert len(rec.samples) == 5                     # merged by hour, not duplicated
    assert [s.hour for s in rec.samples] == sorted(s.hour for s in rec.samples)


def test_archive_merge_extends_and_keeps_real_readings(tmp_path):
    p = tmp_path / "c.json"
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    A.append_samples(_samples(start, 3, elev=340.0), p)
    # overlapping window whose overlap hours have a gap (None) must not erase real readings
    gap = [A.HourSample(hour=(start + timedelta(hours=h)).isoformat(), elev_ft=None, rain_in=0.1)
           for h in range(2, 5)]
    rec = A.append_samples(gap, p)
    assert len(rec.samples) == 5
    assert rec.samples[2].elev_ft == 340.0           # kept the real reading over the gap
    assert rec.samples[2].rain_in == 0.1             # took the new rain


# --- AGWRC from a rain-free recession -----------------------------------------------------

def _geometric_recession(art, start, k_true, days=14):
    """A rain-free record whose model-rating outflow Q declines at a known daily ratio k, by
    inverting the spillway rating to elevations. The extractor should recover k exactly."""
    count = default_stop_log_count(art.stop_logs, start.month, start.day)
    control = control_elev_for_stop_logs(art.stop_logs, count)

    def q_of_h(h):
        return spillway_outflow_cfs(art.spillway, h, control)

    def h_of_q(qt):
        lo, hi = control, control + 3.0
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if q_of_h(mid) < qt else (lo, mid)
        return (lo + hi) / 2

    q0 = q_of_h(control + 0.8)
    return A.ContinuousRecord(samples=[
        A.HourSample(hour=(start + timedelta(hours=hr)).isoformat(),
                     elev_ft=round(h_of_q(q0 * k_true ** (hr / 24)), 4), rain_in=0.0)
        for hr in range(days * 24)
    ])


def test_recession_recovers_known_agwrc(art, reg):
    rec = _geometric_recession(art, datetime(2026, 7, 1, tzinfo=timezone.utc), k_true=0.95)
    res = S.recession_agwrc(rec, art, reg, min_days=5, drop_days=2.0)
    assert res.proposed == pytest.approx(0.95, abs=0.01)     # Vogel & Kroll recovers k
    assert res.evidence["mean_r2"] > 0.99
    assert res.confidence in ("low", "medium", "firm")


def test_recession_none_when_no_clean_segment(art, reg):
    res = S.recession_agwrc(A.ContinuousRecord(samples=[]), art, reg)
    assert res.proposed is None and res.confidence == "none"  # leave AGWRC unchanged
    assert "unchanged" in res.warning


def test_recession_accepts_exactly_min_days_of_samples(art, reg):
    # min_days*24 hourly points span only min_days - 1/24 days; the old clock-span gate rejected
    # this valid 120-hour (5-day) recession. Counting samples must accept it.
    rec = _geometric_recession(art, datetime(2026, 7, 1, tzinfo=timezone.utc), k_true=0.95, days=5)
    assert len(rec.samples) == 5 * 24
    res = S.recession_agwrc(rec, art, reg, min_days=5, drop_days=2.0)
    assert res.proposed == pytest.approx(0.95, abs=0.01)
    assert res.evidence["n_recessions"] >= 1


# --- PERC from BFI -----------------------------------------------------------------------

def _stormy_record(start, days=45):
    """A continuous record with storms heavy enough to generate interflow (so BFI is < 1 and
    movable by PERC), for the baseflow-split solver."""
    samples = []
    for hr in range(days * 24):
        t = start + timedelta(hours=hr)
        rain = 0.25 if (hr % (72)) < 12 else 0.0     # ~3 in burst every 3 days
        samples.append(A.HourSample(hour=t.isoformat(), elev_ft=339.7, rain_in=rain))
    return A.ContinuousRecord(samples=samples)


def test_bfi_perc_solver_hits_an_achievable_target(art, reg):
    rec = _stormy_record(datetime(2026, 3, 1, tzinfo=timezone.utc))
    lo_bfi = S._model_bfi(_with_perc(art, reg.parameters["hspf.PERC_coeff"].min), rec)
    hi_bfi = S._model_bfi(_with_perc(art, reg.parameters["hspf.PERC_coeff"].max), rec)
    target = round((lo_bfi + hi_bfi) / 2, 3)         # guaranteed achievable in [min,max]
    res = S.bfi_perc(rec, art, reg, target=target)
    assert res.proposed is not None
    assert res.evidence["bfi_after"] == pytest.approx(target, abs=0.02)


def test_bfi_perc_is_monotonic_in_perc(art, reg):
    rec = _stormy_record(datetime(2026, 3, 1, tzinfo=timezone.utc))
    lo = S._model_bfi(_with_perc(art, 0.10), rec)
    hi = S._model_bfi(_with_perc(art, 0.50), rec)
    assert hi > lo                                   # more percolation -> more baseflow -> higher BFI


def test_bfi_perc_none_on_short_record(art, reg):
    short = A.ContinuousRecord(samples=_samples(datetime(2026, 3, 1, tzinfo=timezone.utc), 24, rain=0.05))
    res = S.bfi_perc(short, art, reg)
    assert res.proposed is None and res.confidence == "none"


def _with_perc(art, pc):
    a = art.model_copy(deep=True)
    a.hspf.PERC_coeff = pc
    return a


# --- leakage from dry-equilibrium --------------------------------------------------------

def test_leakage_solves_to_dry_equilibrium_band(art, reg):
    res = S.leakage_dry_equilibrium(art, reg)
    lo, hi = art.validation_targets.dry_equilibrium_3logs_ft
    assert res.proposed is not None
    assert lo <= res.evidence["settled_after"] <= hi     # keeps the dry-eq anchor
    assert res.confidence == "low"


# --- pipeline: train -> approve -> version -> revert -------------------------------------

from lake_rise import model, storm_record as SR                         # noqa: E402
from lake_rise.alerting.config import SMTPConfig                        # noqa: E402
from lake_rise.calibration import report, service, train                # noqa: E402
from lake_rise.calibration.config import CalibrationConfig             # noqa: E402
from lake_rise.calibration.state import load_state, save_state         # noqa: E402


def _cfg(tmp_path):
    return CalibrationConfig(
        enabled=True, recipient=None, bfi_target=0.67, min_recession_days=5,
        state_path=tmp_path / "state.json", versions_path=tmp_path / "versions",
        api_token=None, ui_base_url=None, template_path=None,
        smtp=SMTPConfig(host="", port=587, user=None, password=None, sender="", starttls=True),
    )


def _self_truth_storm(art, label="s"):
    from lake_rise.geometry import control_elev_for_stop_logs
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    rs = datetime(2026, 3, 1, tzinfo=timezone.utc)
    t0 = rs + timedelta(hours=24)
    now = t0 + timedelta(hours=24)
    rain = [0.05] * 24 + [0.1] * 24
    st, _ = model.hindcast(art, rain[:24], h0=control, start=rs, control_elev=control)
    st.h = control
    _, recs = model.run(art, st, rain[24:], start=t0, control_elev=control)
    truth = {t0.isoformat(): round(control, 3)}
    truth.update({r.t.isoformat(): round(r.h, 3) for r in recs})
    return SR.StormRecord(label=label, captured_at=now.isoformat(), source="synthetic",
                          rain_start=rs.isoformat(), rain_hourly=rain, level_by_hour=truth,
                          t0=t0.isoformat(), now=now.isoformat(), control_elev=control)


def test_train_approve_promote_revert(art, reg, tmp_path):
    cfg = _cfg(tmp_path)
    rec = _geometric_recession(art, datetime(2026, 7, 1, tzinfo=timezone.utc), k_true=0.95)
    (tmp_path / "cont.json").write_text(rec.model_dump_json())

    cand = service.run_training(cfg, continuous_path=tmp_path / "cont.json",
                                storms_path=tmp_path / "none")
    assert cand.changed_params and cand.acceptable          # a recession proposes AGWRC
    assert load_state(cfg.state_path).pending.id == cand.id

    version = service.approve(cfg, cand.id, cand.token)
    assert version == "v1"
    active = service.active_artifact(cfg)
    assert active.hspf.AGWRC_per_day == pytest.approx(0.95, abs=0.01)   # the proposed value is live
    assert load_state(cfg.state_path).pending is None       # one-shot: pending cleared

    service.revert(cfg, "v0")
    assert service.active_artifact(cfg).hspf.AGWRC_per_day == art.hspf.AGWRC_per_day


def test_approve_requires_valid_token_and_is_single_use(art, tmp_path):
    cfg = _cfg(tmp_path)
    rec = _geometric_recession(art, datetime(2026, 7, 1, tzinfo=timezone.utc), k_true=0.95)
    (tmp_path / "cont.json").write_text(rec.model_dump_json())
    cand = service.run_training(cfg, continuous_path=tmp_path / "cont.json",
                                storms_path=tmp_path / "none")
    with pytest.raises(ValueError):
        service.approve(cfg, cand.id, "wrong-token")
    service.approve(cfg, cand.id, cand.token)
    with pytest.raises(ValueError):                          # token can't be reused
        service.approve(cfg, cand.id, cand.token)


def test_approve_rejects_candidate_trained_against_superseded_version(art, tmp_path):
    """A proposal whose base_version no longer matches active (a concurrent approve/revert moved
    the pointer) must be refused -- promote() would otherwise write the delta onto the stale base
    and silently diverge from what the operator sees as active."""
    cfg = _cfg(tmp_path)
    rec = _geometric_recession(art, datetime(2026, 7, 1, tzinfo=timezone.utc), k_true=0.95)
    (tmp_path / "cont.json").write_text(rec.model_dump_json())
    cand = service.run_training(cfg, continuous_path=tmp_path / "cont.json",
                                storms_path=tmp_path / "none")
    assert cand.base_version == "v0"

    # The active pointer moves out from under the pending proposal.
    st = load_state(cfg.state_path)
    st.active_version = "v1"
    save_state(st, cfg.state_path)

    with pytest.raises(ValueError, match="re-train"):
        service.approve(cfg, cand.id, cand.token)


def test_revert_clears_stale_pending_proposal(art, tmp_path):
    """A rollback drops any pending proposal (trained against the pre-revert head), so it can't be
    approved from a base that no longer matches active."""
    cfg = _cfg(tmp_path)
    rec = _geometric_recession(art, datetime(2026, 7, 1, tzinfo=timezone.utc), k_true=0.95)
    (tmp_path / "cont.json").write_text(rec.model_dump_json())
    cand1 = service.run_training(cfg, continuous_path=tmp_path / "cont.json",
                                 storms_path=tmp_path / "none")
    service.approve(cfg, cand1.id, cand1.token)             # active -> v1, pending cleared

    cand2 = service.run_training(cfg, continuous_path=tmp_path / "cont.json",
                                 storms_path=tmp_path / "none")
    assert load_state(cfg.state_path).pending.id == cand2.id   # a v1-based proposal is pending

    service.revert(cfg, "v0")                               # rollback drops the stale proposal
    assert load_state(cfg.state_path).pending is None
    with pytest.raises(ValueError):                         # nothing pending to approve
        service.approve(cfg, cand2.id, cand2.token)


def test_signed_costs_count_under_prediction_and_lateness(art):
    per_storm = [{"peak_err_ft": -0.2, "peak_timing_err_h": 1.5},   # under & late
                 {"peak_err_ft": 0.3, "peak_timing_err_h": -1.0}]   # over & early (not counted)
    under, late = train._signed_costs(per_storm)
    assert under == pytest.approx(0.2) and late == pytest.approx(1.5)


def test_safety_veto_passes_when_unchanged(art):
    storm = _self_truth_storm(art)
    assert train._safety_veto(art, art, [storm])["passed"] is True   # identical -> no worsening


def test_safety_veto_rejects_when_under_prediction_worsens(art, monkeypatch):
    storm = _self_truth_storm(art)
    seq = iter([  # before, then after: the after under-predicts the peak more
        {"per_storm": [{"peak_err_ft": -0.1, "peak_timing_err_h": 0.0}], "aggregate": {}},
        {"per_storm": [{"peak_err_ft": -0.4, "peak_timing_err_h": 0.0}], "aggregate": {}},
    ])
    monkeypatch.setattr(train.SR, "score_dataset", lambda a, s: next(seq))
    veto = train._safety_veto(art, art.model_copy(deep=True), [storm])
    assert veto["passed"] is False and "under-prediction" in veto["reason"]


def test_cli_calibration_train_status_approve_revert(art, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from lake_rise.cli import app

    rec = _geometric_recession(art, datetime(2026, 7, 1, tzinfo=timezone.utc), k_true=0.95)
    (tmp_path / "cont.json").write_text(rec.model_dump_json())
    monkeypatch.setenv("CALIB_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("CALIB_VERSIONS_PATH", str(tmp_path / "versions"))
    r = CliRunner()

    out = r.invoke(app, ["calibration", "train", "--continuous", str(tmp_path / "cont.json"),
                         "--storms", str(tmp_path / "none")])
    assert out.exit_code == 0 and "CALIBRATION PROPOSAL" in out.stdout

    st = load_state(tmp_path / "state.json")
    cid, token = st.pending.id, st.pending.token
    ap = r.invoke(app, ["calibration", "approve", cid, "--token", token])
    assert ap.exit_code == 0 and "v1" in ap.stdout
    assert load_state(tmp_path / "state.json").active_version == "v1"

    rv = r.invoke(app, ["calibration", "revert", "v0"])
    assert rv.exit_code == 0
    assert load_state(tmp_path / "state.json").active_version == "v0"


def test_report_renders_banner_and_instructions(art, reg, tmp_path):
    cfg = _cfg(tmp_path)
    rec = _geometric_recession(art, datetime(2026, 7, 1, tzinfo=timezone.utc), k_true=0.95)
    cand = train.train(art, reg, rec, storms=[], bfi_target=0.67)
    rendered = report.render(cand, cfg)
    assert "CALIBRATION PROPOSAL" in rendered.text_body
    assert cand.token in rendered.text_body                 # approve instruction present
    assert "CONFIDENCE" in rendered.text_body               # confidence surfaced


def test_promote_accumulates_from_active_base(art, reg, tmp_path):
    """Second promote must inherit v1's params, not reset to v0 + only the new delta."""
    from lake_rise.artifact import load_artifact
    from lake_rise.calibration.state import Candidate, ProposedParam, new_token, now_iso, promote

    cfg = _cfg(tmp_path)
    rec = _geometric_recession(art, datetime(2026, 7, 1, tzinfo=timezone.utc), k_true=0.95)
    cand1 = train.train(art, reg, rec, storms=[])
    v1 = promote(cand1, versions_path=cfg.versions_path)
    v1_art = load_artifact(cfg.versions_path / f"crystal_lake_{v1}.json")
    assert v1_art.hspf.AGWRC_per_day == pytest.approx(0.95, abs=0.01)

    new_perc = round(v1_art.hspf.PERC_coeff * 0.99, 4)
    cand2 = Candidate(
        id="c2", created_at=now_iso(), base_version=v1,
        params=[ProposedParam(
            param="hspf.PERC_coeff", current=v1_art.hspf.PERC_coeff,
            proposed=new_perc, confidence="firm",
        )],
        anchors_pass=True, veto={"passed": True}, token=new_token(),
    )
    v2 = promote(cand2, versions_path=cfg.versions_path)
    v2_art = load_artifact(cfg.versions_path / f"crystal_lake_{v2}.json")
    assert v2_art.hspf.AGWRC_per_day == pytest.approx(0.95, abs=0.01)
    assert v2_art.hspf.PERC_coeff == pytest.approx(new_perc)


def test_train_respects_min_recession_days(art, reg):
    rec = _geometric_recession(
        art, datetime(2026, 7, 1, tzinfo=timezone.utc), k_true=0.95, days=4)
    too_short = train.train(art, reg, rec, storms=[], min_recession_days=5)
    agwrc_short = next(p for p in too_short.params if p.param == "hspf.AGWRC_per_day")
    assert not agwrc_short.changed

    long_enough = train.train(art, reg, rec, storms=[], min_recession_days=3)
    agwrc_ok = next(p for p in long_enough.params if p.param == "hspf.AGWRC_per_day")
    assert agwrc_ok.changed
    assert agwrc_ok.proposed == pytest.approx(0.95, abs=0.01)
