"""Antecedent groundwater inference: recover S_agw from an observed rain-free recession.

The strongest check is a round-trip: run the real model forward with zero rain from a known
groundwater state, feed the resulting (hour, elev, rain=0) trajectory back into
``infer_s_agw``, and confirm it recovers the model's groundwater storage at the anchor. That
exercises the exact water-balance / recession-inversion the seeding relies on."""

from datetime import datetime, timedelta, timezone

from lake_rise import antecedent, model
from lake_rise.geometry import control_elev_for_stop_logs


def _recession(art, *, s_agw0, hours, control_elev, h0=339.9):
    """Drive the model forward with zero rain from a chosen groundwater state; return the
    observation tuples plus the model's own final-hour groundwater storage (the truth)."""
    state = model.initial_state(art, h0=h0, sm0=0.5, s_if0=0.0, s_agw0=s_agw0, month=7)
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    _, recs = model.run(art, state, [0.0] * hours, start=start, control_elev=control_elev)
    obs = [(r.t, r.h, 0.0) for r in recs]
    return obs, recs[-1].s_agw, recs[-1].t


def test_roundtrip_recovers_model_groundwater_state(art):
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    obs, truth_s_agw, anchor = _recession(art, s_agw0=0.30, hours=200, control_elev=control)

    seed = antecedent.infer_s_agw(art, obs, at=anchor, control_elev=control)
    assert seed is not None
    # Recover the store at the anchor to within ~8% (quadratic local-rate fit).
    assert abs(seed.s_agw_in - truth_s_agw) / truth_s_agw < 0.08
    assert seed.baseflow_cfs > 0.0
    assert seed.evidence["recession_hours"] >= 72


def test_roundtrip_across_seed_levels(art):
    """A higher antecedent store must invert to a higher inferred seed (monotone)."""
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    seeds = []
    for s0 in (0.15, 0.30, 0.55):
        obs, _truth, anchor = _recession(art, s_agw0=s0, hours=200, control_elev=control)
        seed = antecedent.infer_s_agw(art, obs, at=anchor, control_elev=control)
        assert seed is not None
        seeds.append(seed.s_agw_in)
    assert seeds[0] < seeds[1] < seeds[2]


def test_none_when_recession_too_short(art):
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    obs, _truth, anchor = _recession(art, s_agw0=0.30, hours=40, control_elev=control)
    assert antecedent.infer_s_agw(art, obs, at=anchor, control_elev=control) is None


def test_none_when_recent_rain_breaks_the_run(art):
    """Rain inside the trailing window truncates the rain-free run below the gate -> None
    (we must not read interflow rise as baseflow)."""
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    obs, _truth, anchor = _recession(art, s_agw0=0.30, hours=200, control_elev=control)
    # Drop 0.5 in of rain 24 h before the anchor: the clean run ending at the anchor is now
    # only ~24 h, below min_recession_hours.
    obs = [(t, e, (0.5 if (anchor - t).total_seconds() / 3600.0 == 24 else 0.0)) for t, e, _ in obs]
    assert antecedent.infer_s_agw(art, obs, at=anchor, control_elev=control) is None


def test_none_when_recession_is_noise_dominated(art):
    """A recession buried in gauge noise (poor fit) must not seed off noise -> None."""
    import random

    control = control_elev_for_stop_logs(art.stop_logs, 3)
    obs, _truth, anchor = _recession(art, s_agw0=0.30, hours=200, control_elev=control)
    rng = random.Random(0)
    noisy = [(t, e + rng.uniform(-0.08, 0.08), r) for t, e, r in obs]   # ~0.05 ft RMS jitter
    assert antecedent.infer_s_agw(art, noisy, at=anchor, control_elev=control) is None


def _synthetic_record(art, control, *, s_agw0, hours, rain=None, month=9, h0=339.75, sm0=0.5):
    """A model run turned into a ContinuousRecord (rain aligned so rain[i] drove hour i)."""
    from lake_rise.calibration.archive import ContinuousRecord, HourSample
    rain = rain if rain is not None else [0.0] * hours
    state = model.initial_state(art, h0=h0, sm0=sm0, s_if0=0.0, s_agw0=s_agw0, month=month)
    start = datetime(2026, month, 1, tzinfo=timezone.utc)
    _, recs = model.run(art, state, rain, start=start, control_elev=control)
    samples = [HourSample(hour=start.isoformat(), elev_ft=round(h0, 3), rain_in=rain[0])]
    for i, r in enumerate(recs):
        samples.append(HourSample(hour=r.t.isoformat(), elev_ft=round(r.h, 3),
                                  rain_in=(rain[i + 1] if i + 1 < len(rain) else 0.0)))
    return ContinuousRecord(samples=samples), recs


