"""Spillway outflow as a function of lake elevation and stop-log control elevation
(spec 4 Module 6 / Reference Module 6).

Each spillway leg is modeled as a rectangular weir: Q = C * L * H ** exponent, where H
is the head above the control elevation, L is the physical crest length, and C is the
discharge coefficient. The exponent is the weir law (1.5). Where a crest length is
measured, C is derived from the single known capacity point (capacity at 342 ft over the
bare sill), so the curve still honors the reported capacity but is now expressed in
physical terms — and the coefficient can be cross-checked against textbook ranges
(~1.9-3.3 for a stop-log structure). Expressing it physically also means a raised crest
(stop-logs in) correctly reduces discharge at a given lake elevation, instead of pinning
the same capacity regardless of the control setting. Where no crest length is known the
leg falls back to the algebraically-equivalent capacity-ratio form.

Above the dam crest the crest spills as a broad-crested weir (``overtopping``), added on
top of the spillway legs. The crest is not level — it sags to a low point where
overtopping begins and rises to the bridge deck — so its wetted length (and thus the
discharge) grows with stage rather than switching on full-width at once.

Every stop-log leg also seeps through its seams whenever water stands above them — at the
bare-board recession and continuously underneath a sheet spilling over the top. That seam
leakage is added to both legs, scaled by the seam width and the submerged seam height."""

from __future__ import annotations

from .artifact import Overtopping, Spillway, SpillwayLeg

ORIFICE_EXPONENT = 0.5   # submerged-orifice law: Q grows as sqrt of the head (vs 1.5 for a weir)


def _rect_weir(coeff: float, length_ft: float, head_ft: float, exponent: float) -> float:
    """Rectangular-weir discharge: Q = coeff * length * head**exponent. Head is measured
    above the weir crest; at or below the crest the flow is zero."""
    if head_ft <= 0.0:
        return 0.0
    return coeff * length_ft * head_ft ** exponent


def leg_weir_coeff(leg: SpillwayLeg, rated_elev_ft: float, exponent: float) -> float:
    """The discharge coefficient C implied by a leg's reported capacity and crest length,
    from capacity = C * L * H_rated**exponent at the rated (342 ft) elevation over the
    leg's bare sill. Both Crystal Lake legs land near C~1.9, a consistent broad/submerged
    stop-log coefficient — an independent corroboration of the reported capacities."""
    h_rated = rated_elev_ft - leg.control_elev_ft
    return leg.capacity_cfs_at_342 / (leg.crest_length_ft * h_rated ** exponent)


def _leg_flow(leg: SpillwayLeg, control_elev: float, rated_elev: float, h: float,
              exponent: float) -> float:
    """Weir/orifice stage-discharge for one leg at lake elevation ``h``. ``control_elev``
    is the active crest (for the primary this is the stop-log elevation, which may sit
    above the leg's bare sill).

    Below the opening soffit: weir flow, Q = C * L * (h-control)**exponent with C from the
    bare-sill capacity (or the capacity-ratio fallback when no crest length is known).

    Above the soffit the opening is fully submerged and the leg drowns out: flow follows a
    submerged-orifice law growing as the square root of the head on the opening centroid,
    anchored for continuity to the weir flow at the drowning point. The exponent drops
    1.5 -> 0.5, so discharge keeps rising but far more slowly — the flow nearly plateaus
    between drowning and dam overtopping."""
    head = h - control_elev
    if head <= 0.0:
        return 0.0
    if not leg.crest_length_ft:
        h_rated = rated_elev - control_elev
        return leg.capacity_cfs_at_342 * (head / h_rated) ** exponent

    cl = leg_weir_coeff(leg, rated_elev, exponent) * leg.crest_length_ft  # C * L
    soffit = leg.soffit_elev_ft
    if soffit is not None and h > soffit > control_elev:
        opening = soffit - control_elev                 # opening height above the crest
        q_drown = cl * opening ** exponent              # weir flow at the drowning point
        head_centroid = h - (control_elev + soffit) / 2.0
        return q_drown * (head_centroid / (opening / 2.0)) ** ORIFICE_EXPONENT
    return cl * head ** exponent


