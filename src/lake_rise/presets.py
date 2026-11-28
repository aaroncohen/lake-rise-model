"""Preset storms for the visualization page. Each builder returns an hourly
rainfall series (inches) given the artifact (some, like Step 6, derive from it)."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist
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


# The field view's example storm (/?example=storm), for showing the page to people: a storm
# already underway. It has rained for EXAMPLE_ELAPSED_H hours of a 48-hour atmospheric river
# (the likely storm's first hours, after EXAMPLE_DRY_BEFORE_H dry ones), so it is raining now and
# the lake is rising. The 50 ensemble members share that past and differ from now on: their storm
# totals sit at the quantiles of a bell curve around a likely 7.2 in (standard deviation 1.6 in),
# as a real ensemble clusters around its likely outcome, so the forecast dots bunch around the
# EAP alert line and thin out toward Warning and Overtop: a WATCH, raining about 0.24 in/hr with
# the lake rising about 0.03 ft/hr.
EXAMPLE_MEMBERS = 50
EXAMPLE_TOTAL_MEAN_IN, EXAMPLE_TOTAL_SD_IN = 7.2, 1.6   # 12% overtop: clear of the 10% WATCH line
EXAMPLE_RAIN_H, EXAMPLE_RAIN_PEAK_H = 48, 26
EXAMPLE_ELAPSED_H = 22
EXAMPLE_DRY_BEFORE_H = 72
EXAMPLE_HORIZON_H = 72
EXAMPLE_START_ELEVATION_ABS_FT = 339.0   # before the storm: about 0.6 ft on the stick
EXAMPLE_STOP_LOGS = 2                    # summer normal
EXAMPLE_PREVIOUS_SCALE = 0.8             # an hour earlier the forecast was this much smaller


def example_storm(scale: float = 1.0, elapsed_h: int = EXAMPLE_ELAPSED_H
                  ) -> tuple[list[float], list[list[float]]]:
    """The example storm ``elapsed_h`` hours in: the rain so far (hourly, in, oldest first) and
    each member's rain from now over ``EXAMPLE_HORIZON_H``, its storm total ``scale``d."""
    bell = NormalDist(EXAMPLE_TOTAL_MEAN_IN, EXAMPLE_TOTAL_SD_IN)
    totals = [max(0.0, bell.inv_cdf((j + 0.5) / EXAMPLE_MEMBERS)) for j in range(EXAMPLE_MEMBERS)]
    likely = _triangular(EXAMPLE_TOTAL_MEAN_IN, EXAMPLE_RAIN_H, EXAMPLE_RAIN_PEAK_H)
    fallen = [0.0] * EXAMPLE_DRY_BEFORE_H + likely[:elapsed_h]
    members = []
    for total in totals:
        rest = _triangular(scale * total, EXAMPLE_RAIN_H, EXAMPLE_RAIN_PEAK_H)[elapsed_h:]
        members.append((rest + [0.0] * EXAMPLE_HORIZON_H)[:EXAMPLE_HORIZON_H])
    return fallen, members
