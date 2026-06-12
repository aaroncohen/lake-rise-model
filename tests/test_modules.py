"""Per-module checks. The Hydrologic Reference gives one validation per module;
these encode them so each piece is correct before the integrated anchors run."""

import math
from datetime import datetime

from lake_rise import model, units
from lake_rise.geometry import (
    control_elev_for_stop_logs,
    default_stop_log_count,
    surface_area_acres,
    storage_acft,
)
from lake_rise.spillway import (
    _leg_flow,
    leg_weir_coeff,
    overtopping_outflow_cfs,
    seam_leakage_cfs,
    spillway_outflow_cfs,
)


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
    s_if, _ = model.m3_interflow(art, s_if=s_if, inflow_in=0.0, dt=1.0)
    start = s_if
    for _ in range(24):  # 1 day
        s_if, _ = model.m3_interflow(art, s_if=s_if, inflow_in=0.0, dt=1.0)
    assert abs(s_if / start - 0.5) < 0.02
    # and a second day halves it again
    for _ in range(24):
        s_if, _ = model.m3_interflow(art, s_if=s_if, inflow_in=0.0, dt=1.0)
    assert abs(s_if / start - 0.25) < 0.02


def test_m7_groundwater_recession_half_life_about_173_days(art):
    """AGWRC = 0.996/day: the active-GW store has a ~173-day half-life, three orders of
    magnitude slower than interflow. This slow tail is what holds the lake up for days."""
    assert art.hspf.AGWRC_per_day == 0.996
    half_life_days = math.log(0.5) / math.log(art.hspf.AGWRC_per_day)
    assert abs(half_life_days - 173) < 1.0
    # Drain a charged store and confirm it halves after ~173 days of hourly steps.
    s_agw = 1.0
    for _ in range(round(half_life_days) * 24):
        s_agw, _ = model.m7_groundwater(art, s_agw=s_agw, perc_in=0.0, dt=1.0)
    assert abs(s_agw - 0.5) < 0.01


def test_m7_groundwater_deepfr_is_the_only_loss(art):
    """A percolation pulse recharges (1-DEEPFR); exactly DEEPFR leaves the basin. With no
    further inflow the store eventually drains fully — only DEEPFR is permanent."""
    deepfr = art.hspf.DEEPFR
    assert deepfr == 0.05
    # From empty, 1.0 in of percolation: (1-DEEPFR)=0.95 recharges, then a tiny first
    # hourly release. Storage sits just under 0.95 — confirming 0.05 (not more) was lost.
    s_agw, _ = model.m7_groundwater(art, s_agw=0.0, perc_in=1.0, dt=1.0)
    assert 0.949 < s_agw < (1.0 - deepfr)
    # Drain fully (slow store): after ~6 yr of hourly steps storage is ~0, so all of the
    # 0.95 recharge left as baseflow and only the 0.05 DEEPFR was ever permanently lost.
    s = s_agw
    for _ in range(6 * 365 * 24):
        s, _ = model.m7_groundwater(art, s_agw=s, perc_in=0.0, dt=1.0)
    assert s < 1e-3


def test_m7_groundwater_linear_when_kvary_zero(art):
    """With KVARY=0 the store is a pure linear reservoir: release scales with storage."""
    assert art.hspf.KVARY_per_in == 0.0
    _, q_lo = model.m7_groundwater(art, s_agw=1.0, perc_in=0.0, dt=1.0)
    _, q_hi = model.m7_groundwater(art, s_agw=2.0, perc_in=0.0, dt=1.0)
    assert abs(q_hi - 2.0 * q_lo) < 1e-9  # linear: double the storage -> double the release


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
    # seasonal default per EAP: summer normal 3 boards, winter normal 2 (one pulled)
    assert default_stop_log_count(art.stop_logs, 7, 1) == 3
    assert default_stop_log_count(art.stop_logs, 1, 1) == 2


def test_m6_spillway_zero_below_control_rises_above(art):
    sp = art.spillway
    control = control_elev_for_stop_logs(art.stop_logs, 0)  # 338.8
    assert spillway_outflow_cfs(sp, 338.0, control) == 0.0          # well below -> nothing
    assert spillway_outflow_cfs(sp, 342.0, control) > 100           # ~combined capacity at 342
    # seam leakage just below a 3-log control: both legs seep, scaled by width x the
    # submerged seam height. Total is the dry-recession seepage (~0.7-0.8 cfs).
    c3 = control_elev_for_stop_logs(art.stop_logs, 3)
    k = art.spillway.leakage.cfs_per_ft2
    h = c3 - 0.1
    expected = (seam_leakage_cfs(sp.primary, c3, h, k)
                + seam_leakage_cfs(sp.auxiliary, sp.auxiliary.control_elev_ft, h, k))
    assert spillway_outflow_cfs(sp, h, c3) == expected
    assert 0.6 < expected < 0.9


