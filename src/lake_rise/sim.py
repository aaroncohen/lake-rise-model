"""Simulation harness: build InputBundles from explicit synthetic conditions and
forecasts, so model behavior can be validated without touching Home Assistant
("feed it simulated current conditions and forecasts" — user requirement).

Also a small library of canonical storm shapes used by the validation anchors."""

from __future__ import annotations

from datetime import datetime

from .artifact import Artifact
from .bundle import InputBundle, ScenarioRain


# --- storm shape generators -------------------------------------------------------

def constant_storm(rate_in_per_hr: float, hours: int) -> list[float]:
    return [rate_in_per_hr] * hours


def dry(hours: int) -> list[float]:
    return [0.0] * hours


def step6_hyetograph(art: Artifact) -> list[float]:
    """The regulatory Step 6 design storm as an hourly series: total 10.27 in over
    72 h, triangular, peaking near hour 39 (Reference 1.9). Calibration anchor."""
    total = art.validation_targets.step6_storm_total_in
    hours = int(art.validation_targets.step6_storm_hours)
    peak = 39
    weights = [(t + 1) / peak if (t + 1) <= peak else (hours - t) / (hours - peak)
               for t in range(hours)]
    s = sum(weights)
    return [w / s * total for w in weights]


# --- simulated source -------------------------------------------------------------

class SimulatedSource:
    """An in-memory DataSource built from explicit synthetic inputs."""

    def __init__(
        self,
        as_of: datetime,
        current_elevation_abs_ft: float,
        stop_log_count: int,
        scenarios: dict[str, list[float]] | None = None,
        trailing_rainfall_in: list[float] | None = None,
        initial_sm_in: float | None = None,
        initial_s_if_in: float = 0.0,
        rainfall_has_gaps: bool = False,
    ):
        self.as_of = as_of
        self.current_elevation_abs_ft = current_elevation_abs_ft
        self.stop_log_count = stop_log_count
        self.scenarios = scenarios or {}
        self.trailing_rainfall_in = trailing_rainfall_in or []
        self.initial_sm_in = initial_sm_in
        self.initial_s_if_in = initial_s_if_in
        self.rainfall_has_gaps = rainfall_has_gaps

    @classmethod
    def single_storm(cls, as_of: datetime, current_elevation_abs_ft: float, stop_log_count: int,
                     storm_in: list[float], **kwargs) -> "SimulatedSource":
        """Drive all three scenarios with the same series (deterministic anchor run)."""
        return cls(as_of, current_elevation_abs_ft, stop_log_count,
                   scenarios={"low": storm_in, "median": storm_in, "high": storm_in}, **kwargs)

    def build_bundle(self) -> InputBundle:
        return InputBundle(
            as_of=self.as_of,
            current_elevation_abs_ft=self.current_elevation_abs_ft,
            stop_log_count=self.stop_log_count,
            trailing_rainfall_in=self.trailing_rainfall_in,
            forecast_scenarios=[ScenarioRain(name=n, hourly_in=h) for n, h in self.scenarios.items()],
            initial_sm_in=self.initial_sm_in,
            initial_s_if_in=self.initial_s_if_in,
            rainfall_has_gaps=self.rainfall_has_gaps,
        )
