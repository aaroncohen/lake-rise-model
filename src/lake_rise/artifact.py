"""The versioned model artifact: every parameter the model needs, validated on
load. Kept human-readable JSON (spec 7) so a safety reviewer can audit it."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_ARTIFACT = Path(__file__).resolve().parents[2] / "artifacts" / "crystal_lake_v0.json"


class _ArtifactModel(BaseModel):
    """Base for every artifact model. ``validate_assignment`` re-validates (and coerces)
    on attribute set, so runtime parameter edits -- ``registry.set``, calibration
    scripts -- are type-checked instead of silently storing a wrong type (e.g. a string
    from the CLI, or a list where a tuple is declared). The tunable-seam's type-safety
    half; the range/tunability half lives in ``registry.check_write``."""
    model_config = ConfigDict(validate_assignment=True)


class HSPFParams(_ArtifactModel):
    INTFW: float
    LZSN_in: float
    INFILT_in_per_hr: float
    IRC_per_day: float
    CEPSC_in_per_storm: float
    LZETP: float
    # Active-groundwater (baseflow) limb. Till Forest values from Ecology WWHM
    # Appendix III-B; ranges from EPA BASINS Tech Note 6. Defaults let older artifacts
    # load. AGWRC is the daily recession ratio (t½ ≈ 173 d at 0.996); the hourly
    # equivalent is AGWRC**(1/24). KVARY adds nonlinear (storage-dependent) recession;
    # 0 keeps the store linear. DEEPFR is the only flux that leaves the basin for good.
    AGWRC_per_day: float = 0.996
    KVARY_per_in: float = 0.0
    DEEPFR: float = 0.05
    # Soil-bucket percolation to groundwater (HSPF-style), active below full saturation:
    # perc = PERC_coeff * INFILT * INFILD * (SM/LZSN)**INFEXP per hour. This is the only
    # groundwater recharge path; saturation-excess overflow goes 100% to fast interflow.
    # PERC_coeff is the calibration knob. INFILD/INFEXP are HSPF Till values.
    PERC_coeff: float = 0.1
    INFILD: float = 2.0
    INFEXP: float = 2.0


class StageArea(_ArtifactModel):
    slope: float
    intercept: float


class StageStorage(_ArtifactModel):
    a: float
    b: float
    c: float


class Geometry(_ArtifactModel):
    datum_base_elev_ft: float
    stage_area: StageArea
    stage_storage: StageStorage
    valid_elev_range_ft: tuple[float, float]


class Datum(_ArtifactModel):
    comment: str = ""
    sensor_to_absolute_offset_ft: float
    staff_to_absolute_offset_ft: float


class Watershed(_ArtifactModel):
    drainage_area_acres: float
    lag_hours: float


class StopLogSeason(_ArtifactModel):
    start: str
    end: str


class StopLogs(_ArtifactModel):
    rise_per_board_ft: float
    count_to_control_elev_ft: dict[str, float]
    season_installed: StopLogSeason

    def control_elev(self, count: int) -> float:
        return self.count_to_control_elev_ft[str(int(count))]


class SpillwayLeg(_ArtifactModel):
    control_elev_ft: float
    capacity_cfs_at_342: float
    # Physical crest length of the stop-log weir (ft). When present, the leg is modeled
    # as a rectangular weir Q = C * L * H**exponent with C derived from the rated
    # capacity; this makes the discharge coefficient physical and lets the capacity be
    # re-derived if the crest is raised by stop-logs. Optional for backward compatibility.
    crest_length_ft: float | None = None
    # Absolute elevation of the opening top (bridge soffit / conduit roof). Above it the
    # opening is fully submerged and the leg transitions from weir flow to a slower
    # submerged-orifice (sqrt-head) law. None = no ceiling modeled (pure weir).
    soffit_elev_ft: float | None = None
    # Absolute elevation of the bottom of the stop-log stack (the lowest seam). Seam
    # leakage occurs whenever water stands above this, up to the crest. None = no seam
    # leakage modeled for this leg.
    seam_bottom_elev_ft: float | None = None
    comment: str = ""


class Overtopping(_ArtifactModel):
    """Flow over the top of the dam once the lake exceeds the crest.

    The road/dam crest is not level. Per the Emergency Action Plan it sags to a low
    point ~25 ft east of the bridge, where overtopping first begins (``crest_elev_ft``),
    and rises to the bridge deck — the high point (``bridge_deck_elev_ft``) — which is
    only fully overtopped once the lake is ``bridge_deck_elev_ft - crest_elev_ft`` higher.
    The wetted crest therefore grows from a point at the low sag to the full
    ``crest_length_ft`` as the lake rises to the bridge deck: a gradual onset, not a
    full-length weir switching on at once.

    Modeled as a linearly-sloped (triangular) crest over that interval — the effective
    crest length grows linearly from 0 at ``crest_elev_ft`` to ``crest_length_ft`` at
    ``bridge_deck_elev_ft`` — with the weir law integrated over the submerged crest. If
    ``bridge_deck_elev_ft`` is None (or not above the low point) the whole crest spills
    as one flat broad-crested weir at ``crest_elev_ft`` (legacy behavior)."""
    crest_elev_ft: float
    # Bridge-deck (crest high-point) elevation. Above it the full crest is engaged. None
    # = flat crest (no sloped onset). EAP: overtopping starts at crest_elev_ft, bridge
    # deck is overtopped a fixed interval higher.
    bridge_deck_elev_ft: float | None = None
    crest_length_ft: float = 60.0
    weir_coeff: float = 2.6   # broad-crested weir coefficient C (US customary)
    comment: str = ""


class Leakage(_ArtifactModel):
    # Seepage through the stop-log seams, modeled as proportional to the seam width (the
    # stop-log crest length) and the submerged seam height (water height standing over the
    # seams, from the stack bottom up to the crest). The two "ft" are width and height.
    # Applies to every stop-log leg, continuously, including while water spills over the
    # top; calibrated so total seam leakage at the summer dry equilibrium is ~0.8 cfs.
    cfs_per_ft2: float = 0.0557
    cfs_low: float
    cfs_high: float
    comment: str = ""


class Spillway(_ArtifactModel):
    primary: SpillwayLeg
    auxiliary: SpillwayLeg
    rated_head_elev_ft: float
    weir_exponent: float = 1.5     # Q = capacity * (H/H_rated)**exponent (weir law)
    overtopping: Overtopping | None = None
    leakage: Leakage


class Thresholds(_ArtifactModel):
    early_warning: float
    dam_crest: float            # initial dam overtopping (crest low point / EAP bridge-closure)
    dam_crest_low: float
    # Bridge-deck overtopping: the crest high point, fully overtopped this much higher than
    # the low point. EAP: bridge deck overtopped -> "imminent failure", evacuate downstream.
    # Aligned with spillway.overtopping.bridge_deck_elev_ft. Optional for older artifacts.
    bridge_deck: float | None = None
    freeboard_alert_below_ft: float
    step6_peak: float


class Uncertainty(_ArtifactModel):
    comment: str = ""
    # 80% interval (10th/90th pct) of actual/forecast precip, by forecast lead day.
    lead_ratio_by_day: dict[str, tuple[float, float]]
    # Approximate forecast skill/confidence (%) by lead day (QPF threat-score decay).
    skill_confidence_by_day: dict[str, float]
    beyond_day7_ratio: tuple[float, float]
    beyond_day7_confidence: float
    # Per-month multiplier on the log-spread: ~1.0 cool-season frontal, >1 summer convective.
    season_spread_factor: dict[str, float]
    # Fraction of a NOAA high-end QPF total used to seed the MEDIAN storm total when the
    # automated point forecast is materially lower (#2 fix). f<1 keeps NOAA a high-end
    # anchor; 0.5 ~ the median/high ratio the day-2/3 band implies. Default for old artifacts.
    noaa_median_fraction: float = 0.5


class ValidationTargets(_ArtifactModel):
    step6_storm_total_in: float
    step6_storm_hours: float
    step6_peak_elev_ft: float
    step6_peak_tolerance_ft: float
    dry_equilibrium_3logs_ft: tuple[float, float]
    storm_peak_tolerance_ft: float
    storm_timing_tolerance_hr: float


class Artifact(_ArtifactModel):
    version: str
    description: str = ""
    hspf: HSPFParams
    monthly_pet_in: dict[str, float]
    watershed: Watershed
    geometry: Geometry
    datum: Datum
    stop_logs: StopLogs
    spillway: Spillway
    thresholds_abs_ft: Thresholds
    seasonal_sm_default_frac_of_lzsn: dict[str, float] = Field(default_factory=dict)
    seasonal_agw_default_in: dict[str, float] = Field(default_factory=dict)
    uncertainty: Uncertainty
    validation_targets: ValidationTargets

    def pet_for_month(self, month: int) -> float:
        return self.monthly_pet_in[str(int(month))]

    def seasonal_sm_default(self, month: int) -> float:
        """Default soil moisture (inches) to seed when rainfall history is too
        short to replay (Reference 1.5)."""
        frac = self.seasonal_sm_default_frac_of_lzsn.get(str(int(month)), 0.5)
        return frac * self.hspf.LZSN_in

    def seasonal_agw_default(self, month: int) -> float:
        """Default standing active-groundwater storage (inches) to seed by month. The
        173-day store can't be established from the short trailing-rainfall hindcast, so
        without a seed every prediction starts with zero baseflow (unphysical). Provisional
        magnitudes pending gauge calibration (brief §C)."""
        return self.seasonal_agw_default_in.get(str(int(month)), 0.0)


def load_artifact(path: str | Path | None = None) -> Artifact:
    """Load and validate a model artifact from JSON."""
    p = Path(path) if path is not None else DEFAULT_ARTIFACT
    data = json.loads(p.read_text())
    # Drop free-text comment keys that aren't part of the schema.
    for section in ("datum", "uncertainty", "seasonal_sm_default_frac_of_lzsn",
                    "seasonal_agw_default_in"):
        if isinstance(data.get(section), dict):
            data[section].pop("comment", None)
    return Artifact.model_validate(data)