def seam_leakage_cfs(leg: SpillwayLeg, crest_elev: float, h_abs_ft: float,
                     cfs_per_ft2: float) -> float:
    """Seepage through a stop-log leg's seams: proportional to the seam width (crest
    length) and the height of water standing over the seams, from the stack bottom up to
    the crest. Present whenever water is above the lowest seam — including while a sheet of
    water is spilling over the crest — and plateaus once the whole stack is submerged."""
    bottom = leg.seam_bottom_elev_ft
    if bottom is None or not leg.crest_length_ft:
        return 0.0
    submerged = min(h_abs_ft, crest_elev) - bottom   # capped at the crest (full stack)
    if submerged <= 0.0:
        return 0.0
    return cfs_per_ft2 * leg.crest_length_ft * submerged


def overtopping_outflow_cfs(ot: Overtopping | None, h_abs_ft: float, exponent: float) -> float:
    """Flow over the dam crest once the lake exceeds it.

    The crest is not level: it sags to a low point (``crest_elev_ft``, ~25 ft east of
    the bridge) where overtopping begins, and rises to the bridge deck
    (``bridge_deck_elev_ft``), the high point. So the wetted crest grows from a point at
    the low sag to the full ``crest_length_ft`` as the lake climbs to the bridge deck —
    a gradual onset, not a full-length weir switching on at once.

    With a linearly-sloped crest the effective length grows linearly with stage, so the
    discharge is the weir law integrated over the submerged crest. Writing z_low for the
    low point, z_top for the bridge deck, and β = crest_length / (z_top − z_low) (the
    crest length gained per foot of rise), and p = exponent + 1:

        Q = weir_coeff · β · [ (h − z_low)**p − max(0, h − z_top)**p ] / p

    Below the bridge deck the second term is zero (only the sag is wetted); above it the
    head keeps growing on the now fully-engaged crest. With no bridge deck modeled the
    crest is a single flat broad-crested weir at ``crest_elev_ft`` (legacy)."""
    if ot is None:
        return 0.0
    z_low = ot.crest_elev_ft
    if h_abs_ft <= z_low:
        return 0.0
    z_top = ot.bridge_deck_elev_ft
    if z_top is None or z_top <= z_low:
        # Legacy flat broad-crested weir: the whole crest sits at one elevation.
        return _rect_weir(ot.weir_coeff, ot.crest_length_ft, h_abs_ft - z_low, exponent)
    beta = ot.crest_length_ft / (z_top - z_low)    # ft of crest engaged per ft of rise
    p = exponent + 1.0
    head_low = h_abs_ft - z_low
    head_top = max(0.0, h_abs_ft - z_top)          # 0 until the lake tops the bridge deck
    return ot.weir_coeff * beta * (head_low ** p - head_top ** p) / p


def spillway_outflow_cfs(sp: Spillway, h_abs_ft: float, control_elev_ft: float) -> float:
    """Total outflow (cfs) at elevation h given the active stop-log control elevation:
    primary leg + auxiliary leg + dam-crest overtopping.

    The primary spillway's effective control is the stop-log control elevation; the
    auxiliary spillway has its own fixed control (~340.0 ft) and only engages above it.
    Both legs additionally seep through their stop-log seams whenever water stands above
    them, including while spilling over the top."""
    rated = sp.rated_head_elev_ft
    n = sp.weir_exponent
    k = sp.leakage.cfs_per_ft2

    # Primary: controlled by stop-logs (crest may sit above the bare sill); seam leakage
    # runs from the stack bottom up to the active stop-log crest.
    q_primary = _leg_flow(sp.primary, control_elev_ft, rated, h_abs_ft, n)
    q_primary += seam_leakage_cfs(sp.primary, control_elev_ft, h_abs_ft, k)

    # Auxiliary: fixed control, independent of stop-logs; seams run up to the 340 ft crest.
    aux_crest = sp.auxiliary.control_elev_ft
    q_aux = _leg_flow(sp.auxiliary, aux_crest, rated, h_abs_ft, n)
    q_aux += seam_leakage_cfs(sp.auxiliary, aux_crest, h_abs_ft, k)

    # Dam-crest overtopping: added on top once the lake exceeds the crest.
    q_overtop = overtopping_outflow_cfs(sp.overtopping, h_abs_ft, n)

    return q_primary + q_aux + q_overtop
