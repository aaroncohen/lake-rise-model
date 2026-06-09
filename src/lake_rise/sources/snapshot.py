"""The snapshot wire format and its conversion to an InputBundle. This single
function is the only place a snapshot becomes model input, so the fixture source,
the live HA source, and the API all share identical preprocessing (spec 2 skew guard)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..artifact import Artifact
from ..bundle import InputBundle
from ..scenarios import synthesize_scenarios


class Snapshot(BaseModel):
    """Normalized HA pull. Raw lake reading + raw hourly arrays; conversion to model
    units happens in ``bundle_from_snapshot``. Extra keys (_comment, _provenance) ignored."""
    model_config = {"extra": "ignore"}

    as_of: str
    lake_depth_reading_ft: float
    stop_log_count: int = Field(3, ge=0, le=3)
    trailing_rainfall_in: list[float] = Field(default_factory=list)
    rainfall_has_gaps: bool = False
    forecast_point_in: list[float] = Field(default_factory=list)
    forecast_pop_frac: list[float] | None = None
    noaa_high_total_in: float | None = None


def bundle_from_snapshot(art: Artifact, snap: Snapshot | dict) -> InputBundle:
    """Convert a snapshot to an InputBundle: apply the datum offset to the raw lake
    reading and synthesize low/median/high scenarios from the point QPF."""
    if isinstance(snap, dict):
        snap = Snapshot.model_validate(snap)

    abs_elev = snap.lake_depth_reading_ft + art.datum.sensor_to_absolute_offset_ft
    month = datetime.fromisoformat(snap.as_of).month
    scenarios = synthesize_scenarios(
        art,
        point_forecast_in=snap.forecast_point_in,
        month=month,
        pop_frac=snap.forecast_pop_frac,
        noaa_high_total_in=snap.noaa_high_total_in,
    )
    return InputBundle(
        as_of=snap.as_of,
        current_elevation_abs_ft=abs_elev,
        stop_log_count=snap.stop_log_count,
        trailing_rainfall_in=snap.trailing_rainfall_in,
        forecast_scenarios=scenarios,
        rainfall_has_gaps=snap.rainfall_has_gaps,
    )
