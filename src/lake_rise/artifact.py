"""The versioned model artifact: every parameter the model needs, validated on
load. Kept human-readable JSON (spec 7) so a safety reviewer can audit it."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_ARTIFACT = Path(__file__).resolve().parents[2] / "artifacts" / "crystal_lake_v0.json"


class HSPFParams(BaseModel):
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


class StageArea(BaseModel):
    slope: float
    intercept: float


class StageStorage(BaseModel):
    a: float
    b: float
    c: float


class Geometry(BaseModel):
    datum_base_elev_ft: float
    stage_area: StageArea
    stage_storage: StageStorage
    valid_elev_range_ft: tuple[float, float]


class Datum(BaseModel):
    comment: str = ""
    sensor_to_absolute_offset_ft: float
    staff_to_absolute_offset_ft: float


class Watershed(BaseModel):
    drainage_area_acres: float
    lag_hours: float


class StopLogSeason(BaseModel):
    start: str
    end: str


class StopLogs(BaseModel):
    rise_per_board_ft: float
    count_to_control_elev_ft: dict[str, float]
    season_installed: StopLogSeason

    def control_elev(self, count: int) -> float:
        return self.count_to_control_elev_ft[str(int(count))]


class SpillwayLeg(BaseModel):
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


class Overtopping(BaseModel):
    """Flow over the top of the dam once the lake exceeds the crest: the whole crest
    acts as one long broad-crested weir, Q = weir_coeff * crest_length * head**exponent."""
    crest_elev_ft: float
    crest_length_ft: float = 60.0
    weir_coeff: float = 2.6   # broad-crested weir coefficient C (US customary)
    comment: str = ""


class Leakage(BaseModel):
    # Seepage through the stop-log seams, modeled as proportional to the seam width (the
    # stop-log crest length) and the submerged seam height (water height standing over the
    # seams, from the stack bottom up to the crest). The two "ft" are width and height.
    # Applies to every stop-log leg, continuously, including while water spills over the
    # top; calibrated so total seam leakage at the summer dry equilibrium is ~0.8 cfs.
    cfs_per_ft2: float = 0.0557
    cfs_low: float
    cfs_high: float
    comment: str = ""


class Spillway(BaseModel):
    primary: SpillwayLeg
    auxiliary: SpillwayLeg
    rated_head_elev_ft: float
    weir_exponent: float = 1.5     # Q = capacity * (H/H_rated)**exponent (weir law)
    overtopping: Overtopping | None = None
    leakage: Leakage


class Thresholds(BaseModel):
    early_warning: float
    dam_crest: float
    dam_crest_low: float
    freeboard_alert_below_ft: float
    step6_peak: float


class Uncertainty(BaseModel):
    comment: str = ""
    # 80% interval (10th/90th pct) of actual/forecast precip, by forecast lead day.
    lead_ratio_by_day: dict[str, tuple[float, float]]
    # Approximate forecast skill/confidence (%) by lead day (QPF threat-score decay).
    skill_confidence_by_day: dict[str, float]
    beyond_day7_ratio: tuple[float, float]
    beyond_day7_confidence: float
    # Per-month multiplier on the log-spread: ~1.0 cool-season frontal, >1 summer convective.
    season_spread_factor: dict[str, float]


class ValidationTargets(BaseModel):
    step6_storm_total_in: float
    step6_storm_hours: float
    step6_peak_elev_ft: float
    step6_peak_tolerance_ft: float
    dry_equilibrium_3logs_ft: tuple[float, float]
    storm_peak_tolerance_ft: float
    storm_timing_tolerance_hr: float


class Artifact(BaseModel):
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
