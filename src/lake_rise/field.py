"""The field view's summary: a pure assembly of a prediction, its bundle, the alert decision
and the gauge facts into the page's JSON. Everything an operator sees is in stick height
(absolute - ``datum.sensor_to_absolute_offset_ft``); chances come from the prediction, so the
page and the emails agree."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel

from .alerting.eap import EAP_LEVELS, active_eap_rank, eap_level
from .alerting.render import preventative_actions
from .alerting.rules import AlertDecision
from .artifact import Artifact
from .bundle import InputBundle
from .observed import GaugeObservation
from .predict import PredictionResult, scenario_rain
from .scenarios import confidence_label

# Under this magnitude (ft/h) the lake reads "steady".
_STEADY_FT_PER_H = 0.03
# A trend this much faster or slower (ft/h) than an hour ago reads "faster" / "slower".
_TREND_CHANGE_FT_PER_H = Decimal("0.02")
# The middle peak must move this much (stick ft) to count as a change.
_MIDDLE_MOVE_FT = Decimal("0.05")
# Preventative actions show from this dam-overtop chance (%), as in the emails.
_ACTIONS_OVERTOP_PCT = 10
_RAIN_BLOCK_HOURS = 6
_RAIN_DAY_HOURS = 24
_TRAILING_RAIN_HOURS = 24
# The details' factor bars, in display order.
_FACTOR_LABELS = {"watershed_runoff": "Surface runoff", "direct_rain": "Rain on the lake",
                  "baseflow": "Groundwater seepage", "evaporation": "Evaporation",
                  "spillway": "Out the spillways"}
_CASE_SCENARIO = (("dry", "low"), ("middle", "median"), ("wet", "high"))
# The model level each alert-ladder threshold label counts at.
_LEVEL_FOR_THRESHOLD = {"early_warning": "early_warning", "dam_crest": "overtop",
                        "bridge_deck": "bridge"}


class FieldLevel(BaseModel):
    key: Literal["early_warning", "eap_plastic", "overtop", "bridge"]
    label: str                       # "Warning" | "Plastic" | "Overtop" | "Bridge"
    stick_ft: float
    band_top_ft: float | None        # 3.90 / 4.40 for the overtopping bands, else None
    pct: int | None                  # chance of reaching it, 0-100 (None: no forecast, or Plastic in fallback)
    reached: bool                    # the lake is already at or above it (absolute frame)


class FieldStatus(BaseModel):
    level: str | None                # ladder name ("WATCH") or None at rank 0
    rank: int                        # ladder rank, 0 = normal
    headline: str | None             # the active level's threshold and its chance ("Overtop 22%")
    observed_eap: dict | None        # {"title": str, "gauge_ft": float, "color": str}


class FieldLake(BaseModel):
    now_ft: float                    # the forecast's anchor
    trend_ft_per_h: float | None
    trend: Literal["rising", "falling", "steady"] | None
    problem: str | None              # the gauge problem text when stale/invalid


class FieldTimeline(BaseModel):
    start: datetime                  # bundle.as_of; middle_ft[i] is at start + (i+1) h
    middle_ft: list[float]           # hourly, len == horizon_hours
    bands: dict[str, list[float]]    # ensemble: "10","25","75","90"; fallback: "low","high"


class FieldChange(BaseModel):
    since: datetime                  # the previous record's generated_at
    middle_was_ft: float             # stick, display-rounded
    pct_was: dict[str, int | None]   # only the level keys whose pct differs
    worse: bool                      # the middle rose >= 0.05 ft or any pct rose


class FieldAction(BaseModel):
    id: str                          # "pull_logs" | "logs_out" | "inspect" | "monitor" | "eap<rank>-<i>"
    short: str


class FieldNextLevel(BaseModel):
    title: str                       # EAPLevel.title
    gauge_ft: float
    actions: list[FieldAction]


class FieldStopLogOption(BaseModel):
    logs_in: int
    dry_ft: float
    middle_ft: float
    wet_ft: float
    pct: dict[str, int | None]       # level_pcts for this option


class FieldStopLogs(BaseModel):
    logs_in: int
    is_default: bool
    options: list[FieldStopLogOption]   # [0] is "leave them" (current count), then fewer


class FieldFactor(BaseModel):
    key: str                         # watershed_runoff | direct_rain | baseflow | evaporation | spillway
    label: str
    ft: float                        # signed delta, 2 dp (not stick height)


class FieldRainDay(BaseModel):
    start: datetime
    dry_in: float
    middle_in: float
    wet_in: float


class FieldCaseTimes(BaseModel):
    name: Literal["dry", "middle", "wet"]
    overtop_at: datetime | None
    overtop_reached: bool            # already over at the start
    bridge_at: datetime | None
    bridge_reached: bool


class FieldDetails(BaseModel):
    factors: list[FieldFactor]       # [] when mode == "no_forecast"
    net_ft: float | None             # None when no_forecast
    factors_window: Literal["to peak", "3 days"] | None
    rain_days: list[FieldRainDay]    # [] when no_forecast
    cases: list[FieldCaseTimes]      # [] when no_forecast
    gauge_age_min: int | None
    rain_gauge_24h_in: float
    stop_log_count: int
    stop_log_default: bool
    rain_source: Literal["ensemble", "point_forecast"] | None   # None iff no_forecast
    agreement_pct: int | None
    agreement_label: str | None      # "High" | "Medium" | "Low"
    agreement_tol_ft: float | None
    soil_pct: int                    # 0-100, clamped


class FieldSummary(BaseModel):
    computed_at: datetime            # request time, UTC, whole seconds
    mode: Literal["ensemble", "fallback", "no_forecast"]
    status: FieldStatus
    lake: FieldLake
    levels: list[FieldLevel]         # 4 (3 without a bridge deck), ordered low to high
    peaks_ft: list[float]            # sorted; empty unless ensemble
    peak_levels: list[str | None]    # parallel to peaks_ft: highest level key reached
    cases: dict[str, float] | None   # {"dry","middle","wet"} = low/median/high peaks
    timeline: FieldTimeline | None
    rain_6h: dict[str, list[float]] | None   # {"middle","wet"} = median/high rain, in per 6 h
    ensemble_cycle: datetime | None
    stale_since: datetime | None = None      # set by /field when the forecast part is a previous result
    unavailable: str | None = None           # banner text in that case
    change: FieldChange | None = None
    trend_change: Literal["faster", "slower"] | None = None
    actions: list[FieldAction] = []
    next_level: FieldNextLevel | None = None
    stop_logs: FieldStopLogs | None = None
    details: FieldDetails | None = None      # summarize always sets it


def _round(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def level_pcts(result: PredictionResult, bundle: InputBundle, art: Artifact) -> dict[str, int | None]:
    """Chance (whole %) the forecast reaches each field level, keyed like ``FieldLevel.key``
    (no "bridge" when the artifact has no bridge deck). All None when there is no forecast
    rain (``no_forecast`` mode)."""
    keys = ("early_warning", "eap_plastic", "overtop")
    if art.thresholds_abs_ft.bridge_deck is not None:
        keys += ("bridge",)
    peaks = result.ensemble_peaks_ft or []
    if not peaks and not any(s.hourly_in for s in bundle.forecast_scenarios):
        return dict.fromkeys(keys)
    prob = {tp.label: tp.p_cross_within_horizon for tp in result.threshold_probabilities}
    plastic_abs = eap_level(1).gauge_ft + art.datum.sensor_to_absolute_offset_ft
    chance = {
        "early_warning": prob["early_warning"],
        "eap_plastic": sum(p >= plastic_abs for p in peaks) / len(peaks) if peaks else None,
        "overtop": prob["dam_crest"],
        "bridge": prob.get("bridge_deck"),
    }
    return {k: round(100 * chance[k]) if chance[k] is not None else None for k in keys}


def _moved(a: float, b: float) -> Decimal:
    return Decimal(str(a)) - Decimal(str(b))


def _trend_change(now: float | None, was: float | None) -> str | None:
    """Faster or slower than an hour ago by 0.02 ft/h, moving the same way; else None. A lake
    that reads "steady" now has no trend change."""
    if now is None or was is None or now * was <= 0 or abs(now) < _STEADY_FT_PER_H:
        return None
    d = _moved(abs(now), abs(was))
    if d >= _TREND_CHANGE_FT_PER_H:
        return "faster"
    return "slower" if d <= -_TREND_CHANGE_FT_PER_H else None


def _same_or_later(previous: dict, bundle: InputBundle) -> bool:
    """True when the record is for this forecast hour (a record's generated_at is floored, so at
    :50-:59 previous() can return the current forecast's own record)."""
    as_of = previous.get("as_of")
    return as_of is not None and datetime.fromisoformat(as_of) >= bundle.as_of


def _details(result: PredictionResult, bundle: InputBundle, art: Artifact, *, forecast: bool,
             rise_ft: float | None, gauge_age_min: int | None) -> FieldDetails:
    """F9's data: factor deltas (ft, never stick heights), rain by day, case times, data rows.
    ``rise_ft`` is the displayed middle peak minus the displayed now."""
    factors, net_ft, window, rain_days, case_times = [], None, None, [], []
    rain_source = agreement_label = agreement_tol = None
    if forecast:
        by_name = {s.name: s for s in result.scenarios}
        f = result.factors
        elev = [p.elevation for p in by_name["median"].trajectory]
        i_peak = max(range(len(elev)), key=elev.__getitem__) if elev else 0
        if i_peak > 0 and elev[i_peak] > result.current_elevation:
            window, values = "to peak", {k: f["cumulative_ft"][k][i_peak] for k in _FACTOR_LABELS}
            # net_cumulative_ft[i_peak] is peak - now; take it from the rounded ends shown beside it.
            net_ft = rise_ft
        else:
            window, values, net_ft = "3 days", f["totals_ft"], _round(f["totals_ft"]["net"])
        # + 0.0 turns a rounded -0.0 into 0.0, so a nil bar has no sign.
        factors = [FieldFactor(key=k, label=label, ft=_round(values[k]) + 0.0)
                   for k, label in _FACTOR_LABELS.items()]

        rain = {s.name: s.hourly_in for s in scenario_rain(bundle, result)}
        rain_days = [FieldRainDay(
            start=bundle.as_of + timedelta(hours=i),
            **{f"{case}_in": round(sum(rain[name][i:i + _RAIN_DAY_HOURS]), 2)
               for case, name in _CASE_SCENARIO})
            for i in range(0, len(rain["median"]), _RAIN_DAY_HOURS)]

        def at(hours: float | None) -> datetime | None:
            return bundle.as_of + timedelta(hours=hours) if hours else None

        for case, name in _CASE_SCENARIO:
            sc = by_name[name]
            case_times.append(FieldCaseTimes(
                name=case, overtop_at=at(sc.hours_to_crest), overtop_reached=sc.hours_to_crest == 0,
                bridge_at=at(sc.hours_to_bridge_deck),
                bridge_reached=sc.hours_to_bridge_deck == 0))

        rain_source = result.input_summary["rain_source"]
        if result.ensemble_agreement_pct is not None:
            agreement_label = confidence_label(result.ensemble_agreement_pct)
            agreement_tol = art.ensemble_rain.agreement_tolerance_ft if art.ensemble_rain else 0.0

    return FieldDetails(
        factors=factors, net_ft=net_ft, factors_window=window, rain_days=rain_days,
        cases=case_times, gauge_age_min=gauge_age_min,
        rain_gauge_24h_in=round(sum(bundle.trailing_rainfall_in[-_TRAILING_RAIN_HOURS:]), 2),
        stop_log_count=bundle.stop_log_count, stop_log_default=bundle.stop_log_count_is_default,
        rain_source=rain_source, agreement_pct=result.ensemble_agreement_pct,
        agreement_label=agreement_label, agreement_tol_ft=agreement_tol,
        soil_pct=min(100, round(100 * result.state_sm_in / art.hspf.LZSN_in)))


def summarize(result: PredictionResult, bundle: InputBundle, decision: AlertDecision,
              art: Artifact, *, observation: GaugeObservation | None,
              trend_ft_per_h: float | None, gauge_problem: str | None,
              computed_at: datetime, previous: dict | None = None,
              trend_was_ft_per_h: float | None = None,
              options: list[tuple[int, PredictionResult]] = (),
              gauge_age_min: int | None = None) -> FieldSummary:
    """``previous`` is the forecast-log record from ``forecast_log.previous``; ``options`` are
    ``(logs_in, prediction)`` for each stop-log count below the current one."""
    off = art.datum.sensor_to_absolute_offset_ft

    def stick(abs_ft: float) -> float:
        # Subtract in Decimal: 342.2 - 338.375 is 3.82499... in floats, which would round to 3.82.
        return _round(float(Decimal(str(abs_ft)) - Decimal(str(off))))

    if result.ensemble_peaks_ft:
        mode = "ensemble"
    elif any(s.hourly_in for s in bundle.forecast_scenarios):
        mode = "fallback"
    else:
        mode = "no_forecast"
    forecast = mode != "no_forecast"
    pcts = level_pcts(result, bundle, art)
    peaks = result.ensemble_peaks_ft or []

    th = art.thresholds_abs_ft
    plastic, overtop_edge, bridge_edge = eap_level(1), eap_level(2), eap_level(3)
    plastic_abs = plastic.gauge_ft + off
    # (key, label, counted at (absolute), band top (stick))
    rows = [
        ("early_warning", "Warning", th.early_warning, None),
        ("eap_plastic", "Plastic", plastic_abs, None),
        ("overtop", "Overtop", th.dam_crest, overtop_edge.gauge_ft),
        ("bridge", "Bridge", th.bridge_deck, bridge_edge.gauge_ft),
    ]
    if th.bridge_deck is None:   # optional in the artifact: no bridge level then
        rows.pop()
    levels = [
        FieldLevel(key=key, label=label, stick_ft=stick(at),
                   band_top_ft=_round(top) if top is not None else None,
                   pct=pcts[key],
                   reached=result.current_elevation >= at)
        for key, label, at, top in rows
    ]
    by_key = {lv.key: lv for lv in levels}

    def highest_reached(peak: float) -> str | None:
        return next((key for key, _, at, _ in reversed(rows) if peak >= at), None)

    headline = None
    if decision.active_threshold_label is not None:
        lv = by_key[_LEVEL_FOR_THRESHOLD[decision.active_threshold_label]]
        headline = f"{lv.label} {lv.pct}%" if lv.pct is not None else lv.label

    observed_eap = None
    observed_rank = 0
    if observation is not None and observation.confirmed and observation.gauge_ft is not None:
        observed_rank = active_eap_rank(observation.gauge_ft)
        if observed_rank > 0:
            seen = eap_level(observed_rank)
            observed_eap = {"title": seen.title, "gauge_ft": _round(observation.gauge_ft),
                            "color": seen.color}

    trend = None
    if trend_ft_per_h is not None:
        trend = ("steady" if abs(trend_ft_per_h) < _STEADY_FT_PER_H
                 else "rising" if trend_ft_per_h > 0 else "falling")

    def case_peaks(r: PredictionResult) -> dict[str, float]:
        by_name = {s.name: s for s in r.scenarios}
        return {case: stick(by_name[name].peak_elevation)
                for case, name in (("dry", "low"), ("middle", "median"), ("wet", "high"))}

    cases = timeline = rain_6h = None
    if forecast:
        by_name = {s.name: s for s in result.scenarios}
        cases = case_peaks(result)

        def path(name: str) -> list[float]:
            return [stick(p.elevation) for p in by_name[name].trajectory]

        if mode == "ensemble":
            bands = {k: [stick(e) for e in result.elevation_bands_ft[k]]
                     for k in ("10", "25", "75", "90")}
        else:
            bands = {"low": path("low"), "high": path("high")}
        timeline = FieldTimeline(start=bundle.as_of, middle_ft=path("median"), bands=bands)

        rain = {s.name: s.hourly_in for s in scenario_rain(bundle, result)}
        rain_6h = {case: [round(sum(rain[name][i:i + _RAIN_BLOCK_HOURS]), 2)
                          for i in range(0, len(rain[name]), _RAIN_BLOCK_HOURS)]
                   for case, name in (("middle", "median"), ("wet", "high"))}

    now_ft = stick(result.current_elevation)
    normal = (decision.active_rank == 0 and observed_eap is None
              and all(not p for p in pcts.values()))

    change = None
    if (previous is not None and cases is not None and not normal
            and not _same_or_later(previous, bundle)
            and previous.get("levels_pct") is not None
            and previous.get("stop_log_count") == bundle.stop_log_count
            and previous.get("rain_source") == result.input_summary.get("rain_source")):
        was_ft = stick(previous["middle_peak_ft"])
        moved = _moved(cases["middle"], was_ft)
        pct_was = {k: previous["levels_pct"].get(k) for k in pcts
                   if previous["levels_pct"].get(k) != pcts[k]}
        if abs(moved) >= _MIDDLE_MOVE_FT or pct_was:
            change = FieldChange(
                since=datetime.fromisoformat(previous["generated_at"]), middle_was_ft=was_ft,
                pct_was=pct_was,
                worse=moved >= _MIDDLE_MOVE_FT or any((pcts[k] or 0) > (was or 0)
                                                     for k, was in pct_was.items()))

    # Actions follow the emails' rule for preventative measures, in stick ft.
    eap_active = observed_eap is not None or any(now_ft >= lv.gauge_ft for lv in EAP_LEVELS)
    # An observed level is reached even when the model's now is below it.
    reachable = ([lv for lv in EAP_LEVELS
                  if lv.rank > observed_rank and now_ft < lv.gauge_ft <= cases["wet"]]
                 if cases is not None else [])
    eap_likely = any(lv.gauge_ft <= cases["middle"] for lv in reachable)
    overtop_pct = pcts["overtop"] or 0
    actions: list[FieldAction] = []
    if eap_active or reachable or overtop_pct >= _ACTIONS_OVERTOP_PCT:
        actions = [FieldAction(id=i, short=short) for i, _, short in preventative_actions(
            stop_logs=bundle.stop_log_count, eap_active=eap_active, eap_likely=eap_likely,
            p_crest_pct=overtop_pct)]
    next_level = None
    if reachable:
        lv = reachable[0]
        next_level = FieldNextLevel(
            title=lv.title, gauge_ft=lv.gauge_ft,
            actions=[FieldAction(id=f"eap{lv.rank}-{i}", short=short)
                     for i, short in enumerate(lv.short_actions)])

    stop_logs = None
    if cases is not None and options and not normal:   # a quiet day shows no stop-log options
        def option(k: int, r: PredictionResult) -> FieldStopLogOption:
            c = case_peaks(r)
            return FieldStopLogOption(logs_in=k, dry_ft=c["dry"], middle_ft=c["middle"],
                                      wet_ft=c["wet"], pct=level_pcts(r, bundle, art))

        stop_options = [option(k, r) for k, r in [(bundle.stop_log_count, result), *options]]
        if any(o.pct != pcts or abs(_moved(o.middle_ft, cases["middle"])) >= _MIDDLE_MOVE_FT
               for o in stop_options[1:]):
            stop_logs = FieldStopLogs(logs_in=bundle.stop_log_count,
                                      is_default=bundle.stop_log_count_is_default,
                                      options=stop_options)

    return FieldSummary(
        computed_at=computed_at,
        mode=mode,
        status=FieldStatus(level=decision.active_level_name, rank=decision.active_rank,
                           headline=headline, observed_eap=observed_eap),
        lake=FieldLake(now_ft=now_ft, trend_ft_per_h=trend_ft_per_h,
                       trend=trend, problem=gauge_problem),
        levels=levels,
        peaks_ft=[stick(p) for p in peaks],
        peak_levels=[highest_reached(p) for p in peaks],
        cases=cases,
        timeline=timeline,
        rain_6h=rain_6h,
        ensemble_cycle=bundle.ensemble_cycle if forecast else None,
        change=change,
        trend_change=_trend_change(trend_ft_per_h, trend_was_ft_per_h),
        actions=actions,
        next_level=next_level,
        stop_logs=stop_logs,
        details=_details(result, bundle, art, forecast=forecast,
                         rise_ft=float(_moved(cases["middle"], now_ft)) if forecast else None,
                         gauge_age_min=gauge_age_min),
    )
