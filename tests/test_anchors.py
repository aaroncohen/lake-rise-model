"""Integrated calibration anchors from both design docs."""

from datetime import datetime

from lake_rise import model, sim, validate
from lake_rise.geometry import control_elev_for_stop_logs


def test_step6_peak_near_3431(art):
    peak, _ = validate.run_step6(art)
    vt = art.validation_targets
    assert abs(peak - vt.step6_peak_elev_ft) <= vt.step6_peak_tolerance_ft


def test_dry_equilibrium_in_band(art):
    final, _ = validate.run_dry_equilibrium(art)
    lo, hi = art.validation_targets.dry_equilibrium_3logs_ft
    assert lo <= final <= hi


def test_all_anchors_pass(art):
    assert all(r.passed for r in validate.run_anchors(art))


def test_lag_peak_rise_follows_peak_rain(art):
    """Reference Module 4 check: peak lake rise lags peak rainfall by ~4.6 h."""
    start = datetime(2026, 1, 1)
    control = control_elev_for_stop_logs(art.stop_logs, 0)
    state = model.initial_state(art, h0=339.0, sm0=art.hspf.LZSN_in)  # saturated -> responsive
    # a single sharp rain pulse at hour 10
    rain = [0.0] * 10 + [0.8] + [0.0] * 40
    _, recs = model.run(art, state, rain, start, control)
    peak_rain_h = max(range(len(rain)), key=lambda i: rain[i])           # = 10
    peak_rise_h = max(range(len(recs)), key=lambda i: recs[i].q_in_cfs)  # inflow peak
    lag = peak_rise_h - peak_rain_h
    assert 3 <= lag <= 7  # ~4.6 h (lag pipe + routing), within tolerance


def test_saturated_watershed_more_responsive_than_dry(art):
    """Antecedent moisture matters: same storm, dry start rises far less."""
    start = datetime(2026, 1, 1)
    control = control_elev_for_stop_logs(art.stop_logs, 0)
    storm = sim.constant_storm(0.2, 24)

    dry_state = model.initial_state(art, h0=338.8, sm0=0.0)
    _, dry_recs = model.run(art, dry_state, storm, start, control)

    wet_state = model.initial_state(art, h0=338.8, sm0=art.hspf.LZSN_in)
    _, wet_recs = model.run(art, wet_state, storm, start, control)

    assert wet_recs[-1].h - 338.8 > 3 * (dry_recs[-1].h - 338.8)


def test_interflow_engages_below_saturation(art):
    """#3: a storm on moist-but-UNSATURATED soil generates interflow before the bucket
    fills -- the wetness-driven subsurface stormflow (perched table over the till hardpan)
    that the old saturation-excess-only model could not produce until SM reached LZSN."""
    start = datetime(2026, 1, 1)
    control = control_elev_for_stop_logs(art.stop_logs, 0)
    lzsn = art.hspf.LZSN_in
    storm = sim.constant_storm(0.1, 12)
    state = model.initial_state(art, h0=338.8, sm0=0.6 * lzsn)   # moist, well below LZSN
    _, recs = model.run(art, state, storm, start, control)

    assert max(r.sm for r in recs) < lzsn        # never fully saturates
    assert max(r.q_if_cfs for r in recs) > 0.0   # yet interflow is generated
