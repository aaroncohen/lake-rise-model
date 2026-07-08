"""Hydrological-signature extractors: set each auto-tunable parameter from the signature
that identifies it (NOT by fitting all three to storm error).

- AGWRC  <- master-recession constant from clean rain-free segments (Vogel & Kroll 1996):
           stage -> outflow Q via the model's spillway rating, drop the early reservoir-
           routing days, regress ln(Q) on time over the late (groundwater) tail.
- PERC   <- the long-run baseflow index BFI = Sum(baseflow)/Sum(total) toward ~0.67
           (Wolock 2003), solved with AGWRC fixed over the continuous record.
- leakage<- a dry-weather parameter, solved to the dry-equilibrium anchor.

Each returns a `SignatureResult` graded by data sufficiency (`confidence`). `none` means the
data can't identify it -> leave the parameter unchanged.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel

from .. import model, validate
from ..artifact import Artifact
from ..geometry import control_elev_for_stop_logs, default_stop_log_count
from ..registry import Registry
from ..spillway import spillway_outflow_cfs
from .archive import ContinuousRecord

CONFIDENCE = ("none", "low", "medium", "firm")


class SignatureResult(BaseModel):
    param: str
    proposed: float | None            # None when confidence == "none" (leave unchanged)
    current: float
    prior: float | None = None
    confidence: str                   # none | low | medium | firm
    evidence: dict[str, Any] = {}
    notes: str = ""

    @property
    def movement_from_prior(self) -> float | None:
        if self.proposed is None or self.prior is None:
            return None
        return round(self.proposed - self.prior, 4)

    @property
    def warning(self) -> str:
        if self.confidence == "none":
            return "no usable data — parameter left unchanged"
        if self.confidence == "low":
            return "LOW CONFIDENCE — early estimate from thin data, not a firm value"
        if self.confidence == "medium":
            return "MEDIUM CONFIDENCE — corroborate across more storms/seasons"
        return ""


# --- small numerics ---------------------------------------------------------------------

def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Ordinary least squares. Returns (slope, r_squared)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    slope = sxy / sxx if sxx > 0 else 0.0
    r2 = (sxy * sxy) / (sxx * syy) if sxx > 0 and syy > 0 else 0.0
    return slope, r2


def _control_elev(art: Artifact, t: datetime) -> float:
    count = default_stop_log_count(art.stop_logs, t.month, t.day)
    return control_elev_for_stop_logs(art.stop_logs, count)


def _stage_to_Q(art: Artifact, t: datetime, elev: float) -> float:
    return spillway_outflow_cfs(art.spillway, elev, _control_elev(art, t))


# --- AGWRC from rain-free recessions ----------------------------------------------------

def _rain_free_recession_segments(
    record: ContinuousRecord, min_days: int, rain_eps: float = 1e-4
) -> list[list[tuple[datetime, float]]]:
    """Contiguous runs of >= min_days of rain-free hours with a usable (declining) elevation.
    Returns each as a list of (hour, elev)."""
    segs: list[list[tuple[datetime, float]]] = []
    cur: list[tuple[datetime, float]] = []
    for s in record.samples:
        # A missing rain or gauge hour breaks the recession (unknown wetness), same as a wet hour.
        wet = s.elev_ft is None or s.rain_in is None or s.rain_in > rain_eps
        if wet:
            if cur:
                segs.append(cur)
                cur = []
            continue
        cur.append((datetime.fromisoformat(s.hour), s.elev_ft))
    if cur:
        segs.append(cur)
    out = []
    for seg in segs:
        if not seg:
            continue
        # Count rain-free hourly samples, not clock span: N points span only (N-1) hours, so an
        # exactly min_days*24-hour recession spans min_days - 1/24 days and a clock-span gate would
        # reject valid 120-hour recessions. Sample count is also more robust to gaps.
        if len(seg) >= min_days * 24 and seg[-1][1] < seg[0][1]:   # enough rain-free hours, net declining
            out.append(seg)
    return out


def recession_agwrc(record: ContinuousRecord, art: Artifact, reg: Registry,
                    min_days: int = 5, drop_days: float = 2.0) -> SignatureResult:
    spec = reg.parameters["hspf.AGWRC_per_day"]
    current = art.hspf.AGWRC_per_day
    segs = _rain_free_recession_segments(record, min_days)

    ks: list[float] = []
    weights: list[int] = []
    r2s: list[float] = []
    months: set[int] = set()
    for seg in segs:
        t0 = seg[0][0]
        late = [(t, e) for t, e in seg if (t - t0).total_seconds() / 86400 >= drop_days]
        qpts = [(t, _stage_to_Q(art, t, e)) for t, e in late]
        qpts = [(t, q) for t, q in qpts if q > 0]
        if len(qpts) < 3:
            continue
        base = qpts[0][0]
        t_days = [(t - base).total_seconds() / 86400 for t, _ in qpts]
        lnq = [math.log(q) for _, q in qpts]
        slope, r2 = _linreg(t_days, lnq)
        if slope >= 0:                                   # must be a recession
            continue
        ks.append(math.exp(slope))                       # daily recession ratio = AGWRC
        weights.append(len(qpts))
        r2s.append(r2)
        months.add(base.month)

    if not ks:
        return SignatureResult(
            param="hspf.AGWRC_per_day", proposed=None, current=current, prior=spec.prior,
            confidence="none", evidence={"segments_found": len(segs), "usable": 0},
            notes=f"no clean rain-free recession >= {min_days} d with a groundwater tail",
        )

    k_hat = sum(k * w for k, w in zip(ks, weights)) / sum(weights)
    k_hat = min(max(k_hat, spec.min), spec.max)
    mean_r2 = sum(r2s) / len(r2s)
    conf = _conf_from(n=len(ks), seasons=_n_seasons(months), quality=mean_r2)
    return SignatureResult(
        param="hspf.AGWRC_per_day", proposed=round(k_hat, 4), current=current, prior=spec.prior,
        confidence=conf,
        evidence={"n_recessions": len(ks), "mean_r2": round(mean_r2, 3),
                  "seasons": _n_seasons(months), "half_life_days": round(math.log(0.5) / math.log(k_hat), 1)},
        notes="Vogel & Kroll ln(Q) recession on the model-rating outflow, late groundwater tail",
    )


# --- PERC from the baseflow index -------------------------------------------------------

def _model_bfi(art: Artifact, record: ContinuousRecord) -> float | None:
    """Long-run BFI = Sum(groundwater baseflow) / Sum(total watershed inflow) from running the
    model over the record's rainfall. Returns None if there's negligible flow to split."""
    rain = [(s.rain_in or 0.0) for s in record.samples]   # missing hours -> 0 for the coarse BFI
    if not rain or sum(rain) <= 0:
        return None
    start = datetime.fromisoformat(record.samples[0].hour)
    control = _control_elev(art, start)
    state = model.initial_state(art, h0=control, sm0=art.seasonal_sm_default(start.month),
                                month=start.month)
    _, recs = model.run(art, state, rain, start, control)
    base = sum(r.q_agw_cfs for r in recs)
    total = sum(r.q_agw_cfs + r.q_in_cfs for r in recs)
    return base / total if total > 0 else None


def bfi_perc(record: ContinuousRecord, art: Artifact, reg: Registry,
             target: float = 0.67) -> SignatureResult:
    spec = reg.parameters["hspf.PERC_coeff"]
    current = art.hspf.PERC_coeff
    span_days = record.span_hours() / 24
    bfi_now = _model_bfi(art, record)
    if bfi_now is None or span_days < 30:
        return SignatureResult(
            param="hspf.PERC_coeff", proposed=None, current=current, prior=spec.prior,
            confidence="none", evidence={"span_days": round(span_days, 1), "bfi_now": bfi_now},
            notes="need >= ~30 d of continuous record with rain to identify the baseflow split",
        )

    # BFI increases monotonically with PERC_coeff -> bisect to the target.
    def bfi_at(pc: float) -> float:
        a = art.model_copy(deep=True)
        a.hspf.PERC_coeff = pc
        return _model_bfi(a, record) or 0.0

    lo, hi = spec.min, spec.max
    proposed = current
    if bfi_at(lo) <= target <= bfi_at(hi):
        for _ in range(40):
            mid = (lo + hi) / 2
            if bfi_at(mid) < target:
                lo = mid
            else:
                hi = mid
        proposed = (lo + hi) / 2
    else:
        proposed = lo if target < bfi_at(lo) else hi   # target outside achievable band -> clamp

    proposed = min(max(proposed, spec.min), spec.max)
    conf = _conf_from(n=1, seasons=_n_seasons({datetime.fromisoformat(s.hour).month
                                              for s in record.samples}),
                      quality=1.0 if span_days >= 90 else 0.4)
    return SignatureResult(
        param="hspf.PERC_coeff", proposed=round(proposed, 4), current=current, prior=spec.prior,
        confidence=conf,
        evidence={"target_bfi": target, "bfi_before": round(bfi_now, 3),
                  "bfi_after": round(bfi_at(proposed), 3), "span_days": round(span_days, 1)},
        notes=f"solved so long-run model BFI ~= {target} (Wolock grid) with AGWRC fixed",
    )


# --- leakage from the dry-equilibrium anchor --------------------------------------------

def leakage_dry_equilibrium(art: Artifact, reg: Registry,
                            record: ContinuousRecord | None = None) -> SignatureResult:
    spec = reg.parameters["spillway.leakage.cfs_per_ft2"]
    current = art.spillway.leakage.cfs_per_ft2
    lo_band, hi_band = art.validation_targets.dry_equilibrium_3logs_ft
    target = (lo_band + hi_band) / 2

    # Dry-equilibrium level DEcreases as leakage increases (more outflow) -> bisect.
    def settled(k: float) -> float:
        a = art.model_copy(deep=True)
        a.spillway.leakage.cfs_per_ft2 = k
        level, _ = validate.run_dry_equilibrium(a)
        return level

    lo, hi = spec.min, spec.max
    if settled(hi) <= target <= settled(lo):
        for _ in range(40):
            mid = (lo + hi) / 2
            if settled(mid) > target:      # too high -> need more leakage
                lo = mid
            else:
                hi = mid
        proposed = (lo + hi) / 2
    else:
        proposed = lo if target > settled(lo) else hi
    proposed = min(max(proposed, spec.min), spec.max)

    return SignatureResult(
        param="spillway.leakage.cfs_per_ft2", proposed=round(proposed, 4), current=current,
        prior=spec.prior, confidence="low",
        evidence={"dry_eq_target_ft": round(target, 3), "settled_before": round(settled(current), 3),
                  "settled_after": round(settled(proposed), 3)},
        notes="solved to the dry-equilibrium anchor centre; a dry-weather parameter (not storm-tuned)",
    )


# --- confidence heuristics --------------------------------------------------------------

def _n_seasons(months: set[int]) -> int:
    """Distinct wet/shoulder seasons touched (crude: count distinct calendar quarters)."""
    return len({(m - 1) // 3 for m in months})


def _conf_from(n: int, seasons: int, quality: float) -> str:
    if n <= 0:
        return "none"
    if n >= 5 and seasons >= 2 and quality >= 0.9:
        return "firm"
    if n >= 3 and quality >= 0.7:
        return "medium"
    return "low"
