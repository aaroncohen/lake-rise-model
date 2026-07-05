"""Parameter sensitivity sweep (Stage 3 of the calibration subsystem).

Vary ONE tunable parameter across its registry range, and at each value score the model
against the stored storm-truth dataset (Stage 2) and re-check the calibration anchors. This
answers "how does this parameter affect prediction accuracy?" and "where does it start
breaking the Step 6 / dry-equilibrium anchors?" -- the empirical picture a human (or, later,
the Stage 4 optimiser) needs before trusting a value.

It reuses everything below it unchanged: registry ranges + get/set, the mutable ``Artifact``
(each step scores a deep copy, never mutating the input), ``storm_record.score_dataset``, and
``validate.run_anchors``. A 1-D sweep holds all OTHER parameters fixed, so for a parameter
with ``couples_with`` entries (e.g. ``PERC_coeff`` trades against the Step 6 flood peak) the
curve is a sensitivity slice, not the joint optimum -- the ``couples_with`` note is surfaced
so that isn't mistaken for independence.
"""

from __future__ import annotations

from typing import Any

from . import storm_record as SR
from . import validate
from .artifact import Artifact
from .registry import Registry, get, set as _set


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    if n < 2:
        return [lo]
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]


def sweep_parameter(
    art: Artifact,
    reg: Registry,
    records: list[SR.StormRecord],
    path: str,
    steps: int = 9,
    include_anchors: bool = True,
) -> dict[str, Any]:
    """Sweep one scalar tunable parameter across its registry ``[min, max]``.

    Returns ``{path, range, current, prior, couples_with, n_records, rows}`` where each row is
    ``{value, is_current, is_prior, mean_rmse_ft, mean_abs_peak_err_ft,
    mean_abs_peak_timing_err_h, anchors_pass, anchors}``. The current value and the prior are
    always included as evaluated rows (marked), so the sweep shows how today's model and the
    research prior score relative to the range."""
    spec = reg.parameters.get(path)
    if spec is None:
        raise ValueError(f"'{path}' is not a registered parameter")
    if not spec.tunable:
        raise ValueError(f"'{path}' is class '{spec.cls}' and is not tunable")
    if spec.table or spec.min is None or spec.max is None:
        raise ValueError(f"'{path}' needs a scalar min/max to sweep (whole-table params are not supported)")

    lo, hi = spec.min, spec.max
    current = float(get(art, path))
    values = _linspace(lo, hi, steps)
    for extra in (current, spec.prior):                     # always score the current + prior
        if extra is not None and lo <= float(extra) <= hi:
            values.append(float(extra))
    values = sorted({round(v, 6) for v in values})

    rows: list[dict[str, Any]] = []
    for v in values:
        clone = art.model_copy(deep=True)                   # never mutate the caller's artifact
        _set(clone, path, v)
        agg = SR.score_dataset(clone, records)["aggregate"] if records else {}
        row: dict[str, Any] = {
            "value": v,
            "is_current": abs(v - current) < 1e-9,
            "is_prior": spec.prior is not None and abs(v - float(spec.prior)) < 1e-9,
            "mean_rmse_ft": agg.get("mean_rmse_ft"),
            "mean_abs_peak_err_ft": agg.get("mean_abs_peak_err_ft"),
            "mean_abs_peak_timing_err_h": agg.get("mean_abs_peak_timing_err_h"),
        }
        if include_anchors:
            results = validate.run_anchors(clone)
            row["anchors_pass"] = all(r.passed for r in results)
            row["anchors"] = {r.name: r.observed for r in results}
        rows.append(row)

    return {
        "path": path,
        "range": [lo, hi],
        "current": current,
        "prior": spec.prior,
        "couples_with": spec.couples_with,
        "n_records": len(records),
        "rows": rows,
    }
