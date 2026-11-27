"""The versioned model artifact: every parameter the model needs, validated on
load. Kept human-readable JSON (spec 7) so a safety reviewer can audit it."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

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
    # Interflow-generation exponent: a (SM/LZSN)**IFEXP share of rain sheds to interflow.
    # None keeps it at INFEXP (the percolation nonlinearity), as before it was split out.
    IFEXP: float | None = None

    @property
    def interflow_exp(self) -> float:
        return self.INFEXP if self.IFEXP is None else self.IFEXP


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


def _utc(t: datetime) -> datetime:
    """Naive times are UTC (the archive's hours), so naive and aware times compare."""
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


class SpillwayControl(NamedTuple):
    """The crest each spillway leg spills over (ft, absolute) for one stop-log setting. The two
    legs carry their own stop logs and are not always level with each other (pulling the aux
    log leaves the main spillway's base boards in), so the spillway needs both."""
    primary_ft: float
    auxiliary_ft: float

    @property
    def spill_start_ft(self) -> float:
        """The lake level where water first passes over the stop logs (the lower crest)."""
        return min(self.primary_ft, self.auxiliary_ft)


class StopLogLeg(_ArtifactModel):
    # Surveyed top of this leg's stop logs with "1 log" in (the EAP's count), stick ft.
    crest_one_log_stick_ft: float
    # Logs below the 1-log crest that come out as the count drops to 0: the aux spillway's
    # single log (1). The main spillway's base boards stay in at 0 (0).
    logs_below_one_log: int = 0
    # Boards under the 1-log crest that stay in at every count (bottom first): the main
    # spillway's base boards. No count pulls them; in an emergency all but the bottom
    # ``mortared_boards`` can come out (``Artifact.emergency_control``).
    base_board_heights_ft: list[float] = Field(default_factory=list)
    mortared_boards: int = 0

    def crest_stick_ft(self, count: int, log_height_ft: float) -> float:
        """Crest (stick ft) with ``count`` logs in: one log height per log from the 1-log crest,
        down to the lowest the count reaches on this leg."""
        n = max(int(count), 1 - self.logs_below_one_log)
        return self.crest_one_log_stick_ft + (n - 1) * log_height_ft

    def emergency_crest_stick_ft(self, log_height_ft: float) -> float:
        """Lowest crest reachable (stick ft): every log out, and every base board but the
        mortared ones."""
        return self.sill_stick_ft(log_height_ft) + sum(self.base_board_heights_ft[:self.mortared_boards])

    def sill_stick_ft(self, log_height_ft: float) -> float:
        """Bare sill (stick ft): every log and base board out."""
        return (self.crest_one_log_stick_ft - self.logs_below_one_log * log_height_ft
                - sum(self.base_board_heights_ft))


class StopLogs(_ArtifactModel):
    # Counted the EAP's way: "1 log" is the aux spillway's single log with the main spillway's
    # base boards, the two tops surveyed against the stick; each log added (or, on the aux,
    # removed) moves the crest by ``log_height_ft``. Heights are in stick ft; the artifact
    # converts them with the datum (``Artifact.spillway_control``).
    log_height_ft: float
    max_count: int
    primary: StopLogLeg
    auxiliary: StopLogLeg
    season_installed: StopLogSeason
    summer_count: int        # seasonal default in season (EAP "summer, normal stop logs")
    winter_count: int        # seasonal default off season (EAP "winter, normal stop logs")
    # Before this instant the operator helper (and the archive) counted boards in the main
    # spillway the old way, one more than the EAP's logs: those values are read as count - 1.
    # None: every recorded count is in logs.
    board_counts_before: datetime | None = None

    def logs_from_record(self, count: int | None, at: datetime) -> int | None:
        """A recorded stop-log count (helper history, archive, storm record) in logs: a count
        recorded before ``board_counts_before`` was in boards, one more than the logs."""
        if count is None:
            return None
        n = int(count)
        cut = self.board_counts_before
        if cut is not None and _utc(at) < _utc(cut):
            n = max(0, n - 1)
        return min(n, self.max_count)


class SpillwayLeg(_ArtifactModel):
    # The crest the documented capacity was rated over (absolute ft). Only the weir coefficient
    # comes from it (capacity = C * L * (rated head)**exponent); the leg spills over the
    # stop-log crest the count sets (``SpillwayControl``).
    rated_crest_elev_ft: float
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
    # the low point. EAP: bridge deck overtopped -> Emergency Level 3, "imminent failure".
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
    dry_equilibrium_summer_ft: tuple[float, float]
    storm_peak_tolerance_ft: float
    storm_timing_tolerance_hr: float


class LakeEvaporationModel(_ArtifactModel):
    """Site constants for estimating lake evaporation from recorded weather (``evaporation.py``);
    the coordinates also locate the NWS forecast grid cell (``sources/nws.py``)."""
    latitude_deg: float
    longitude_deg: float | None = None
    elevation_m: float
    timezone: str                 # local day for daily means / temperature range
    hargreaves_krs: float         # radiation from temperature range (0.16 interior .. 0.19 coastal)
    comment: str = ""


class BaseflowAssimilation(_ArtifactModel):
    """Correct the replayed groundwater store from the lake's own water balance
    (``antecedent.assimilate_baseflow``): over the ``window_days`` before T0 the lake-implied
    watershed inflow, less the model's quickflow, is the observed baseflow, and the store is
    scaled so the model's baseflow matches it. Skipped (the pure replay stands) when the window
    is too wet, the lake is near the spillway crest, the estimate is too noisy, or the model has
    too little baseflow to scale."""
    window_days: int
    min_days: int                    # 24 h blocks with enough gauge readings
    min_hours_per_day: int           # gauge readings a block needs
    max_window_rain_in: float        # quickflow from more rain than this would leak into the fit
    max_model_quickflow_cfs: float   # ...and so would a window the model gives real storm runoff
    max_se_cfs: float                # standard error of the observed inflow
    min_model_baseflow_cfs: float    # below this there is nothing meaningful to scale
    scale_min: float
    scale_max: float
    crest_margin_ft: float           # every block median must sit this far below the crest
    comment: str = ""


class DeepZone(_ArtifactModel):
    """The deep zone between the soil bucket and the groundwater store (``model.m8_deep_zone``):
    percolation refills it before recharging groundwater, and deep roots draw it down for the
    ET demand the soil can't meet. Seeded each month at ``capacity_in`` times the soil's
    seasonal wetness fraction (``seasonal_sm_default_frac_of_lzsn``)."""
    capacity_in: float               # inches over the watershed
    et_coeff: float                  # share of the soil's unmet ET demand it supplies when full
    gate_exp: float                  # recharge = perc * (S_dz/capacity)**gate_exp
    comment: str = ""


class EnsembleDayCalibration(_ArtifactModel):
    """One lead day's correction of the ensemble members' rain, in square-root space
    (``scenarios.calibrate_members``): z' = mean_scale*zbar + spread_scale*(z - zbar)
    + added_spread*zbar*u, with u the member's rank as a standard-normal quantile."""
    mean_scale: float
    spread_scale: float
    added_spread: float


class EnsembleRain(_ArtifactModel):
    """The archived ensemble rain forecast (``qpf_archive``) that drives the live forecast when a
    recent run is on hand (``scenarios.ensemble_members``); otherwise the point forecast's
    synthetic band (``uncertainty``) stands in."""
    source: str                      # qpf_archive source name
    max_run_age_h: float             # older runs are ignored
    # By lead day from the run ("1", "2", ...); later days reuse the last one.
    calibration_by_day: dict[str, EnsembleDayCalibration]
    # Forecast agreement (the confidence shown): the share of members whose lake peak is
    # within this of the middle case's.
    agreement_tolerance_ft: float = 0.1
    comment: str = ""


class LakeFilter(_ArtifactModel):
    """The live lake reading's Kalman filter (``lake_filter.estimate_lake``): level, inflow
    scale, residual rate and a persistent gauge offset, stepped each minute. Every ``*_sd`` is a
    standard deviation per one-minute step."""
    history_hours: int
    noise_sd_ft: float                  # white part of the reading error
    offset_sd_ft: float                 # persistent offset (stationary sd)
    offset_minutes: float               # its AR(1) time constant
    level_sd_ft: float
    scale_sd: float                     # random walk of the inflow scale
    rate_sd_dry_ft_per_min: float       # random walk of the residual rate, dry
    rate_sd_wet_ft_per_min: float       # ... fully wet
    init_scale_sd: float
    init_rate_sd_ft_per_h: float
    scale_max: float
    gate_sigmas: float                  # innovations beyond this many sigmas are down-weighted
    wet_window_hours: float
    wet_full_in: float                  # rain over the window that makes it fully wet
    min_valid_minutes_last_30: int
    comment: str | None = None


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
    # Open-water evaporation off the lake surface, inches/day by month (empty -> none).
    lake_evaporation_in_per_day: dict[str, float] = Field(default_factory=dict)
    lake_evaporation_model: LakeEvaporationModel | None = None
    baseflow_assimilation: BaseflowAssimilation | None = None
    # None -> no deep zone: percolation recharges groundwater directly.
    deep_zone: DeepZone | None = None
    # None -> the live forecast always uses the point forecast's synthetic band.
    ensemble_rain: EnsembleRain | None = None
    # None -> the live reading is the 30-min median everywhere.
    lake_filter: LakeFilter | None = None
    uncertainty: Uncertainty
    validation_targets: ValidationTargets

    def spillway_control(self, count: int) -> SpillwayControl:
        """Both legs' crests (absolute ft) with ``count`` stop logs in (the EAP's count)."""
        sl = self.stop_logs
        n = int(count)
        if not 0 <= n <= sl.max_count:
            raise ValueError(f"stop-log count {count} outside 0-{sl.max_count}")
        off = self.datum.sensor_to_absolute_offset_ft
        return SpillwayControl(round(off + sl.primary.crest_stick_ft(n, sl.log_height_ft), 3),
                               round(off + sl.auxiliary.crest_stick_ft(n, sl.log_height_ft), 3))

    def spill_start_elev(self, count: int) -> float:
        """Where water first passes over the stop logs (absolute ft) with ``count`` logs in."""
        return self.spillway_control(count).spill_start_ft

    def emergency_control(self) -> SpillwayControl:
        """Both legs at their lowest reachable crests: the aux log out and the main spillway's
        removable base boards out (the EAP's "remove the remaining stop logs")."""
        sl = self.stop_logs
        off = self.datum.sensor_to_absolute_offset_ft
        return SpillwayControl(round(off + sl.primary.emergency_crest_stick_ft(sl.log_height_ft), 3),
                               round(off + sl.auxiliary.emergency_crest_stick_ft(sl.log_height_ft), 3))

    def rated_control(self) -> SpillwayControl:
        """The crests the documented capacities were rated over: the regulatory design study's
        spillway, which the Step 6 anchor reproduces."""
        sp = self.spillway
        return SpillwayControl(sp.primary.rated_crest_elev_ft, sp.auxiliary.rated_crest_elev_ft)

    def pet_for_month(self, month: int) -> float:
        return self.monthly_pet_in[str(int(month))]

    def lake_evap_for_month(self, month: int) -> float:
        """Open-water evaporation from the lake surface, inches/day (0 if not configured)."""
        return self.lake_evaporation_in_per_day.get(str(int(month)), 0.0)

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

    def seasonal_dz_default(self, month: int | None) -> float:
        """Default deep-zone storage (inches) to seed by month: the capacity times the soil's
        seasonal wetness fraction (full in winter, near empty in late summer). Full without a
        month, and 0 with no deep zone configured."""
        if self.deep_zone is None:
            return 0.0
        if month is None:
            return self.deep_zone.capacity_in
        return self.deep_zone.capacity_in * self.seasonal_sm_default(month) / self.hspf.LZSN_in


def load_artifact(path: str | Path | None = None) -> Artifact:
    """Load and validate a model artifact from JSON."""
    p = Path(path) if path is not None else DEFAULT_ARTIFACT
    data = json.loads(p.read_text())
    # Drop free-text comment keys that aren't part of the schema.
    for section in ("datum", "uncertainty", "seasonal_sm_default_frac_of_lzsn",
                    "seasonal_agw_default_in", "lake_evaporation_in_per_day"):
        if isinstance(data.get(section), dict):
            data[section].pop("comment", None)
    return Artifact.model_validate(data)
