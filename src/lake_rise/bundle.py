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
