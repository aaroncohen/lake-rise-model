"""Factor breakdown: decompose each hourly lake-level change into the exact
feet contributed by each physical flux.

The lake update in m5_lake_update is linear in flow:
    Δh = cfs_to_dh(q_in + q_lake_precip - q_out, dt, A(h_start))

Because cfs_to_dh is linear in q, we can attribute the Δh exactly:
    dh_runoff       = cfs_to_dh(q_in_cfs,          dt, A(h_prev))  >= 0
    dh_direct_rain  = cfs_to_dh(q_lake_precip_cfs, dt, A(h_prev))  >= 0
    dh_spillway     = -cfs_to_dh(q_out_cfs,        dt, A(h_prev))  <= 0

These three sum exactly to rec.h - h_prev (same area used, same formula).
"""

from __future__ import annotations

from .artifact import Artifact
from .geometry import surface_area_acres
from .units import cfs_to_dh


def factor_breakdown(
    art: Artifact,
    records: list,
    h0: float,
    dt: float = 1.0,
) -> dict:
    """Decompose per-step lake-level motion into contributing fluxes.

    Args:
        art:     Model artifact (geometry + hspf params).
        records: List of StepRecord objects from model.run / model.forecast.
        h0:      Lake elevation (ft, absolute) at the step BEFORE records[0]
                 (i.e. the anchor elevation, NOT included in the output arrays).
        dt:      Timestep hours (default 1.0).

    Returns a dict with one entry per record:
        valid_at                  — ISO timestamps
        per_hour_ft               — per-step Δh for each flux
        cumulative_ft             — running cumulative Δh from h0
        net_ft                    — per-step sum (≈ rec.h - h_prev)
        net_cumulative_ft         — running net (≈ rec.h - h0)
        state                     — soil / interflow diagnostics
        totals_ft                 — scalar sums at end of window
    """
    if not records:
        empty: list = []
        return {
            "valid_at": empty,
            "per_hour_ft": {
                "watershed_runoff": empty,
                "direct_rain": empty,
                "spillway": empty,
            },
            "cumulative_ft": {
                "watershed_runoff": empty,
                "direct_rain": empty,
                "spillway": empty,
            },
            "net_ft": empty,
            "net_cumulative_ft": empty,
            "state": {
                "soil_moisture_in": empty,
                "soil_saturation_pct": empty,
                "interflow_storage_in": empty,
                "rain_in": empty,
            },
            "totals_ft": {
                "watershed_runoff": 0.0,
                "direct_rain": 0.0,
                "spillway": 0.0,
                "net": 0.0,
            },
        }

    lzsn = art.hspf.LZSN_in

    valid_at: list[str] = []
    ph_runoff: list[float] = []
    ph_direct: list[float] = []
    ph_spillway: list[float] = []
    cum_runoff: list[float] = []
    cum_direct: list[float] = []
    cum_spillway: list[float] = []
    net_ft: list[float] = []
    net_cum: list[float] = []
    sm_list: list[float] = []
    sat_list: list[float] = []
    sif_list: list[float] = []
    rain_list: list[float] = []

    h_prev = h0
    c_runoff = 0.0
    c_direct = 0.0
    c_spillway = 0.0

    for rec in records:
        a = surface_area_acres(art.geometry, h_prev)

        dh_runoff = cfs_to_dh(rec.q_in_cfs, dt, a)
        dh_direct = cfs_to_dh(rec.q_lake_precip_cfs, dt, a)
        dh_spill = -cfs_to_dh(rec.q_out_cfs, dt, a)
        net = dh_runoff + dh_direct + dh_spill

        c_runoff += dh_runoff
        c_direct += dh_direct
        c_spillway += dh_spill

        valid_at.append(rec.t.isoformat())
        ph_runoff.append(dh_runoff)
        ph_direct.append(dh_direct)
        ph_spillway.append(dh_spill)
        cum_runoff.append(c_runoff)
        cum_direct.append(c_direct)
        cum_spillway.append(c_spillway)
        net_ft.append(net)
        net_cum.append(c_runoff + c_direct + c_spillway)
        sm_list.append(round(rec.sm, 6))
        sat_list.append(round(rec.sm / lzsn * 100.0, 4))
        sif_list.append(round(rec.s_if, 6))
        rain_list.append(round(rec.p_gross_in, 6))

        h_prev = rec.h

    total_net = c_runoff + c_direct + c_spillway
    return {
        "valid_at": valid_at,
        "per_hour_ft": {
            "watershed_runoff": ph_runoff,
            "direct_rain": ph_direct,
            "spillway": ph_spillway,
        },
        "cumulative_ft": {
            "watershed_runoff": cum_runoff,
            "direct_rain": cum_direct,
            "spillway": cum_spillway,
        },
        "net_ft": net_ft,
        "net_cumulative_ft": net_cum,
        "state": {
            "soil_moisture_in": sm_list,
            "soil_saturation_pct": sat_list,
            "interflow_storage_in": sif_list,
            "rain_in": rain_list,
        },
        "totals_ft": {
            "watershed_runoff": c_runoff,
            "direct_rain": c_direct,
            "spillway": c_spillway,
            "net": total_net,
        },
    }
