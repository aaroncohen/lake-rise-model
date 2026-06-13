"""The HSPF-style interflow bucket model: six modules stepped hourly, plus the
hindcast and forecast drivers. Pure and framework-free (spec 2 / 4, Reference
Modules 1-6).

State carried each step: SM (soil moisture, in), S_if (interflow storage, in over
the watershed), h (lake elevation, absolute ft), a lag pipeline of in-flight
inflow (cfs), and bookkeeping for per-storm canopy interception.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from . import units
from .artifact import Artifact
from .geometry import surface_area_acres
from .spillway import spillway_outflow_cfs

DT_HOURS = 1.0
# A storm "ends" (canopy bucket resets) after this many consecutive dry hours.
STORM_DRY_GAP_HOURS = 12


@dataclass
class State:
    sm: float                      # soil moisture, inches (0..LZSN)
    s_if: float                    # interflow storage, inches over watershed
    s_agw: float                   # active-groundwater storage, inches over watershed
    h: float                       # lake elevation, absolute ft
    lag_pipe: deque[float]         # in-flight watershed inflow, cfs (FIFO, len = lag steps)
    canopy_remaining: float        # inches of canopy capacity left in the current storm
    hours_since_rain: float        # for storm-end detection

    def copy(self) -> "State":
        return replace(self, lag_pipe=deque(self.lag_pipe, maxlen=self.lag_pipe.maxlen))


@dataclass
class StepRecord:
    t: datetime
    h: float
    sm: float
    s_if: float
    s_agw: float             # active-groundwater storage, inches over watershed
    p_gross_in: float
    p_eff_in: float
    q_if_cfs: float          # interflow released this step (pre-lag), cfs
    q_in_cfs: float          # interflow inflow arriving at the lake (post-lag), cfs
    q_agw_cfs: float         # active-groundwater baseflow to the lake (bypasses lag), cfs
    q_lake_precip_cfs: float
    q_out_cfs: float
    q_net_cfs: float


def lag_steps(art: Artifact, dt_hours: float = DT_HOURS) -> int:
    return max(1, round(art.watershed.lag_hours / dt_hours))


def initial_state(
    art: Artifact,
    h0: float,
    sm0: float | None = None,
    s_if0: float = 0.0,
    s_agw0: float | None = None,
    month: int | None = None,
) -> State:
    """Build a starting state. If sm0/s_agw0 are None, seed them from the seasonal
    defaults using ``month`` (Reference 1.5; groundwater seed so the slow store isn't
    unphysically empty)."""
    if sm0 is None:
        sm0 = art.seasonal_sm_default(month) if month is not None else art.hspf.LZSN_in * 0.5
    if s_agw0 is None:
        s_agw0 = art.seasonal_agw_default(month) if month is not None else 0.0
    n = lag_steps(art)
    return State(
        sm=sm0,
        s_if=s_if0,
        s_agw=s_agw0,
        h=h0,
        lag_pipe=deque([0.0] * n, maxlen=n),
        canopy_remaining=art.hspf.CEPSC_in_per_storm,
        hours_since_rain=STORM_DRY_GAP_HOURS,  # start "between storms"
    )


# --- the six modules, as small pure functions -------------------------------------

def m1_canopy(art: Artifact, p_gross: float, canopy_remaining: float, hours_since_rain: float,
              dt: float) -> tuple[float, float, float]:
    """Module 1: subtract CEPSC once per storm. Returns (p_eff, canopy_remaining, hours_since_rain)."""
    if p_gross <= 0.0:
        hours_since_rain += dt
        if hours_since_rain >= STORM_DRY_GAP_HOURS:
            canopy_remaining = art.hspf.CEPSC_in_per_storm  # storm over -> refill canopy bucket
        return 0.0, canopy_remaining, hours_since_rain
    # raining
    hours_since_rain = 0.0
    intercepted = min(p_gross, canopy_remaining)
    canopy_remaining -= intercepted
    return p_gross - intercepted, canopy_remaining, hours_since_rain


def m2_soil_bucket(art: Artifact, sm: float, p_eff: float, month: int,
                   dt: float) -> tuple[float, float, float]:
    """Module 2: fill SM (cap LZSN), drain by PET*LZETP, then percolate a saturation-
    dependent flux to groundwater. Returns (sm_new, overflow_in, perc_in).

    overflow is the saturation excess (SM>LZSN), which routes to fast interflow.
    perc is HSPF-style matrix drainage active *below* saturation -- the only groundwater
    recharge path -- so light rain produces a small baseflow instead of nothing."""
    lzsn = art.hspf.LZSN_in
    sm_filled = sm + p_eff
    overflow = max(0.0, sm_filled - lzsn)
    sm_filled = min(sm_filled, lzsn)
    pet_hr = art.pet_for_month(month) / units.days_in_month(month) / 24.0
    et = min(sm_filled, pet_hr * art.hspf.LZETP * dt)
    sm_new = max(0.0, sm_filled - et)
    # Percolation: perc = PERC_coeff * INFILT * INFILD * (SM/LZSN)**INFEXP per hour.
    # (SM/LZSN)**INFEXP strongly suppresses drainage when the soil is dry.
    sat = sm_new / lzsn
    perc = min(sm_new, art.hspf.PERC_coeff * art.hspf.INFILT_in_per_hr
                       * art.hspf.INFILD * sat ** art.hspf.INFEXP * dt)
    sm_new -= perc
    return sm_new, overflow, perc


def m3_interflow(art: Artifact, s_if: float, inflow_in: float, dt: float) -> tuple[float, float]:
    """Module 3: add this step's interflow inflow to interflow storage, release at the
    hourly-equivalent IRC. ``inflow_in`` is the soil-bucket overflow (saturation excess),
    which routes 100% to fast interflow; groundwater is fed separately by percolation in
    ``m7_groundwater``. Returns (s_if_new, q_if_cfs)."""
    s_if = s_if + inflow_in
    hourly_drain = 1.0 - (1.0 - art.hspf.IRC_per_day) ** (dt / 24.0)
    released_in = s_if * hourly_drain
    s_if -= released_in
    q_if_cfs = units.depth_in_to_cfs(released_in, art.watershed.drainage_area_acres, dt)
    return s_if, q_if_cfs


def m7_groundwater(art: Artifact, s_agw: float, perc_in: float, dt: float) -> tuple[float, float]:
    """Module 7: the active-groundwater (baseflow) reservoir. Percolation recharges it
    less the permanent DEEPFR sink; the store drains very slowly at the hourly-equivalent
    AGWRC (t½ ~173 d), optionally nonlinear via KVARY. Returns (s_agw_new, q_agw_cfs).

    Mirrors m3_interflow's recession shape but three orders of magnitude slower, which is
    what sustains the lake for days after rain stops (brief §B)."""
    s_agw = s_agw + perc_in * (1.0 - art.hspf.DEEPFR)  # (1-DEEPFR) returns; DEEPFR leaves the basin
    hourly_drain = 1.0 - art.hspf.AGWRC_per_day ** (dt / 24.0)
    # KVARY=0 -> linear store. Otherwise steepen recession when GW storage is high, using
    # AGWS (inches) as the GWVS index so KVARY keeps its natural /inch units (brief §B.2).
    factor = 1.0 + art.hspf.KVARY_per_in * s_agw
    released_in = min(s_agw, s_agw * hourly_drain * factor)
    s_agw -= released_in
    q_agw_cfs = units.depth_in_to_cfs(released_in, art.watershed.drainage_area_acres, dt)
    return s_agw, q_agw_cfs


def m4_lag(lag_pipe: deque[float], q_if_cfs: float) -> float:
    """Module 4: push this step's interflow into the pipeline, return what arrives
    now (delayed by ~4.6 h)."""
    arriving = lag_pipe[0] if len(lag_pipe) == lag_pipe.maxlen else 0.0
    lag_pipe.append(q_if_cfs)  # maxlen deque drops the oldest automatically
    return arriving


def m5_lake_update(art: Artifact, h: float, q_in_cfs: float, p_gross: float, q_out_cfs: float,
                   dt: float) -> tuple[float, float, float]:
    """Module 5: combine inflows and outflow, step elevation. Returns (h_new, q_lake_precip_cfs, q_net_cfs)."""
    a = surface_area_acres(art.geometry, h)
    q_lake_precip = units.depth_in_to_cfs(p_gross, a, dt)  # direct precip on the lake (minor)
    q_net = q_in_cfs + q_lake_precip - q_out_cfs
    h_new = h + units.cfs_to_dh(q_net, dt, a)
    return h_new, q_lake_precip, q_net


def m6_spillway(art: Artifact, h: float, control_elev: float) -> float:
    """Module 6: spillway + leakage outflow at the current elevation."""
    return spillway_outflow_cfs(art.spillway, h, control_elev)


# --- one timestep -----------------------------------------------------------------

def step(art: Artifact, state: State, p_gross_in: float, t: datetime, control_elev: float,
         dt: float = DT_HOURS) -> tuple[State, StepRecord]:
    """Advance the model one timestep. Explicit (outflow uses h at step start)."""
    p_eff, canopy_remaining, hours_since_rain = m1_canopy(
        art, p_gross_in, state.canopy_remaining, state.hours_since_rain, dt)
    sm_new, overflow, perc = m2_soil_bucket(art, state.sm, p_eff, t.month, dt)

    # Saturation-excess overflow -> fast interflow; soil percolation -> slow groundwater.
    s_if_new, q_if_cfs = m3_interflow(art, state.s_if, overflow, dt)
    s_agw_new, q_agw_cfs = m7_groundwater(art, state.s_agw, perc, dt)

    pipe = deque(state.lag_pipe, maxlen=state.lag_pipe.maxlen)
    q_in_cfs = m4_lag(pipe, q_if_cfs)  # interflow keeps the ~4.6 h basin lag

    q_out_cfs = m6_spillway(art, state.h, control_elev)
    # Baseflow bypasses the basin lag (slow enough that 4.6 h is negligible).
    h_new, q_lake_precip, q_net = m5_lake_update(
        art, state.h, q_in_cfs + q_agw_cfs, p_gross_in, q_out_cfs, dt)

    new_state = State(
        sm=sm_new, s_if=s_if_new, s_agw=s_agw_new, h=h_new, lag_pipe=pipe,
        canopy_remaining=canopy_remaining, hours_since_rain=hours_since_rain,
    )
    rec = StepRecord(
        t=t, h=h_new, sm=sm_new, s_if=s_if_new, s_agw=s_agw_new,
        p_gross_in=p_gross_in, p_eff_in=p_eff,
        q_if_cfs=q_if_cfs, q_in_cfs=q_in_cfs, q_agw_cfs=q_agw_cfs,
        q_lake_precip_cfs=q_lake_precip, q_out_cfs=q_out_cfs, q_net_cfs=q_net,
    )
    return new_state, rec


# --- drivers ----------------------------------------------------------------------

def run(art: Artifact, state: State, rainfall_in: list[float], start: datetime,
        control_elev: float, dt: float = DT_HOURS) -> tuple[State, list[StepRecord]]:
    """Step the model over an hourly rainfall series. Returns (end_state, records)."""
    records: list[StepRecord] = []
    s = state
    t = start
    for p in rainfall_in:
        t = t + timedelta(hours=dt)
        s, rec = step(art, s, p, t, control_elev, dt)
        records.append(rec)
    return s, records


def hindcast(art: Artifact, rainfall_in: list[float], h0: float, start: datetime,
             control_elev: float, sm0: float | None = None, s_if0: float = 0.0,
             s_agw0: float | None = None, dt: float = DT_HOURS) -> tuple[State, list[StepRecord]]:
    """Replay observed rainfall to spin up SM/S_if/S_agw and arrive at current state
    (Reference: Hindcast mode). ``h0`` is the gauge elevation at ``start``."""
    state = initial_state(art, h0=h0, sm0=sm0, s_if0=s_if0, s_agw0=s_agw0, month=start.month)
    return run(art, state, rainfall_in, start, control_elev, dt)


def forecast(art: Artifact, end_state: State, scenario_rain_in: list[float], start: datetime,
             control_elev: float, dt: float = DT_HOURS) -> list[StepRecord]:
    """Project forward from a hindcast end-state under one rainfall scenario
    (Reference: Forecast mode). The lag pipeline is inherited, so the first ~4.6 h
    are still measurement-constrained."""
    _, records = run(art, end_state.copy(), scenario_rain_in, start, control_elev, dt)
    return records
