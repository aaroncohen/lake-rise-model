"""The lake filter's tuning study (docs/plans/lake-filter.md, T3; ``lake-rise lake-filter-study``).

On the minute archive's real gauge noise it compares the filter with today's anchor (the 30-min
median of ``lake_sensor``): hour-to-hour jitter, dry and wet, and the 99th-percentile error
against a "truth" that is the centred 4 h median of the raw depth (not independent of the gauge).
Synthetic storm ramps added to the real readings check P12. Every estimate is a stateless call
over the trailing ``history_hours``, as live (the same rain and replay-inflow alignment), so it is
evaluated only at sampled minutes."""

from __future__ import annotations

import bisect
import itertools
import math
import re
import statistics
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import antecedent, gauge_minute, units
from .artifact import Artifact, load_artifact
from .calibration import archive
from .fsutil import atomic_write_text
from .geometry import default_stop_log_count, surface_area_acres
from .hourly import floor_hour, rain_hourly_from_daily_accumulator
from .lake_filter import DT_H, estimate_lake, inflow_by_minute, minute_grid, rain_by_minute
from .model import m5_lake_update, m6_spillway

MIN_DAYS = 7
TRUTH_HOURS = 4                          # the centred median that stands in for the truth
ANCHOR_MINUTES = 30                      # today's anchor: the 30-min median of lake_sensor
JITTER_MINUTES = 60                      # hour-to-hour
RAMP_RATES_FT_PER_H = (0.05, 0.1)
RAMP_HOURS = 6
ONSET_HOURS = 2                          # the gradual onset reaches full rate over 2 h
STORM_RAIN_IN_PER_H = 0.25
RAMP_EVERY_HOURS = 15                    # not 24: onsets rotate through 8 times of day
CASES = {"right": 1.0, "half": 0.5, "blind": 0.0}   # share of the ramp's inflow the model sees
# "outlasts": the abrupt ramp's full 6 h, but the rain stops 2 h after the onset (a basin keeps
# delivering for hours after the rain; ruling on #68). "rain_starts": the abrupt ramp with the rain
# starting at the onset, after a dry spell, so the filter's wetness has to build (#74).
SHAPES = ("abrupt", "gradual", "outlasts", "rain_starts")
RAIN_LEAD_HOURS = {"abrupt": 2, "gradual": 2, "outlasts": 2, "rain_starts": 0}   # rain before the ramp
RAIN_AFTER_ONSET_HOURS = {"abrupt": RAMP_HOURS, "gradual": RAMP_HOURS, "outlasts": 2,
                          "rain_starts": RAMP_HOURS}
CHECK_MINUTES = (30, 60, 120)            # after the onset
AFTER_RAIN_CHECK_MINUTES = (60, 120, 240)   # after the rain stops, for a rise that outlasts it
P12_BLIND_EXTRA_FT = {30: 0.03, 60: 0.03}   # P12, blind, in rain; every other check: never further behind


def checks(shape: str) -> tuple[int, ...]:
    """Minutes after the onset at which ``shape`` is checked."""
    stop = RAIN_AFTER_ONSET_HOURS[shape] * 60
    return CHECK_MINUTES + (tuple(stop + m for m in AFTER_RAIN_CHECK_MINUTES) if stop < RAMP_HOURS * 60 else ())


ALL_CHECKS = sorted({m for shape in SHAPES for m in checks(shape)})
GRID = {"rate_sd_wet_ft_per_min": (3e-5, 5e-5, 1e-4, 2e-4),
        "wet_window_hours": (3, 6, 12),
        "offset_minutes": (15, 20, 30),
        "wet_full_in": (0.05, 0.1, 0.2)}
STORM_RISE_FT = 0.10                     # step 4: a settled 24 h rise ...
STORM_RAIN_IN = 0.5                      # ... with this much rain
SETTLE_HOURS = 24
DRY_SPLIT_HOURS = max(GRID["wet_window_hours"])   # one dry/wet split for every combination; also
                                                  # the dry spell before a "rain_starts" ramp
MAX_MISSING_SHARE = 1 / 3                # more ramps than this without an estimate fails the case


