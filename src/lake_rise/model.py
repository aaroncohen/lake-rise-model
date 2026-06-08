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
    p_gross_in: float
    p_eff_in: float
    q_if_cfs: float          # interflow released this step (pre-lag), cfs
    q_in_cfs: float          # watershed inflow arriving at the lake (post-lag), cfs
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
    month: int | None = None,
) -> State:
    """Build a starting state. If sm0 is None, seed it from the seasonal default
    (Reference 1.5) using ``month``."""
    if sm0 is None:
        sm0 = art.seasonal_sm_default(month) if month is not None else art.hspf.LZSN_in * 0.5
    n = lag_steps(art)
    return State(
        sm=sm0,
        s_if=s_if0,
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


def m2_soil_bucket(art: Artifact, sm: float, p_eff: float, month: int, dt: float) -> tuple[float, float]:
    """Module 2: fill SM (cap LZSN), drain by PET*LZETP. Returns (sm_new, overflow_in)."""
    lzsn = art.hspf.LZSN_in
    sm_filled = sm + p_eff
    overflow = max(0.0, sm_filled - lzsn)
    sm_filled = min(sm_filled, lzsn)
    pet_hr = art.pet_for_month(month) / units.days_in_month(month) / 24.0
    et = min(sm_filled, pet_hr * art.hspf.LZETP * dt)
    sm_new = max(0.0, sm_filled - et)
    return sm_new, overflow


def m3_interflow(art: Artifact, s_if: float, overflow_in: float, dt: float) -> tuple[float, float]:
    """Module 3: route overflow to interflow storage (less deep-groundwater loss),
    release at the hourly-equivalent IRC. Returns (s_if_new, q_if_cfs)."""
    to_interflow = overflow_in * (1.0 - art.watershed.deep_loss_fraction)
    s_if = s_if + to_interflow
    hourly_drain = 1.0 - (1.0 - art.hspf.IRC_per_day) ** (dt / 24.0)
    released_in = s_if * hourly_drain
    s_if -= released_in
    q_if_cfs = units.depth_in_to_cfs(released_in, art.watershed.drainage_area_acres, dt)
    return s_if, q_if_cfs


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
    sm_new, overflow = m2_soil_bucket(art, state.sm, p_eff, t.month, dt)
    s_if_new, q_if_cfs = m3_interflow(art, state.s_if, overflow, dt)

    pipe = deque(state.lag_pipe, maxlen=state.lag_pipe.maxlen)
    q_in_cfs = m4_lag(pipe, q_if_cfs)

    q_out_cfs = m6_spillway(art, state.h, control_elev)
    h_new, q_lake_precip, q_net = m5_lake_update(art, state.h, q_in_cfs, p_gross_in, q_out_cfs, dt)

    new_state = State(
        sm=sm_new, s_if=s_if_new, h=h_new, lag_pipe=pipe,
        canopy_remaining=canopy_remaining, hours_since_rain=hours_since_rain,
    )
    rec = StepRecord(
        t=t, h=h_new, sm=sm_new, s_if=s_if_new, p_gross_in=p_gross_in, p_eff_in=p_eff,
        q_if_cfs=q_if_cfs, q_in_cfs=q_in_cfs, q_lake_precip_cfs=q_lake_precip,
        q_out_cfs=q_out_cfs, q_net_cfs=q_net,
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
             dt: float = DT_HOURS) -> tuple[State, list[StepRecord]]:
    """Replay observed rainfall to spin up SM/S_if and arrive at current state
    (Reference: Hindcast mode). ``h0`` is the gauge elevation at ``start``."""
    state = initial_state(art, h0=h0, sm0=sm0, s_if0=s_if0, month=start.month)
    return run(art, state, rainfall_in, start, control_elev, dt)


def forecast(art: Artifact, end_state: State, scenario_rain_in: list[float], start: datetime,
             control_elev: float, dt: float = DT_HOURS) -> list[StepRecord]:
    """Project forward from a hindcast end-state under one rainfall scenario
    (Reference: Forecast mode). The lag pipeline is inherited, so the first ~4.6 h
    are still measurement-constrained."""
    _, records = run(art, end_state.copy(), scenario_rain_in, start, control_elev, dt)
    return records
