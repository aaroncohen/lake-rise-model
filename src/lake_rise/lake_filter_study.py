"""The lake filter's tuning study (docs/plans/lake-filter.md, T3; ``lake-rise lake-filter-study``).

On the minute archive's real gauge noise it compares the filter with today's anchor (the 30-min
median of ``lake_sensor``): hour-to-hour jitter, dry and wet, and the 99th-percentile error
against a "truth" that is the centred 4 h median of the raw depth (not independent of the gauge).
Synthetic storm ramps added to the real readings check P12. Every estimate is a stateless call
over the trailing ``history_hours``, as live, so it is evaluated only at sampled minutes."""

from __future__ import annotations

import bisect
import itertools
import math
import re
import statistics
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
from .lake_filter import DT_H, estimate_lake, minute_grid, rain_by_minute, wetness
from .model import m5_lake_update, m6_spillway

MIN_DAYS = 7
TRUTH_HOURS = 4                          # the centred median that stands in for the truth
ANCHOR_MINUTES = 30                      # today's anchor: the 30-min median of lake_sensor
JITTER_MINUTES = 60                      # hour-to-hour
RAMP_RATES_FT_PER_H = (0.05, 0.1)
RAMP_HOURS = 6
ONSET_HOURS = 2                          # the gradual onset reaches full rate over 2 h
STORM_RAIN_IN_PER_H = 0.25
RAIN_LEAD_HOURS = 2                      # the storm's rain starts 2 h before the ramp
RAMP_EVERY_HOURS = 15                    # not 24: onsets rotate through 8 times of day
CASES = {"right": 1.0, "half": 0.5, "blind": 0.0}   # share of the ramp's inflow the model sees
SHAPES = ("abrupt", "gradual")
CHECK_MINUTES = (30, 60, 120)
P12_BLIND_EXTRA_FT = {30: 0.03, 60: 0.03, 120: 0.0}  # P12; right and half: never further behind
GRID = {"rate_sd_wet_ft_per_min": (3e-5, 5e-5, 1e-4, 2e-4),
        "wet_window_hours": (3, 6, 12),
        "offset_minutes": (15, 20, 30)}
STORM_RISE_FT = 0.10                     # step 4: a settled 24 h rise ...
STORM_RAIN_IN = 0.5                      # ... with this much rain
SETTLE_HOURS = 24


