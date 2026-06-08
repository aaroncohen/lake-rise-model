"""Per-module checks. The Hydrologic Reference gives one validation per module;
these encode them so each piece is correct before the integrated anchors run."""

from datetime import datetime

from lake_rise import model, units
from lake_rise.geometry import (
    control_elev_for_stop_logs,
    default_stop_log_count,
    surface_area_acres,
    storage_acft,
)
from lake_rise.spillway import spillway_outflow_cfs


def test_m1_canopy_absorbs_first_020_in_of_storm(art):
    """Module 1: a dry-start storm's first ~0.20 in is intercepted (zero P_eff)."""
    cap = art.hspf.CEPSC_in_per_storm
    rem, hsr = cap, model.STORM_DRY_GAP_HOURS
    p_eff_total = 0.0
    for _ in range(3):  # 0.1 in/hr for 3 h = 0.3 in gross
        pe, rem, hsr = model.m1_canopy(art, 0.1, rem, hsr, 1.0)
        p_eff_total += pe
    assert abs(p_eff_total - (0.3 - cap)) < 1e-9
    assert rem == 0.0


def test_m1_canopy_resets_after_dry_gap(art):
    rem, hsr = 0.0, 0.0  # mid-storm, canopy already used
    for _ in range(model.STORM_DRY_GAP_HOURS):
        _, rem, hsr = model.m1_canopy(art, 0.0, rem, hsr, 1.0)
    assert rem == art.hspf.CEPSC_in_per_storm  # refilled


def test_m2_soil_bucket_overflows_only_above_capacity(art):
    lzsn = art.hspf.LZSN_in
    # below capacity -> no overflow
    sm, ov = model.m2_soil_bucket(art, sm=1.0, p_eff=1.0, month=1, dt=1.0)
    assert ov == 0.0 and sm <= lzsn
    # at capacity + extra -> overflow equals the excess (winter PET ~ 0)
    sm, ov = model.m2_soil_bucket(art, sm=lzsn, p_eff=0.5, month=1, dt=1.0)
    assert abs(ov - 0.5) < 0.01


def test_m3_interflow_drains_half_per_day(art):
    """IRC = 0.5/day, applied via the doc's hourly fraction 1-(1-0.5)^(1/24),
    drains exactly 50% per 24 h (half-life 1 day; the doc's '~2 day' prose is the
    loose recession-length descriptor, not the formula)."""
    s_if = 1.0
    s_if, _ = model.m3_interflow(art, s_if=s_if, overflow_in=0.0, dt=1.0)
    start = s_if
    for _ in range(24):  # 1 day
        s_if, _ = model.m3_interflow(art, s_if=s_if, overflow_in=0.0, dt=1.0)
    assert abs(s_if / start - 0.5) < 0.02
    # and a second day halves it again
    for _ in range(24):
        s_if, _ = model.m3_interflow(art, s_if=s_if, overflow_in=0.0, dt=1.0)
    assert abs(s_if / start - 0.25) < 0.02


def test_m4_lag_is_about_46_hours(art):
    n = model.lag_steps(art)
    assert n == 5  # round(4.6)
    pipe = model.initial_state(art, h0=339.0).lag_pipe
    # inject a unit pulse; it should emerge n steps later
    arrivals = []
    for i in range(n + 2):
        val = 100.0 if i == 0 else 0.0
        arrivals.append(model.m4_lag(pipe, val))
    assert arrivals[n] == 100.0
    assert all(a == 0.0 for j, a in enumerate(arrivals) if j != n)


def test_geometry_matches_documented_hard_points(art):
    geom = art.geometry
    # Storage hard points (Reference 3.1): 338.8->131, 340.0->230, 342.15->~486
    assert abs(storage_acft(geom, 338.8) - 131) < 2
    assert abs(storage_acft(geom, 340.0) - 230) < 3
    assert abs(storage_acft(geom, 342.15) - 486) < 3
    # Surface area: 75.1 ac at base, ~96 ac at 340.0
    assert abs(surface_area_acres(geom, 338.8) - 75.1) < 0.5
    assert abs(surface_area_acres(geom, 340.0) - 96.1) < 0.5


def test_stop_log_control_elevations(art):
    assert control_elev_for_stop_logs(art.stop_logs, 0) == 338.800
    assert control_elev_for_stop_logs(art.stop_logs, 3) == 339.675
    # seasonal default: summer in, winter out
    assert default_stop_log_count(art.stop_logs, 7, 1) == 3
    assert default_stop_log_count(art.stop_logs, 1, 1) == 0


def test_m6_spillway_zero_below_control_rises_above(art):
    sp = art.spillway
    control = control_elev_for_stop_logs(art.stop_logs, 0)  # 338.8
    assert spillway_outflow_cfs(sp, 338.0, control) == 0.0          # well below -> nothing
    assert spillway_outflow_cfs(sp, 342.0, control) > 100           # ~combined capacity at 342
    # leakage just below a 3-log control
    c3 = control_elev_for_stop_logs(art.stop_logs, 3)
    assert spillway_outflow_cfs(sp, c3 - 0.1, c3) == art.spillway.leakage.cfs


def test_units_self_consistent(art):
    # 1 inch over 2131 ac sustained 24 h ~ 89.5 cfs (physical), not the doc's ~49.
    cfs = units.depth_in_to_cfs(1.0, 2131, 24.0)
    assert 88 < cfs < 91
