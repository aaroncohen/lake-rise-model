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
        "light_rain", "Light rain",
        "~0.5 in over 12 h — a routine wet-season shower.",
        lambda art: _triangular(0.5, 12, 6)),
    "moderate_storm": StormPreset(
        "moderate_storm", "Moderate storm",
        "~1.5 in over 24 h — a typical wet-season frontal day.",
        lambda art: _triangular(1.5, 24, 12)),
    "heavy_storm": StormPreset(
        "heavy_storm", "Heavy storm",
        "~3 in over 36 h — a strong frontal system.",
        lambda art: _triangular(3.0, 36, 18)),
    "atmospheric_river": StormPreset(
        "atmospheric_river", "Atmospheric river",
        "~5.5 in over 48 h — a strong atmospheric river hitting the lowlands.",
        lambda art: _triangular(5.5, 48, 26)),
    "hundred_year": StormPreset(
        "hundred_year", "100-yr storm",
        "~7.5 in over 72 h — roughly a 1%-annual-chance (100-yr) multi-day regional total; "
        "outcome depends heavily on antecedent soil moisture (docs §2.2).",
        lambda art: _triangular(7.5, 72, 40)),
    "step6_design": StormPreset(
        "step6_design", "10,000-yr storm (regulatory IDF)",
        "1-in-10,000-yr design storm (Step 6 IDF): 10.27 in / 72 h on a saturated watershed — "
        "the model's extreme anchor (~343.1 ft peak).",
        lambda art: sim.step6_hyetograph(art)),
}


def build_storm(art: Artifact, key: str) -> list[float]:
    if key not in STORM_PRESETS:
        raise KeyError(key)
    return STORM_PRESETS[key].builder(art)