@dataclass
class StudyData:
    """The minute archive on one grid (element k is minute ``start + k``), with the replay's
    inflow and the stop-log control per minute."""
    start: datetime
    raw: np.ndarray                      # lake_raw_sensor, stick ft (nan = missing)
    smoothed: np.ndarray                 # lake_sensor, stick ft
    truth: np.ndarray                    # centred 4 h median of raw
    rain_states: list[tuple[datetime, float | None]]   # the daily accumulator
    hourly_rain: list[float | None]      # per clock hour from floor_hour(start)
    inflow_cfs: np.ndarray               # the pure replay's watershed inflow
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
    if not raw_rows or raw_rows[-1][0] - raw_rows[0][0] < timedelta(days=MIN_DAYS):
        span = (raw_rows[-1][0] - raw_rows[0][0]) if raw_rows else timedelta(0)
        raise ValueError(f"{span} of minute readings in {d}; the study needs {MIN_DAYS} days")
    end = raw_rows[-1][0]
    n = int((end - raw_rows[0][0]).total_seconds() // 60) + 1
    start = end.replace(second=0, microsecond=0) - timedelta(minutes=n - 1)
    raw = _nan(minute_grid(raw_rows, end, n))
    smoothed = _nan(minute_grid(rows.get(cfg.lake_sensor, []), end, n))
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
    inflows = replay[1]
    times = [start + timedelta(minutes=k) for k in range(n)]
    back = [int((as_of - floor_hour(t)).total_seconds() // 3600) for t in times]
    # minutes after the replay's last hour take its value (persistence, as live)
    inflow = np.array([inflows[max(0, len(inflows) - max(b, 1))] for b in back])

    logs = minute_grid(rows.get(cfg.stop_log_helper, []), end, n) if cfg.stop_log_helper else [None] * n
    control = np.array([art.stop_logs.control_elev(
        int(c) if c is not None else default_stop_log_count(art.stop_logs, t.month, t.day))
        for c, t in zip(logs, times)])
    return StudyData(start, raw, smoothed, truth, rain_states, hourly_rain, inflow, control)


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


def _floats(a: np.ndarray) -> list[float | None]:
    return [None if math.isnan(v) else float(v) for v in a]


def _anchor(smoothed: np.ndarray) -> float | None:
    w = smoothed[-ANCHOR_MINUTES:]
    return None if np.isnan(w).all() else float(np.nanmedian(w))


def _sd(x: list[float]) -> float | None:
    return float(np.std(x)) if len(x) > 1 else None


def noise_table(art: Artifact, d: StudyData, estimate=None) -> dict:
    """Hour-to-hour jitter sd of the filter and today's anchor, split by the filter's wetness at
    the later call, and each one's 99th-percentile |error| against the truth."""
    estimate = estimate or estimate_lake
    n = art.lake_filter.history_hours * 60
    calls = []
    for k in range(n - 1, len(d.raw), JITTER_MINUTES):
        rain = _rain(d, k, n)
        win = slice(k - n + 1, k + 1)
        t = _time(d, k)
        est = estimate(art, _floats(d.raw[win]), rain, inflow_cfs_by_minute=list(d.inflow_cfs[win]),
                       control_elev_abs=float(d.control_elev_abs[k]), month=t.month)
        calls.append((None if est is None else est.level_ft, _anchor(d.smoothed[win]),
                      wetness(art.lake_filter, rain)[-1] == 0, d.truth[k]))
    out = {}
    for name, i in (("filter", 0), ("today", 1)):
        jit = {True: [], False: []}
        for prev, cur in zip(calls, calls[1:]):
            if prev[i] is not None and cur[i] is not None:
                jit[cur[2]].append(cur[i] - prev[i])
        err = [abs(c[i] - c[3]) for c in calls if c[i] is not None and not math.isnan(c[3])]
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
    last = len(d.raw) - 1 - max(CHECK_MINUTES)
    return [k for k in range(n, last + 1, RAMP_EVERY_HOURS * 60)
            if not any(math.isnan(d.truth[k + m]) for m in (0, *CHECK_MINUTES))]


def _ramp_inflow(art: Artifact, d: StudyData, k0: int, added: np.ndarray, lo: int) -> np.ndarray:
    """The ramp's inflow (cfs) by the T1 recipe: what makes the model's rise match the ramp, less
    the storm's extra rain on the lake. An inflow can't be negative."""
    month = _time(d, k0).month
    base = float(d.truth[k0])
    q = np.zeros(len(added))
    for j in range(k0 - lo + 1, len(added)):
        level, c = base + added[j], float(d.control_elev_abs[lo + j])
        extra = _u_rest(art, level, STORM_RAIN_IN_PER_H / 60, c, month) - _u_rest(art, base, 0.0, c, month)
        area = surface_area_acres(art.geometry, level + art.datum.sensor_to_absolute_offset_ft)
        q[j] = max(0.0, units.acft_to_cfs((added[j] - added[j - 1] - extra) * area, DT_H))
    return q


def storm_table(art: Artifact, d: StudyData, estimate=None) -> dict:
    """{(rate, shape, case): {minute: (filter lag, today lag, filter behind, today behind)}}, each
    the mean over the ramps for a call ``minute`` after each onset. "Behind" is truth minus
    estimate; the gating "lag" is the storm's share of it: behind on the ramped series minus
    behind on the same series without the ramp (the storm's rain kept, so the wetness is the
    same). The lag cancels what an estimator does at that time of day anyway."""
    estimate = estimate or estimate_lake
    n = art.lake_filter.history_hours * 60
    acc = {}
    for k0 in onsets(art, d):
        lo, hi = k0 + min(CHECK_MINUTES) - n + 1, k0 + max(CHECK_MINUTES) + 1
        month = _time(d, k0).month
        ramps = {}
        for rate, shape in itertools.product(RAMP_RATES_FT_PER_H, SHAPES):
            added = np.zeros(hi - lo)
            added[k0 - lo:] = _ramp(rate, shape, hi - k0)
            ramps[rate, shape] = added, _ramp_inflow(art, d, k0, added, lo)
        for m in CHECK_MINUTES:
            k = k0 + m
            win = slice(k - n + 1, k + 1)
            w0 = k - n + 1 - lo
            rain = _rain(d, k, n)
            for i in range(n):                # the storm's rain, on top of the measured
                if rain[i] is not None and k0 - RAIN_LEAD_HOURS * 60 <= k - n + 1 + i:
                    rain[i] += STORM_RAIN_IN_PER_H / 60

            def behind(truth, readings, smoothed, inflow):
                est = estimate(art, _floats(readings), rain, inflow_cfs_by_minute=list(inflow),
                               control_elev_abs=float(d.control_elev_abs[k]), month=month)
                return (math.nan if est is None else truth - est.level_ft), truth - _anchor(smoothed)

            calm = behind(d.truth[k], d.raw[win], d.smoothed[win], d.inflow_cfs[win])
            for (rate, shape), (added, ramp_q) in ramps.items():
                a = added[w0:w0 + n]
                for case, share in CASES.items():
                    filt, today = behind(d.truth[k] + added[k - lo], d.raw[win] + a,
                                         d.smoothed[win] + a, d.inflow_cfs[win] + share * ramp_q[w0:w0 + n])
                    acc.setdefault((rate, shape, case), {}).setdefault(m, []).append(
                        (filt - calm[0], today - calm[1], filt, today))
    return {key: {m: tuple(float(np.nanmean(col)) for col in zip(*v)) for m, v in by_m.items()}
            for key, by_m in acc.items()}


def p12_margin(storms: dict) -> float:
    """The smallest slack under P12, on the storm-induced lag, over every synthetic case and
    check (negative = fails)."""
    return min((today + (P12_BLIND_EXTRA_FT[m] if case == "blind" else 0.0)) - filt
               for (_, _, case), by_m in storms.items() for m, (filt, today, *_) in by_m.items())


def meets_p12(storms: dict) -> bool:
    return bool(storms) and p12_margin(storms) >= 0.0


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
    """The passing combination with the lowest dry jitter (to 0.001 ft); ties to the lower wet."""
    ok = [r for r in rows if r["passes"]]
    return min(ok, key=lambda r: (round(r["noise"]["filter"]["dry"], 3), r["noise"]["filter"]["wet"]),
               default=None)


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
    atomic_write_text(path, head + sep + body + close + tail)
    load_artifact(path)                  # the gate: it must still load


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
                       inflow_cfs_by_minute=list(d.inflow_cfs[win]),
                       control_elev_abs=float(d.control_elev_abs[k]), month=_time(d, k).month)
        today = _anchor(d.smoothed[win])
        if est is not None and today is not None:
            rows.append((d.truth[k] - est.level_ft, d.truth[k] - today))
    return tuple(float(np.mean(c)) for c in zip(*rows)) if rows else None
