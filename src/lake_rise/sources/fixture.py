"""Fixture source: reads a snapshotted HA bundle from JSON (this milestone).

The snapshot is the wire format ``lake-rise pull`` writes and a future live HA REST
client would produce identically. Raw lake-depth readings are converted to absolute
elevation here using the artifact's datum offset, and forecast scenarios are
synthesized from the point QPF."""

from __future__ import annotations

import json
from pathlib import Path

from ..artifact import Artifact
from ..bundle import InputBundle
from ..scenarios import synthesize_scenarios


class FixtureSource:
    """Build an :class:`InputBundle` from a snapshot JSON file."""

    def __init__(self, art: Artifact, snapshot_path: str | Path):
        self.art = art
        self.snapshot_path = Path(snapshot_path)

    def build_bundle(self) -> InputBundle:
        snap = json.loads(self.snapshot_path.read_text())

        # Raw HA lake-depth reading -> absolute elevation (datum offset, open item #2).
        reading = snap["lake_depth_reading_ft"]
        abs_elev = reading + self.art.datum.sensor_to_absolute_offset_ft

        scenarios = synthesize_scenarios(
            self.art,
            point_forecast_in=snap.get("forecast_point_in", []),
            pop_frac=snap.get("forecast_pop_frac"),
            noaa_high_total_in=snap.get("noaa_high_total_in"),
        )

        return InputBundle(
            as_of=snap["as_of"],
            current_elevation_abs_ft=abs_elev,
            stop_log_count=snap.get("stop_log_count", 3),
            trailing_rainfall_in=snap.get("trailing_rainfall_in", []),
            forecast_scenarios=scenarios,
            rainfall_has_gaps=snap.get("rainfall_has_gaps", False),
        )
