"""Validation against the calibration anchors from both design docs. Shared by the
``lake-rise validate`` command and the test suite so they can't drift."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import model, sim
from .artifact import Artifact
from .geometry import control_elev_for_stop_logs


@dataclass
class AnchorResult:
    name: str
    target: str
    observed: str
    passed: bool


def run_step6(art: Artifact) -> tuple[float, list[model.StepRecord]]:
    """Saturated watershed hit by the Step 6 storm. Returns (peak_elev, records)."""
    start = datetime(2026, 1, 1)  # winter: negligible PET, watershed stays saturated
    control = control_elev_for_stop_logs(art.stop_logs, 0)  # no stop-logs in January
    state = model.initial_state(art, h0=art.geometry.datum_base_elev_ft,
                                sm0=art.hspf.LZSN_in, s_if0=0.0, month=start.month)
    _, records = model.run(art, state, sim.step6_hyetograph(art), start, control)
    peak = max(r.h for r in records)
    return peak, records


def run_dry_equilibrium(art: Artifact, hours: int = 72) -> tuple[float, list[model.StepRecord]]:
    """3 stop-logs, summer, no rain. Lake should rest near the control elevation."""
    start = datetime(2026, 7, 1)
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    state = model.initial_state(art, h0=control, sm0=art.seasonal_sm_default(7), s_if0=0.0,
                                month=start.month)
    _, records = model.run(art, state, sim.dry(hours), start, control)
    return records[-1].h, records


def run_anchors(art: Artifact) -> list[AnchorResult]:
    vt = art.validation_targets
    results: list[AnchorResult] = []

    peak, _ = run_step6(art)
    tol = vt.step6_peak_tolerance_ft
    results.append(AnchorResult(
        name="Step 6 storm peak (saturated)",
        target=f"{vt.step6_peak_elev_ft:.2f} ± {tol:.2f} ft",
        observed=f"{peak:.2f} ft",
        passed=abs(peak - vt.step6_peak_elev_ft) <= tol,
    ))

    final, _ = run_dry_equilibrium(art)
    lo, hi = vt.dry_equilibrium_3logs_ft
    results.append(AnchorResult(
        name="Dry-weather equilibrium (3 logs)",
        target=f"{lo:.2f}–{hi:.2f} ft",
        observed=f"{final:.3f} ft",
        passed=lo <= final <= hi,
    ))

    return results