def test_estimate_state_recovers_groundwater_dry(art):
    """Assimilating a clean dry recession recovers the model's groundwater state to ~1%."""
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    rec, recs = _synthetic_record(art, control, s_agw0=0.22, hours=25 * 24, month=7, sm0=0.5)
    est = antecedent.estimate_state(art, rec, recs[-1].t, control)
    assert est is not None
    assert abs(est.state.s_agw - recs[-1].s_agw) / recs[-1].s_agw < 0.03


def test_estimate_state_recovers_groundwater_after_a_storm(art):
    """Even when a storm dominates the window (s_agw0 under-constrained), the *end* groundwater
    state is data-driven and recovered within a few percent -- unlike the seasonal seed."""
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    rain = [0.0] * (30 * 24)
    for i in range(48, 80):
        rain[i] = 0.15                                   # ~5 in storm early in the window
    rec, recs = _synthetic_record(art, control, s_agw0=0.30, hours=len(rain), rain=rain, month=6, sm0=2.0)
    est = antecedent.estimate_state(art, rec, recs[-1].t, control)
    assert est is not None
    assert abs(est.state.s_agw - recs[-1].s_agw) / recs[-1].s_agw < 0.08


def test_estimate_state_none_when_history_too_short(art):
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    rec, recs = _synthetic_record(art, control, s_agw0=0.22, hours=10 * 24, month=7, sm0=0.5)
    assert antecedent.estimate_state(art, rec, recs[-1].t, control) is None


def test_estimate_state_is_stable_across_window_lengths(art):
    """Growing the window past ~2-3 weeks barely moves the T0 estimate (< 0.01 in of storage,
    negligible for lake level) -- the justification for a bounded (not full-history) lookback."""
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    rec, recs = _synthetic_record(art, control, s_agw0=0.22, hours=40 * 24, month=7, sm0=0.5)
    at = recs[-1].t
    a = antecedent.estimate_state(art, rec, at, control, max_days=20)
    b = antecedent.estimate_state(art, rec, at, control, max_days=30)
    assert a is not None and b is not None
    assert abs(a.state.s_agw - b.state.s_agw) < 0.01     # absolute storage: what moves the lake


def test_backtest_state0_tracks_recession_where_seasonal_over_predicts(art):
    """End-to-end: over a clean dry recession, seeding the backtest from the assimilated state
    tracks the continued recession, where the (higher) seasonal spin-up drifts the forward run up.
    This is the dry-backtest drift the feature exists to fix."""
    from lake_rise import backtest
    from lake_rise.hourly import floor_hour

    control = control_elev_for_stop_logs(art.stop_logs, 3)
    rec, recs = _synthetic_record(art, control, s_agw0=0.08, hours=600, month=9, sm0=0.4, h0=339.75)
    level_by_hour = {r.t: r.h for r in recs}
    rain_hourly = [0.0] * len(recs)
    start = recs[0].t - timedelta(hours=1)
    t0, now = recs[450].t, recs[-1].t                    # >= 18 days of history before T0

    est = antecedent.estimate_state(art, rec, t0, control)
    assert est is not None

    def net_pred(state0):
        res = backtest.run_backtest(art, rain_hourly, floor_hour(start), level_by_hour,
                                    t0, now, control, state0=state0)
        p = [x["elevation"] for x in res["predicted"]]
        a = [x["elevation"] for x in res["actual"]]
        return p[-1] - p[0], a[-1] - a[0], res["gw_seed_source"]

    assimilated_net, actual_net, src = net_pred(est.state)
    seasonal_net, _, src2 = net_pred(None)               # seasonal spin-up fallback
    assert src == "assimilated" and src2 == "seasonal_default"
    assert actual_net < 0                                # the truth is a recession
    assert seasonal_net > assimilated_net                # seasonal seed drifts higher
    assert abs(assimilated_net - actual_net) < abs(seasonal_net - actual_net)


def test_none_on_empty_observations(art):
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    assert antecedent.infer_s_agw(art, [], at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                                  control_elev=control) is None


def test_gauge_gap_at_anchor_truncates_run(art):
    """A missing gauge reading (elev None) at the anchor ends the run -> None when what's
    left is too short."""
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    obs, _truth, anchor = _recession(art, s_agw0=0.30, hours=200, control_elev=control)
    obs = [(t, (None if (anchor - t).total_seconds() / 3600.0 < 30 else e), r) for t, e, r in obs]
    # Only <30 h of gap-free data remains contiguous at the anchor... actually the gap is at
    # the anchor end, so the run ending at the anchor breaks immediately -> None.
    assert antecedent.infer_s_agw(art, obs, at=anchor, control_elev=control) is None
