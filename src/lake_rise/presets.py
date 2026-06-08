"""Preset storms for the visualization page. Each builder returns an hourly
rainfall series (inches) given the artifact (some, like Step 6, derive from it)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import sim
from .artifact import Artifact


def _triangular(total_in: float, hours: int, peak_hour: int) -> list[float]:
    """A triangular hyetograph peaking at peak_hour, scaled to total_in."""
    w = [(t + 1) / peak_hour if (t + 1) <= peak_hour else (hours - t) / (hours - peak_hour)
         for t in range(hours)]
    s = sum(w) or 1.0
    return [x / s * total_in for x in w]


@dataclass
class StormPreset:
    key: str
    label: str
    description: str
    builder: Callable[[Artifact], list[float]]


STORM_PRESETS: dict[str, StormPreset] = {
    "dry": StormPreset(
        "dry", "Dry spell", "No rain for 72 h — watch recession toward the control elevation.",
        lambda art: sim.dry(72)),
    "light_rain": StormPreset(
        "light_rain", "Light rain", "0.05 in/hr for 12 h (~0.6 in), then dry.",
        lambda art: sim.constant_storm(0.05, 12) + sim.dry(60)),
    "moderate_storm": StormPreset(
        "moderate_storm", "Moderate storm", "0.10 in/hr for 24 h (~2.4 in), then dry.",
        lambda art: sim.constant_storm(0.10, 24) + sim.dry(48)),
    "heavy_storm": StormPreset(
        "heavy_storm", "Heavy storm", "0.30 in/hr for 24 h (~7.2 in), then dry.",
        lambda art: sim.constant_storm(0.30, 24) + sim.dry(48)),
    "atmospheric_river": StormPreset(
        "atmospheric_river", "Atmospheric river", "~6 in over 48 h, peaking mid-event.",
        lambda art: _triangular(6.0, 48, 24) + sim.dry(24)),
    "step6_design": StormPreset(
        "step6_design", "Step 6 design storm", "Regulatory IDF: 10.27 in / 72 h (saturate the watershed).",
        lambda art: sim.step6_hyetograph(art)),
}


def build_storm(art: Artifact, key: str) -> list[float]:
    if key not in STORM_PRESETS:
        raise KeyError(key)
    return STORM_PRESETS[key].builder(art)