def test_m6_physical_crest_lengths_corroborate_reported_capacity(art):
    sp = art.spillway
    n = sp.weir_exponent
    # Both legs imply the same broad/submerged stop-log weir coefficient (~1.9): the
    # measured crest lengths are physically consistent with the reported capacities.
    c_primary = leg_weir_coeff(sp.primary, sp.rated_head_elev_ft, n)
    c_aux = leg_weir_coeff(sp.auxiliary, sp.rated_head_elev_ft, n)
    assert abs(c_primary - c_aux) < 0.1
    assert 1.5 < c_primary < 3.5
    # Where a leg is still a free weir at its rating point, the physical form reproduces
    # the reported capacity: the primary's soffit is 342 ft, so primary alone passes ~110.
    control0 = control_elev_for_stop_logs(art.stop_logs, 0)  # 338.8, no logs
    q_primary_342 = _leg_flow(sp.primary, control0, sp.rated_head_elev_ft, 342.0, n)
    assert abs(q_primary_342 - sp.primary.capacity_cfs_at_342) < 0.5
    # The aux drowns under the bridge soffit (341.3 ft) below its 342 ft rating point, so
    # at 342 it passes LESS than its nominal free-weir 40 cfs — the bridge constricts it.
    q_aux_342 = _leg_flow(sp.auxiliary, sp.auxiliary.control_elev_ft, sp.rated_head_elev_ft, 342.0, n)
    assert q_aux_342 < sp.auxiliary.capacity_cfs_at_342
    # Raising the crest with stop-logs cuts discharge at a fixed elevation (a weir with
    # less head passes less water) — the accuracy gain over a fixed-capacity model.
    c3 = control_elev_for_stop_logs(art.stop_logs, 3)
    assert spillway_outflow_cfs(sp, 342.0, c3) < spillway_outflow_cfs(sp, 342.0, control0)


def test_m6_seam_leakage_present_for_both_legs_and_while_spilling(art):
    sp = art.spillway
    k = sp.leakage.cfs_per_ft2
    # Seam leakage scales with submerged seam height: more water over the seams -> more
    # seepage, and it vanishes at/below the stack bottom (338.8 ft).
    assert seam_leakage_cfs(sp.primary, 339.675, 338.8, k) == 0.0       # at the seam bottom
    assert (seam_leakage_cfs(sp.primary, 339.675, 339.2, k)
            < seam_leakage_cfs(sp.primary, 339.675, 339.6, k))          # grows with height
    # Both legs seep — the aux contributes too (same 338.8 stack base, 7.5 ft of seam).
    assert seam_leakage_cfs(sp.auxiliary, 340.0, 339.6, k) > 0.0
    # Leakage plateaus once the whole stack is submerged (capped at the crest), so it
    # keeps flowing while water spills over the top rather than cutting off.
    aux_full = seam_leakage_cfs(sp.auxiliary, 340.0, 340.0, k)
    assert seam_leakage_cfs(sp.auxiliary, 340.0, 342.5, k) == aux_full  # spilling: still leaking
    assert abs(aux_full - k * sp.auxiliary.crest_length_ft * (340.0 - 338.8)) < 1e-9
    # It is genuinely additive to spillway flow above the crest (not gated off).
    control0 = control_elev_for_stop_logs(art.stop_logs, 0)
    assert spillway_outflow_cfs(sp, 341.0, control0) > _leg_flow(
        sp.auxiliary, 340.0, sp.rated_head_elev_ft, 341.0, sp.weir_exponent)


def test_m6_submergence_slows_flow_above_soffit(art):
    sp = art.spillway
    n = sp.weir_exponent
    aux, soffit = sp.auxiliary, sp.auxiliary.soffit_elev_ft  # 341.3
    ctrl = aux.control_elev_ft
    rated = sp.rated_head_elev_ft
    # Continuous at the drowning point (weir -> orifice hand-off).
    lo = _leg_flow(aux, ctrl, rated, soffit - 1e-4, n)
    hi = _leg_flow(aux, ctrl, rated, soffit + 1e-4, n)
    assert abs(hi - lo) < 0.01
    # Flow keeps rising above the soffit but far more slowly than the weir would: the
    # orifice (sqrt) branch sits below the extrapolated weir at the same elevation.
    h = soffit + 0.6
    orifice = _leg_flow(aux, ctrl, rated, h, n)
    weir_extrap = leg_weir_coeff(aux, rated, n) * aux.crest_length_ft * (h - ctrl) ** n
    assert lo < orifice < weir_extrap
    # The drowned slope is gentler than the free-weir slope just below the soffit.
    d_below = (_leg_flow(aux, ctrl, rated, soffit, n)
               - _leg_flow(aux, ctrl, rated, soffit - 0.1, n))
    d_above = (_leg_flow(aux, ctrl, rated, soffit + 0.1, n)
               - _leg_flow(aux, ctrl, rated, soffit, n))
    assert 0 < d_above < d_below


def test_m6_overtopping_engages_above_crest(art):
    sp = art.spillway
    ot = sp.overtopping
    n = sp.weir_exponent
    control0 = control_elev_for_stop_logs(art.stop_logs, 0)
    # No overtopping at or below the crest; it turns on smoothly above it and grows.
    assert overtopping_outflow_cfs(ot, ot.crest_elev_ft, n) == 0.0
    half = overtopping_outflow_cfs(ot, ot.crest_elev_ft + 0.5, n)
    full = overtopping_outflow_cfs(ot, ot.crest_elev_ft + 1.0, n)
    assert 0.0 < half < full
    # The 60 ft crest is a large weir: 1 ft over it sheds >150 cfs of extra relief.
    assert full > 150
    # Total outflow above the crest = both legs + the overtopping term (additive).
    h = ot.crest_elev_ft + 1.0
    total = spillway_outflow_cfs(sp, h, control0)
    legs = total - overtopping_outflow_cfs(ot, h, n)
    assert abs(total - (legs + full)) < 1e-6
    assert legs > 0  # legs keep flowing while the crest overtops


def test_units_self_consistent(art):
    # 1 inch over 2131 ac sustained 24 h ~ 89.5 cfs (physical), not the doc's ~49.
    cfs = units.depth_in_to_cfs(1.0, 2131, 24.0)
    assert 88 < cfs < 91
