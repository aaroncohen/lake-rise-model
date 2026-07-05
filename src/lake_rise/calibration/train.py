"""The training run: extract signatures, assemble a graded Candidate, enforce the hard
constraints (anchors + range), and apply the safety-weighted acceptance veto.

Calibration is UNBIASED (signatures + priors); the safety asymmetry lives only in the veto:
a candidate is rejected if it worsens peak UNDER-prediction or LATE timing against the stored
storms, even if average error improves.
"""

from __future__ import annotations

from typing import Any

from .. import registry as R
from .. import storm_record as SR
from .. import validate
from ..artifact import Artifact
from ..registry import Registry
from . import signatures as SIG
from .archive import ContinuousRecord
from .state import Candidate, ProposedParam, new_token, now_iso

_EPS = 1e-6


def train(art: Artifact, reg: Registry, continuous: ContinuousRecord,
          storms: list[SR.StormRecord], bfi_target: float = 0.67) -> Candidate:
    # 1. Signatures, in order (AGWRC -> PERC with AGWRC fixed -> leakage).
    r_agwrc = SIG.recession_agwrc(continuous, art, reg)
    work = art.model_copy(deep=True)
    if r_agwrc.proposed is not None:
        work.hspf.AGWRC_per_day = r_agwrc.proposed
    r_perc = SIG.bfi_perc(continuous, work, reg, target=bfi_target)
    r_leak = SIG.leakage_dry_equilibrium(work, reg, continuous)

    # 2. Build the candidate artifact; a proposal must stay within range to be applied.
    cand_art = art.model_copy(deep=True)
    params: list[ProposedParam] = []
    for res in (r_agwrc, r_perc, r_leak):
        spec = reg.parameters[res.param]
        current = float(R.get(art, res.param))
        keep = res.proposed
        conf = res.confidence
        note = res.notes
        if keep is not None and not R.in_range(spec, keep):
            keep, conf = None, "none"
            note = f"proposed {res.proposed} outside [{spec.min}, {spec.max}] — left unchanged"
        pp = ProposedParam(
            param=res.param, current=current, proposed=keep, prior=res.prior, confidence=conf,
            movement_from_prior=(None if keep is None or res.prior is None else round(keep - res.prior, 4)),
            warning=res.warning, evidence={**res.evidence, "notes": note},
        )
        if pp.changed:
            R.set(cand_art, res.param, keep)
        params.append(pp)

    # 3. Hard constraint: anchors must still pass.
    anchors_pass = all(r.passed for r in validate.run_anchors(cand_art))

    # 4. Safety-weighted acceptance veto over the stored storms.
    veto = _safety_veto(art, cand_art, storms)

    # 5. Multi-criteria acceptance table (reported separately, never one scalar).
    criteria = _criteria(art, cand_art, continuous, storms)

    banner = _banner(params, anchors_pass, veto)
    return Candidate(
        id=now_iso().replace(":", "").replace("-", ""), created_at=now_iso(),
        base_version=art.version, params=params, criteria=criteria,
        anchors_pass=anchors_pass, veto=veto, banner=banner, token=new_token(),
    )


def _signed_costs(agg_per_storm: list[dict]) -> tuple[float, float]:
    """(peak under-prediction, late-timing) severities summed across storms — the two
    dangerous directions. peak_err<0 = predicted below actual; timing>0 = predicted late."""
    under = sum(max(0.0, -s["peak_err_ft"]) for s in agg_per_storm if s.get("peak_err_ft") is not None)
    late = sum(max(0.0, s["peak_timing_err_h"]) for s in agg_per_storm if s.get("peak_timing_err_h") is not None)
    return under, late


def _safety_veto(before_art: Artifact, after_art: Artifact,
                 storms: list[SR.StormRecord]) -> dict[str, Any]:
    if not storms:
        return {"passed": True, "reason": "no stored storms to validate against (advisory only)",
                "n_storms": 0}
    b = SR.score_dataset(before_art, storms)["per_storm"]
    a = SR.score_dataset(after_art, storms)["per_storm"]
    under_b, late_b = _signed_costs(b)
    under_a, late_a = _signed_costs(a)
    worse_under = under_a > under_b + _EPS
    worse_late = late_a > late_b + _EPS
    passed = not (worse_under or worse_late)
    reason = "no worse on peak under-prediction or late timing" if passed else (
        ("worsens peak under-prediction" if worse_under else "") +
        ("; " if worse_under and worse_late else "") +
        ("worsens late timing" if worse_late else ""))
    return {"passed": passed, "reason": reason, "n_storms": len(storms),
            "under_pred_ft": {"before": round(under_b, 3), "after": round(under_a, 3)},
            "late_timing_h": {"before": round(late_b, 2), "after": round(late_a, 2)}}


def _criteria(before: Artifact, after: Artifact, continuous: ContinuousRecord,
              storms: list[SR.StormRecord]) -> dict[str, Any]:
    def dry_eq(a: Artifact) -> float:
        return round(validate.run_dry_equilibrium(a)[0], 3)

    def bfi(a: Artifact) -> float | None:
        v = SIG._model_bfi(a, continuous)
        return round(v, 3) if v is not None else None

    out: dict[str, Any] = {
        "low_flow_dry_eq_ft": {"before": dry_eq(before), "after": dry_eq(after)},
        "water_balance_bfi": {"before": bfi(before), "after": bfi(after)},
    }
    if storms:
        b = SR.score_dataset(before, storms)["aggregate"]
        a = SR.score_dataset(after, storms)["aggregate"]
        out["storm_peak_abs_err_ft"] = {"before": b["mean_abs_peak_err_ft"], "after": a["mean_abs_peak_err_ft"]}
        out["storm_timing_abs_err_h"] = {"before": b["mean_abs_peak_timing_err_h"], "after": a["mean_abs_peak_timing_err_h"]}
        out["storm_rmse_ft"] = {"before": b["mean_rmse_ft"], "after": a["mean_rmse_ft"]}
    return out


def _banner(params: list[ProposedParam], anchors_pass: bool, veto: dict) -> str:
    changed = [p for p in params if p.changed]
    if not changed:
        return "No change proposed — the data does not yet identify any parameter."
    if not anchors_pass:
        return "⚠ REJECTED — a proposed value breaks a calibration anchor; do not apply."
    if not veto.get("passed", True):
        return f"⚠ REJECTED by safety veto — {veto.get('reason')}."
    worst = min((p.confidence for p in changed),
                key=lambda c: SIG.CONFIDENCE.index(c))
    if worst in ("low", "medium"):
        return (f"⚠ {worst.upper()} CONFIDENCE — {len(changed)} change(s) from thin data; "
                "review the evidence before approving.")
    return f"{len(changed)} change(s) proposed at firm confidence; review and approve."
