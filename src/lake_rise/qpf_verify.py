"""Score rain forecasts against the gauge: NBM percentiles and ECMWF members (``qpf_archive``),
Apple WeatherKit and NWS (the forecast-of-record log), over the same periods -- each of the
first three days from the forecast's run time and the 3-day total.

For each source and period: the median's total against the measured total (bias), its mean
absolute error, and, for the ensembles, how often the measured rain fell at or below the 10th,
50th and 90th percentiles (well calibrated: 10 / 50 / 90 %). ``common=True`` keeps only the runs
every scored source has, so the sources are compared on the same weather.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta

PERIODS = ((0, 24, "day 1"), (24, 24, "day 2"), (48, 24, "day 3"), (0, 72, "3-day total"))
WET_IN = 0.1                                   # a "wet" period, for the wet-only numbers
QUANTILES = (10, 50, 90)


def _quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = (len(s) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def ensemble_quantiles(record: dict) -> dict[tuple[int, int], dict[int, float]]:
    """``(lead_h, hours) -> {10: q10, 50: q50, 90: q90}`` from an archived NBM or ECMWF cycle."""
    out = {}
    for p in record["periods"]:
        key = (p["lead_h"], p["hours"])
        if "percentiles_in" in p:
            out[key] = {q: p["percentiles_in"][str(q)] for q in QUANTILES}
        else:
            out[key] = {q: _quantile(p["members_in"], q) for q in QUANTILES}
    return out


def apple_totals(log_record: dict) -> dict[tuple[int, int], float]:
    by = {datetime.fromisoformat(x["datetime"].replace("Z", "+00:00")): x["precipitation_in"]
          for x in log_record.get("qpf_outlook") or []}
    t0 = datetime.fromisoformat(log_record["as_of"])
    out = {}
    for lead, hours, _ in PERIODS:
        v = [by.get(t0 + timedelta(hours=lead + i)) for i in range(hours)]
        if all(x is not None for x in v):
            out[(lead, hours)] = sum(v)
    return out


def nws_totals(log_record: dict) -> dict[tuple[int, int], float]:
    from .sources import nws

    o = log_record.get("nws_outlook")
    if not o or not o.get("qpf"):
        return {}
    t0 = datetime.fromisoformat(log_record["as_of"])
    blocks = [(datetime.fromisoformat(b["start"]), b["hours"], b["precipitation_in"])
              for b in o["qpf"]]
    out = {}
    for lead, hours, _ in PERIODS:
        a, z = t0 + timedelta(hours=lead), t0 + timedelta(hours=lead + hours)
        inside = [b for b in blocks if b[0] < z and b[0] + timedelta(hours=b[1]) > a]
        covered = (inside and min(b[0] for b in inside) <= a
                   and max(b[0] + timedelta(hours=b[1]) for b in inside) >= z)
        if covered and all(b[2] is not None for b in inside):
            out[(lead, hours)] = nws.total_in(o, a, hours)
    return out


def measured_total(rain: dict[datetime, float | None], start: datetime, hours: int
                   ) -> float | None:
    """The gauge's rain over ``[start, start + hours)`` (hour-start stamps), ``None`` if any
    hour is missing."""
    v = [rain.get(start + timedelta(hours=i)) for i in range(hours)]
    return None if any(x is None for x in v) else sum(v)


def forecasts_by_cycle(nbm: list[dict], ecmwf: list[dict], log: list[dict]
                       ) -> dict[str, dict[datetime, dict]]:
    """``source -> cycle -> {(lead_h, hours): {q: value}}``. Apple and NWS come from the log line
    issued at each 00Z/12Z cycle hour; deterministic, so only their median (q50) is filled."""
    out: dict[str, dict[datetime, dict]] = defaultdict(dict)
    for name, recs in (("nbm", nbm), ("ecmwf_ens", ecmwf)):
        for r in recs:
            out[name][datetime.fromisoformat(r["cycle"])] = ensemble_quantiles(r)
    for r in log:
        t = datetime.fromisoformat(r["as_of"])
        if t.minute or t.hour % 12:
            continue
        for name, fn in (("apple", apple_totals), ("nws", nws_totals)):
            totals = fn(r)
            if totals:
                out[name][t] = {k: {50: v} for k, v in totals.items()}
    return out


def score(forecasts: dict[str, dict[datetime, dict]], rain: dict[datetime, float | None], *,
          sources: list[str] | None = None, common: bool = False) -> list[dict]:
    """One row per source and period with the scores described above."""
    sources = sources or sorted(forecasts)
    cycles = {s: set(forecasts.get(s, {})) for s in sources}
    if common:
        shared = set.intersection(*cycles.values()) if cycles else set()
        cycles = {s: shared for s in sources}
    rows = []
    for s in sources:
        for lead, hours, label in PERIODS:
            pairs = []
            for c in sorted(cycles[s]):
                q = forecasts[s][c].get((lead, hours))
                m = measured_total(rain, c + timedelta(hours=lead), hours)
                if q is not None and m is not None:
                    pairs.append((m, q))
            if pairs:
                rows.append({"source": s, "period": label, **_metrics(pairs)})
    return rows


def _metrics(pairs: list[tuple[float, dict[int, float]]]) -> dict:
    meas = [m for m, _ in pairs]
    med = [q[50] for _, q in pairs]
    out = {"n": len(pairs), "measured_mean_in": round(statistics.mean(meas), 3),
           "median_over_measured": round(sum(med) / sum(meas), 2) if sum(meas) else None,
           "mae_in": round(statistics.mean(abs(f - m) for m, f in zip(meas, med)), 3)}
    wet = [(m, q) for m, q in pairs if m >= WET_IN]
    out["n_wet"] = len(wet)
    if wet:
        out["wet_median_over_measured"] = round(sum(q[50] for _, q in wet)
                                                / sum(m for m, _ in wet), 2)
    if all(10 in q and 90 in q for _, q in pairs):
        for q in QUANTILES:
            out[f"at_or_below_q{q}"] = round(sum(m <= qq[q] for m, qq in pairs) / len(pairs), 2)
        if wet:
            out["wet_above_q90"] = round(sum(m > q[90] for m, q in wet) / len(wet), 2)
    return out
