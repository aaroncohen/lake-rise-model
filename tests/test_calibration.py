"""Stage 4 calibration pipeline: continuous archive + signature extractors."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
