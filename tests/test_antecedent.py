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


def test_estimate_state_recovers_end_state_after_the_drainage_window(art):
    """Over a > 5-half-life record with periodic storms, the seasonal seed has drained and the
    assimilated T0 groundwater tracks the model's true end state within ~15%."""
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    hours = 130 * 24
    rain = [0.0] * hours
    for d0 in range(6, 130, 11):                         # a storm every ~11 days
        for h in range(d0 * 24, d0 * 24 + 16):
            rain[h] = 0.10
    rec, recs = _synthetic_record(art, control, s_agw0=0.5, hours=hours, rain=rain, month=3, sm0=1.5)
    est = antecedent.estimate_state(art, rec, recs[-1].t, control)
    assert est is not None
    assert abs(est.state.s_agw - recs[-1].s_agw) / recs[-1].s_agw < 0.15


def test_estimate_state_engages_only_past_the_drainage_window(art):
    """< 5 half-lives of usable history -> the seasonal seed isn't drained -> None (seasonal
    fallback); past it the estimator engages. The window length is the minimum archive needed.
    Dry summer (month 7, low seasonal SM) so a rainless window is realistic and drains cleanly."""
    control = control_elev_for_stop_logs(art.stop_logs, 3)
    short, srecs = _synthetic_record(art, control, s_agw0=0.22, hours=100 * 24, month=7, sm0=0.5)
    assert antecedent.estimate_state(art, short, srecs[-1].t, control) is None      # ~100 d < 114 d
    long, lrecs = _synthetic_record(art, control, s_agw0=0.22, hours=130 * 24, month=7, sm0=0.5)
    assert antecedent.estimate_state(art, long, lrecs[-1].t, control) is not None    # >= 5 half-lives


def test_backtest_state0_lowers_forecast_vs_undrained_seasonal_seed(art):
    """End-to-end: after a long dry stretch the true groundwater is nearly drained, so seeding the
    backtest from the assimilated state tracks the (flat) gauge, where an undrained seasonal seed
    drifts the forward run up. This is the dry-backtest drift the feature exists to fix."""
    from lake_rise import backtest
    from lake_rise.hourly import floor_hour

    control = control_elev_for_stop_logs(art.stop_logs, 3)
    rec, recs = _synthetic_record(art, control, s_agw0=0.06, hours=150 * 24, month=7, sm0=0.5, h0=339.75)
    level_by_hour = {r.t: r.h for r in recs}
    rain_hourly = [0.0] * len(recs)
    start = recs[0].t - timedelta(hours=1)
    t0, now = recs[130 * 24].t, recs[-1].t               # >= 130 d of history before T0

    est = antecedent.estimate_state(art, rec, t0, control)
    assert est is not None

    def net_pred(state0):
        res = backtest.run_backtest(art, rain_hourly, floor_hour(start), level_by_hour,
                                    t0, now, control, state0=state0)
        p = [x["elevation"] for x in res["predicted"]]
        a = [x["elevation"] for x in res["actual"]]
        return p[-1] - p[0], a[-1] - a[0], res["gw_seed_source"]

    # An undrained seasonal seed (what a short HA hindcast can't drain) vs the assimilated state.
    seasonal_state = model.initial_state(
        art, h0=level_by_hour[t0], sm0=art.seasonal_sm_default(t0.month),
        s_agw0=art.seasonal_agw_default(t0.month), month=t0.month)
    assimilated_net, actual_net, src = net_pred(est.state)
    seasonal_net, _, _ = net_pred(seasonal_state)
    assert src == "assimilated"
    assert net_pred(None)[2] == "seasonal_default"       # no state0 -> seasonal spin-up path
    assert seasonal_net > assimilated_net + 0.005        # undrained seasonal seed drifts higher
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
