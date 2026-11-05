"""The normalized input bundle: the predictor's only input. Built identically by
the fixture source, the (future) live HA source, and the simulator, so every code
path exercises the same pure predictor (spec 6)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ScenarioRain(BaseModel):
    """One rainfall scenario over the forecast horizon (hourly inches)."""
    name: str                       # "low" | "median" | "high"
    hourly_in: list[float]


class SpinupState(BaseModel):
    """The watershed's internal state at ``as_of``, already spun up by the caller (the live
    source replays the archived true rain -- ``antecedent.replay_subsurface``). Lake elevation
    is not part of it: the predictor always starts from the measured gauge."""
    sm_in: float
    s_if_in: float
    s_agw_in: float
    lag_pipe_cfs: list[float]       # in-flight interflow, oldest first
    canopy_remaining_in: float
    hours_since_rain: float
    source: str = "archive_replay"
    history_days: float | None = None
    # The factor the groundwater store was scaled by to match the lake's measured water balance
    # (``antecedent.assimilate_baseflow``); None when the pure replay stands.
    baseflow_scale: float | None = None


class InputBundle(BaseModel):
    """Everything the predictor needs, in model units (absolute ft, inches, hourly)."""
    as_of: datetime
    current_elevation_abs_ft: float
    stop_log_count: int = Field(ge=0, le=3)

    # Trailing observed hourly rainfall (oldest -> newest), ending at ``as_of``.
    trailing_rainfall_in: list[float] = Field(default_factory=list)

    # Forecast scenarios (low / median / high), each hourly from ``as_of`` forward.
    forecast_scenarios: list[ScenarioRain] = Field(default_factory=list)

    # Optional explicit starting model state (used by the simulator); None -> spin up / seasonal.
    initial_sm_in: float | None = None
    initial_s_if_in: float = 0.0

    # Optional full spun-up state from recorded history (live source). When set (and no
    # simulator initial_sm_in), the predictor starts from it instead of re-running the short
    # seasonal-seed hindcast over trailing_rainfall_in.
    spinup_state: SpinupState | None = None

    # Degraded-data signal (spec 2.2): the trailing rainfall record is sparse/gappy
    # (recorder outage) and/or the live lake gauge is stale -- either degrades the
    # state estimate. Gappy rain biases the hindcast dry, so when this is set the
    # predictor floors the spun-up SM/AGW at the month's climatological seed (#4) and
    # the result is flagged not-fresh.
    rainfall_has_gaps: bool = False

    @property
    def horizon_hours(self) -> int:
        if not self.forecast_scenarios:
            return 0
        return max(len(s.hourly_in) for s in self.forecast_scenarios)