@dataclass
class StudyData:
    """The minute archive on one grid (element k is minute ``start + k``), with the replay's
    hourly inflow and the stop-log control per minute."""
    start: datetime
    raw: np.ndarray                      # lake_raw_sensor, stick ft (nan = missing)
    smoothed: np.ndarray                 # lake_sensor, stick ft
    truth: np.ndarray                    # centred 4 h median of raw
    rain_states: list[tuple[datetime, float | None]]   # the daily accumulator
    hourly_rain: list[float | None]      # per clock hour from floor_hour(start)
    inflow_hourly_cfs: list[float]       # the pure replay's watershed inflow, oldest first ...
    inflow_as_of: datetime               # ... the last one the hour ending here
    control_elev_abs: np.ndarray


def _nan(values: list[float | None]) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def _states(rows: list[tuple[datetime, str]]) -> list[tuple[datetime, float | None]]:
    out = []
    for t, s in rows:
        try:
            out.append((t, float(s)))
        except ValueError:
            out.append((t, None))
    return out


def load(art: Artifact, cfg, minute_dir: str | None) -> StudyData:
    """Raises ``ValueError`` with fewer than ``MIN_DAYS`` of minute readings or no replay."""
    d = gauge_minute._dir(minute_dir)
    days = sorted(p.stem for p in Path(d).glob("????-??-??.json"))
    if not days:
        raise ValueError(f"no minute archive in {d}")
    first = datetime.fromisoformat(days[0]).replace(tzinfo=timezone.utc)
    rows = gauge_minute.load(first, datetime.fromisoformat(days[-1]).replace(tzinfo=timezone.utc)
                             + timedelta(days=1), d)
    raw_rows = rows.get(cfg.lake_raw_sensor, [])
    if not raw_rows:
        raise ValueError(f"invalid study input: no {cfg.lake_raw_sensor} readings in {d}")
    end = raw_rows[-1][0]
    n = int((end - raw_rows[0][0]).total_seconds() // 60) + 1
    start = end.replace(second=0, microsecond=0) - timedelta(minutes=n - 1)
    raw = _nan(minute_grid(raw_rows, end, n))
    smoothed = _nan(minute_grid(rows.get(cfg.lake_sensor, []), end, n))
    for entity, series in ((cfg.lake_raw_sensor, raw), (cfg.lake_sensor, smoothed)):
        valid = np.flatnonzero(~np.isnan(series))
        span = timedelta(minutes=int(valid[-1] - valid[0])) if len(valid) else timedelta(0)
        if span < timedelta(days=MIN_DAYS):
            raise ValueError(f"invalid study input: {span} of valid {entity} readings in {d}; "
                             f"the study needs {MIN_DAYS} days")
    truth = (pd.Series(raw).rolling(TRUTH_HOURS * 60 + 1, center=True, min_periods=TRUTH_HOURS * 30)
             .median().to_numpy())
    rain_states = _states(rows.get(cfg.rain_sensor, []))
    hourly_rain = rain_hourly_from_daily_accumulator(rain_states, floor_hour(start),
                                                     floor_hour(end) + timedelta(hours=1))

    # One replay covers the window: its inflow is a pure function of the archived rain.
    rec = archive.load_window(end - timedelta(days=2), end + timedelta(days=1))
    rained = [datetime.fromisoformat(s.hour) for s in rec.samples if s.rain_in is not None]
    if not rained:
        raise ValueError("the continuous archive has no rain near the minute window")
    as_of = min(rained[-1] + timedelta(hours=1), floor_hour(end) + timedelta(hours=1))
    replay = antecedent.archive_replay(art, as_of, [])
    if replay is None:
        raise ValueError(f"the continuous archive can't be replayed to {as_of.isoformat()}")
    times = [start + timedelta(minutes=k) for k in range(n)]

    logs = minute_grid(rows.get(cfg.stop_log_helper, []), end, n) if cfg.stop_log_helper else [None] * n
    control = np.array([art.stop_logs.control_elev(
        int(c) if c is not None else default_stop_log_count(art.stop_logs, t.month, t.day))
        for c, t in zip(logs, times)])
    return StudyData(start, raw, smoothed, truth, rain_states, hourly_rain, replay[1], as_of, control)


def _time(d: StudyData, k: int) -> datetime:
    return d.start + timedelta(minutes=k)


def _rain(d: StudyData, k: int, n: int) -> list[float | None]:
    """The rain a live call at minute k would see: completed hours spread evenly, the current
    hour's accumulator rise so far over its minutes."""
    t = _time(d, k)
    hour = floor_hour(t)
    done = int((hour - floor_hour(d.start)).total_seconds() // 3600)
    upto = bisect.bisect_right([s[0] for s in d.rain_states], t)
    partial = rain_hourly_from_daily_accumulator(d.rain_states[:upto], hour,
                                                 hour + timedelta(hours=1))[0]
    return rain_by_minute(d.hourly_rain[:done], partial, t, n)


def _inflow(d: StudyData, k: int, n: int) -> np.ndarray:
    """The replay inflow a live call at minute k would see: the replay run to the top of k's
    hour, its last hour held over the current one (``inflow_by_minute``; none -> 0, as u_in)."""
    t = _time(d, k)
    as_of = min(floor_hour(t), d.inflow_as_of)
    keep = len(d.inflow_hourly_cfs) - int((d.inflow_as_of - as_of).total_seconds() // 3600)
    q = inflow_by_minute(d.inflow_hourly_cfs[:keep], as_of, t, n)
    return np.zeros(n) if q is None else np.array(q)


def _floats(a: np.ndarray) -> list[float | None]:
    return [None if math.isnan(v) else float(v) for v in a]


def _anchor(smoothed: np.ndarray) -> float | None:
    w = smoothed[-ANCHOR_MINUTES:]
    return None if np.isnan(w).all() else float(np.nanmedian(w))


def _sd(x: list[float]) -> float | None:
    return float(np.std(x)) if len(x) > 1 else None


def _dry(rain: list[float | None]) -> bool:
    """The common split: no rain (and none unknown) in the DRY_SPLIT_HOURS before the call."""
    recent = rain[-DRY_SPLIT_HOURS * 60:]
    return all(r is not None for r in recent) and sum(recent) == 0


def noise_table(art: Artifact, d: StudyData, estimate=None) -> dict:
    """Hour-to-hour jitter sd of the filter and today's anchor, split dry/wet by ``_dry`` at the
    later call (the same split for every combination), and each one's 99th-percentile |error|
    against the truth, over the calls where both have a value. A split without pairs (a
    wet-season window may have no dry hour) has jitter None and 0 pairs."""
    estimate = estimate or estimate_lake
    n = art.lake_filter.history_hours * 60
    calls = []
    for k in range(n - 1, len(d.raw), JITTER_MINUTES):
        rain = _rain(d, k, n)
        win = slice(k - n + 1, k + 1)
        t = _time(d, k)
        est = estimate(art, _floats(d.raw[win]), rain, inflow_cfs_by_minute=list(_inflow(d, k, n)),
                       control_elev_abs=float(d.control_elev_abs[k]), month=t.month)
        calls.append((None if est is None else est.level_ft, _anchor(d.smoothed[win]),
                      _dry(rain), d.truth[k]))
    # both estimators on the same pairs and calls: those where each has a value
    pairs = [(p, c) for p, c in zip(calls, calls[1:]) if None not in (p[0], p[1], c[0], c[1])]
    both = [c for c in calls if None not in (c[0], c[1]) and not math.isnan(c[3])]
    out = {}
    for name, i in (("filter", 0), ("today", 1)):
        jit = {True: [], False: []}
        for prev, cur in pairs:
            jit[cur[2]].append(cur[i] - prev[i])
        err = [abs(c[i] - c[3]) for c in both]
        out[name] = {"dry": _sd(jit[True]), "wet": _sd(jit[False]),
                     "p99": float(np.percentile(err, 99)) if err else None,
                     "pairs": (len(jit[True]), len(jit[False]))}
    return out


def _ramp(rate_ft_per_h: float, shape: str, minutes: int) -> np.ndarray:
    """The rise added at each minute after the onset (index 0 = the onset)."""
    i = np.arange(minutes)
    rate = rate_ft_per_h / 60 * np.where(i < RAMP_HOURS * 60, 1.0, 0.0)
    if shape == "gradual":
        rate = rate * np.minimum(i / (ONSET_HOURS * 60), 1.0)
    return np.concatenate([[0.0], np.cumsum(rate[:-1])])


def _u_rest(art: Artifact, level_stick: float, rain_in: float, control: float, month: int) -> float:
    """The filter's unscaled drive (P3): rain on the lake - spillway - evaporation, ft/min."""
    h = level_stick + art.datum.sensor_to_absolute_offset_ft
    return m5_lake_update(art, h, 0.0, rain_in, m6_spillway(art, h, control), DT_H, month)[0] - h


def onsets(art: Artifact, d: StudyData) -> list[int]:
    """One ramp every 15 h (so the onsets rotate through the times of day), once the first call
    has a full history; skipped where the truth is undefined at the onset or a check (a gauge gap)."""
    n = art.lake_filter.history_hours * 60
    last = len(d.raw) - 1 - max(ALL_CHECKS)
    return [k for k in range(n, last + 1, RAMP_EVERY_HOURS * 60)
            if not any(math.isnan(d.truth[k + m]) for m in (0, *ALL_CHECKS))]


def _ramp_inflow(art: Artifact, d: StudyData, k0: int, added: np.ndarray, lo: int,
                 rain_stop: int) -> np.ndarray:
    """The ramp's inflow (cfs) by the T1 recipe: what makes the model's rise match the ramp, less
    the storm's extra rain on the lake (until minute ``rain_stop``). An inflow can't be negative."""
    month = _time(d, k0).month
    base = float(d.truth[k0])
    q = np.zeros(len(added))
    for j in range(k0 - lo + 1, len(added)):
        level, c = base + added[j], float(d.control_elev_abs[lo + j])
        storm = STORM_RAIN_IN_PER_H / 60 if lo + j < rain_stop else 0.0
        extra = _u_rest(art, level, storm, c, month) - _u_rest(art, base, 0.0, c, month)
        area = surface_area_acres(art.geometry, level + art.datum.sensor_to_absolute_offset_ft)
        q[j] = max(0.0, units.acft_to_cfs((added[j] - added[j - 1] - extra) * area, DT_H))
    return q


def storm_table(art: Artifact, d: StudyData, estimate=None) -> dict:
    """{(rate, shape, case): {minute: (filter lag, today lag, filter behind, today behind,
    ramps used, filter missing, today missing)}}: means over the ramps where both estimators
    have a value, for a call ``minute`` after each onset. "Behind" is truth minus estimate; the
    checks are ``checks(shape)``; the gating "lag" is the storm's share of it: behind on the ramped series minus behind on the same
    series without the ramp, at the same minutes and with the storm's rain kept (so the wetness
    is the same). The lag cancels what an estimator does at that time of day anyway. A
    "rain_starts" ramp is used only at an onset with no measured rain in the DRY_SPLIT_HOURS before it."""
    estimate = estimate or estimate_lake
    n = art.lake_filter.history_hours * 60
    acc = {}
    for k0 in onsets(art, d):
        lo, hi = k0 + min(ALL_CHECKS) - n + 1, k0 + max(ALL_CHECKS) + 1
        month = _time(d, k0).month
        shapes = [s for s in SHAPES if s != "rain_starts" or _dry(_rain(d, k0, n))]
        # the storm's rain, minutes [start, stop), per shape
        rains = {shape: (k0 - RAIN_LEAD_HOURS[shape] * 60, k0 + RAIN_AFTER_ONSET_HOURS[shape] * 60)
                 for shape in shapes}
        ramps = {}
        for rate, shape in itertools.product(RAMP_RATES_FT_PER_H, shapes):
            added = np.zeros(hi - lo)
            added[k0 - lo:] = _ramp(rate, shape, hi - k0)
            ramps[rate, shape] = added, _ramp_inflow(art, d, k0, added, lo, rains[shape][1])
        for m in ALL_CHECKS:
            k = k0 + m
            win = slice(k - n + 1, k + 1)
            w0 = k - n + 1 - lo
            inflow = _inflow(d, k, n)
            for start, stop in sorted({rains[shape] for shape in shapes if m in checks(shape)}):
                rain = _rain(d, k, n)
                for i in range(n):            # the storm's rain, on top of the measured
                    if rain[i] is not None and start <= k - n + 1 + i < stop:
                        rain[i] += STORM_RAIN_IN_PER_H / 60

                def behind(truth, readings, smoothed, inflow):
                    est = estimate(art, _floats(readings), rain, inflow_cfs_by_minute=list(inflow),
                                   control_elev_abs=float(d.control_elev_abs[k]), month=month)
                    anchor = _anchor(smoothed)
                    return (math.nan if est is None else truth - est.level_ft,
                            math.nan if anchor is None else truth - anchor)

                calm = behind(d.truth[k], d.raw[win], d.smoothed[win], inflow)
                for (rate, shape), (added, ramp_q) in ramps.items():
                    if rains[shape] != (start, stop) or m not in checks(shape):
                        continue
                    a = added[w0:w0 + n]
                    for case, share in CASES.items():
                        filt, today = behind(d.truth[k] + added[k - lo], d.raw[win] + a, d.smoothed[win] + a,
                                             inflow + share * ramp_q[w0:w0 + n])
                        acc.setdefault((rate, shape, case), {}).setdefault(m, []).append(
                            (filt - calm[0], today - calm[1], filt, today))
    return {key: {m: _means(v) for m, v in by_m.items()} for key, by_m in acc.items()}


def _means(ramps: list[tuple]) -> tuple:
    both = np.array([r for r in ramps if not math.isnan(r[0]) and not math.isnan(r[1])]).reshape(-1, 4)
    means = tuple(float(x) for x in both.mean(axis=0)) if len(both) else (math.nan,) * 4
    return (*means, len(both), sum(math.isnan(r[0]) for r in ramps), sum(math.isnan(r[1]) for r in ramps))


def p12_margin(storms: dict) -> float:
    """The smallest slack under P12, on the storm-induced lag, over every synthetic case and
    check (negative = fails). A check with no common ramp, or with the filter missing on more
    than MAX_MISSING_SHARE of the ramps, is -inf."""
    def slack(case, m, row):
        filt, today, _, _, used, filt_missing, _ = row
        if not used or filt_missing > MAX_MISSING_SHARE * (used + filt_missing):
            return -math.inf
        return today + (P12_BLIND_EXTRA_FT.get(m, 0.0) if case == "blind" else 0.0) - filt
    return min(slack(case, m, row) for (_, _, case), by_m in storms.items() for m, row in by_m.items())


def skipped(storms: dict) -> tuple[int, int]:
    """(filter, today) ramp-checks without an estimate, over every case."""
    rows = [row for by_m in storms.values() for row in by_m.values()]
    return sum(r[5] for r in rows), sum(r[6] for r in rows)


def meets_p12(storms: dict) -> bool:
    """Every case of every shape has ramps (a "rain_starts" needs a dry spell) and P12 holds."""
    every = set(itertools.product(RAMP_RATES_FT_PER_H, SHAPES, CASES))
    return set(storms) == every and p12_margin(storms) >= 0.0


def with_values(art: Artifact, values: dict) -> Artifact:
    return art.model_copy(update={"lake_filter": art.lake_filter.model_copy(update=values)})


def run_grid(art: Artifact, d: StudyData, estimate=None) -> list[dict]:
    rows = []
    for combo in itertools.product(*GRID.values()):
        values = dict(zip(GRID, combo))
        a = with_values(art, values)
        storms = storm_table(a, d, estimate)
        rows.append({"values": values, "noise": noise_table(a, d, estimate), "storms": storms,
                     "margin": p12_margin(storms) if storms else None, "passes": meets_p12(storms)})
    return rows


def pick(rows: list[dict]) -> dict | None:
    """The passing combination with the lowest dry jitter (to 0.001 ft); ties (or no dry pairs)
    to the lower wet."""
    def key(r):
        dry, wet = r["noise"]["filter"]["dry"], r["noise"]["filter"]["wet"]
        return (math.inf if dry is None else round(dry, 3), math.inf if wet is None else wet)
    return min((r for r in rows if r["passes"]), key=key, default=None)


def write_values(path: str | Path, values: dict) -> None:
    """Rewrite the ``lake_filter`` values in place, keeping the file's layout and comments."""
    text = Path(path).read_text()
    head, sep, block = text.partition('"lake_filter": {')
    body, close, tail = block.partition("}")
    for key, v in values.items():
        num = (np.format_float_scientific(v, trim="-", exp_digits=1)    # the file's 1e-4 style
               if isinstance(v, float) and 0 < abs(v) < 1e-3 else f"{v:g}")
        body, count = re.subn(rf'("{key}":\s*)[^,\n]+', rf"\g<1>{num}", body)
        if count != 1:
            raise ValueError(f"lake_filter.{key} not found once in {path}")
    new = head + sep + body + close + tail
    with tempfile.TemporaryDirectory() as tmp:          # the gate: it must load before it replaces
        check = Path(tmp) / "artifact.json"
        check.write_text(new)
        load_artifact(check)
    atomic_write_text(path, new)


def find_storms(samples: list[archive.HourSample]) -> list[dict]:
    """Every hour where the settled level (the 24 h median after it, against the 24 h median
    before it) rose >= 0.10 ft with >= 0.5 in of rain in the 24 h around it; consecutive hours
    are one storm, reported at its largest rise."""
    by_hour = {datetime.fromisoformat(s.hour): s for s in samples}
    if not by_hour:
        return []
    hours = sorted(by_hour)
    span = timedelta(hours=SETTLE_HOURS)
    out: list[dict] = []
    h = hours[0] + span
    while h + span <= hours[-1] + timedelta(hours=1):
        def window(a, b):
            return [by_hour[a + timedelta(hours=i)] for i in range(int((b - a) / timedelta(hours=1)))
                    if a + timedelta(hours=i) in by_hour]
        before = [s.elev_ft for s in window(h - span, h) if s.elev_ft is not None]
        after = [s.elev_ft for s in window(h, h + span) if s.elev_ft is not None]
        rain = sum(s.rain_in or 0.0 for s in window(h - span / 2, h + span / 2))
        if len(before) >= SETTLE_HOURS / 2 and len(after) >= SETTLE_HOURS / 2:
            rise = statistics.median(after) - statistics.median(before)
            if rise >= STORM_RISE_FT and rain >= STORM_RAIN_IN:
                storm = {"hour": h, "rise_ft": rise, "rain_in": rain}
                if out and h - out[-1]["last"] <= timedelta(hours=1):
                    if rise > out[-1]["rise_ft"]:
                        out[-1].update(storm)
                    out[-1]["last"] = h
                else:
                    out.append({**storm, "last": h})
        h += timedelta(hours=1)
    return out


def storm_behind(art: Artifact, d: StudyData, hour: datetime, estimate=None
                 ) -> tuple[float, float] | None:
    """Mean (filter, today) behind the truth over the 24 h after ``hour``, called hourly with the
    replay's inflow; ``None`` when the minute archive doesn't cover it."""
    estimate = estimate or estimate_lake
    n = art.lake_filter.history_hours * 60
    k0 = int((hour - d.start).total_seconds() // 60)
    rows = []
    for k in range(k0, k0 + SETTLE_HOURS * 60, JITTER_MINUTES):
        if k - n + 1 < 0 or k >= len(d.raw) or math.isnan(d.truth[k]):
            continue
        win = slice(k - n + 1, k + 1)
        est = estimate(art, _floats(d.raw[win]), _rain(d, k, n),
                       inflow_cfs_by_minute=list(_inflow(d, k, n)),
                       control_elev_abs=float(d.control_elev_abs[k]), month=_time(d, k).month)
        today = _anchor(d.smoothed[win])
        if est is not None and today is not None:
            rows.append((d.truth[k] - est.level_ft, d.truth[k] - today))
    return tuple(float(np.mean(c)) for c in zip(*rows)) if rows else None
