# Calibration & field-observations log

This file is the **source of truth for why each calibrated parameter has the value it
does** (the prose "why"), grounded in field measurements and gauge-derived analysis. The
research basis is [`baseflow_parameter_research_brief.md`](../baseflow_parameter_research_brief.md);
the values themselves live in [`artifacts/crystal_lake_v0.json`](../artifacts/crystal_lake_v0.json)
(each has an inline `*_comment` with its provenance). Keep those comments and this log in
sync.

Its **machine-readable companion** is
[`artifacts/parameter_registry.json`](../artifacts/parameter_registry.json) (the structured
`class` / `tunable` / range / `prior` / `couples_with` metadata this table describes in prose).
Run `.venv/bin/lake-rise params --tunable` to see which parameters are eligible for local
fitting, or `--class provisional` to see the weakly-grounded ones. `tests/test_registry.py`
enforces that the registry stays complete and in sync with the artifact — when you add a
parameter, classify it there too, or the completeness test fails.

**Automated calibration (`lake-rise calibration`, advisory + human-approved).** The three
`auto_tunable` subsurface parameters (`PERC_coeff`, `AGWRC_per_day`, `leakage.cfs_per_ft2`) can be
re-fit from **hydrological signatures** — AGWRC from rain-free recessions (Vogel & Kroll), PERC
from the Wolock BFI ≈ 0.67 target, leakage from the dry-equilibrium anchor — over a rolling
continuous record (`data/continuous`). Calibration is **unbiased**; the dam-safety asymmetry is an
acceptance *veto* (reject anything that worsens peak under-prediction / late timing), plus anchors
as a hard constraint and regularization toward the registry `prior`s. Every proposal is graded by
data sufficiency and emailed for approval; approval writes a new `artifacts/versions/crystal_lake_vN.json`
and is revertible. **Nothing auto-applies, and the canonical `v0` is unchanged.** When a real
proposal is approved, record the reasoning here and update the parameter table + artifact comment
as usual.

> **Maintain this file.** When you (human or agent) change a calibrated parameter, make a
> field/gauge observation, or resolve an open item, add a dated entry below and update the
> parameter table. Calibration here is grounded in real observations — do not silently
> re-tune a value away from a gauge-anchored number without recording why.

---

## Calibrated parameters & provenance

| Parameter (in `crystal_lake_v0.json`) | Value | Basis | Confidence |
|---|---|---|---|
| `hspf.AGWRC_per_day` | **0.97** (~23-day half-life) | Moderate subsurface store. The Ecology Till nominal 0.996 (~173 d) returned ~nothing within an event window, so soil drainage routed there was lost to the near-term budget and the modeled lake drew down after rain while the gauge held/rose. Within EPA Tech Note 6 typical 0.92–0.99. | reasoned; gauge-consistent |
| `hspf.PERC_coeff` | **0.31** (~3× HSPF nominal) | **Calibrated to the 2026-06-12 gauge recession** (~3.7 cfs sustained baseflow; at 0.10 the model gave ~2.5 cfs and fell). Re-derived 0.20→0.31 on 2026-07-03 when the #3 wetness-driven interflow freed the flood-peak margin that had pinned it at ~0.20; the gauge target (3.7 cfs) is held, the parameter moved to keep hitting it. | gauge-calibrated (1 event, 1 season) |
| `spillway.leakage.cfs_per_ft2` | **0.040** (~0.6 cfs at dry recession) | Trimmed from 0.0557 (~0.8 cfs), which over-attributed dry-season outflow now that a standing baseflow inflow is modeled. Inside the documented 0.5–2.0 cfs bound. | reasoned |
| `seasonal_agw_default_in` | seasonal table | Standing-baseflow seed so the slow store isn't empty at the start of every prediction (the short hindcast can't charge a multi-week store). Provisional magnitudes from a recharge-fraction estimate; the **seed sets the baseline, not the post-rain trend** (that's `PERC_coeff`). | provisional |
| `hspf.IRC_per_day` | 0.5 | Brief Till value. Fast interflow, ~1-day half-life. **Note:** higher IRC = *faster* drain in this model (`1-(1-IRC)^(dt/24)`). | brief |
| `spillway` weir law | exponent **1.5**, C≈1.9 | **Corroborated** — both legs independently imply the same C≈1.9; enforced by `test_m6_physical_crest_lengths_corroborate_reported_capacity`. Do **not** change the exponent to reduce spill without strong cause; it breaks that corroboration. | corroborated |
| `spillway.overtopping.bridge_deck_elev_ft` | **342.7** (= crest 342.2 + 0.50 ft) | **EAP-grounded.** The crest sags to a low point ~25 ft east of the bridge (overtopping starts) and rises 0.50 ft to the bridge deck (full crest engaged). Low point anchored to the established dam-crest 342.2; the **0.50 ft sag→bridge interval** is the EAP's solid *relative* number (sensor 3.90→4.40 ft). Effective weir length grows linearly 0→60 ft over that interval. | EAP-grounded (relative); absolute tied to dam-crest |
| `spillway.auxiliary.control_elev_ft` | 340.0 | Geometry (338.8 sill + board stack). Relative-to-primary spacing confirmed by field (2026-06-11). Absolute value not independently pinned. | reasoned; field-consistent |
| `datum.sensor_to_absolute_offset_ft` | 338.375 | **Unconfirmed within ~0.12 ft.** Two soft anchors disagree (see 2026-06-11 entry). Left unchanged pending a clean crest-crossing reading. | provisional / watch for drift |

---

## Field & gauge observations

### 2026-06-11 — dam tape-down (field visit)
**Observed:** staff stick read ~1.5 ft; HA gauge averaging ~1.5 ft (the two roughly in sync,
though the sensor has been noisy); **3 stop logs** in place; a **continuous ~1-inch stream
over the primary** stop logs and a **non-contiguous trickle over the aux** board.

**What it validated:**
- **Spillway geometry is sound.** The aux being higher than the primary (it was barely
  topping out while the primary streamed) and passing less (also narrower: 7.5 ft vs 10 ft
  crest) is consistent with the modeled relative heights. Boards/count check out.
- **Sensor offset is *not* confirmed.** Two anchors disagree by ~0.12 ft: the old
  "summer-normal = 1.3 ft ↔ 3-log crest 339.675" helper implies offset ≈ 338.375 (current),
  while "aux barely trickling = lake at the aux crest 340.0, gauge 1.5" implies ≈ 338.5. That
  gap is either field-read noise or ~0.12 ft of sensor drift since the helper was set. **Not
  changed.** The stick/gauge agreeing in the field argues against gross drift.

**To pin the offset cleanly (do this next time at the dam):** record the gauge reading at the
*instant* a crest just starts or stops spilling (the aux trickle stopping is ideal). That is a
direct crest crossing: `offset = crest_elev − gauge`, no head-estimation. A few of these over
time also directly measure drift.

### 2026-06-12 — gauge recession (the post-rain "rise")
**Observed (8 days of HA depth history, heavily smoothed; sensor noisy):** last rain
2026-06-10 (2.89 in over the 8 days); 66 h later the lake was at ~339.91 ft abs, **spilling
~0.24 ft over the 3-log crest, and still rising at ~0.2 in/day**.

**Analysis:** rise rate × surface area ⇒ ~+0.8 cfs net; at the modeled spillway outflow
(~2.9 cfs) that means the watershed was delivering **~3.7 cfs sustained, 66 h after rain**.
This is a normal baseflow recession tail (~0.04 in/day of yield), **self-limiting** (plateaus
~339.97 as spill catches up). It is **not** a mystery inflow source and **not** the basin lag
— in the model the fast interflow is fully drained (0 cfs) by this point, so the 4.6 h
translation lag is irrelevant. A translation lag delays the response; it cannot *sustain* a
rise. Running the model over the actual rain confirmed it: at `PERC_coeff=0.1` the model
delivered ~2.5 cfs and the lake *fell*, while the gauge rose.

**Action:** `PERC_coeff 0.10 → 0.20` → model baseflow 3.70 cfs (= observed), level 339.93
(obs 339.91), trend flips to rising. Anchors held (Step 6 **342.78**, dry-eq 339.67).

### 2026-06-12 — gauge noise & smoothing conventions
The `sensor.crystal_lake_depth_smoothed` gauge is **heavily noisy** ("noisy lately"): it swings
**0.6–1.4 in within a single hour**, and that's the *already-smoothed* HA entity. It's symmetric
noise (mean ≈ median ≈ trimmed-mean), not spikes or quantization; ~20–30 samples/hr leaves the
per-hour central value with ~0.2 in sampling error. Conventions adopted to keep this out of the
model without hiding real movement (see `backtest.py` / `live_ha.py`):

- **Hourly aggregation = per-hour MEDIAN** (`level_history_to_hourly`), not last-in-hour. Last-in-hour
  threw away ~22 of 23 samples and made the plotted line ~1.8× noisier than the signal.
- **Live "now" anchor = 30-min trailing median** (`LIVE_ANCHOR_WINDOW_HOURS = 0.5`). A trailing
  median lags a monotonic rise by ~half the window; 30 min is the knee of the noise-vs-lag curve
  (~0.21 in residual, ~15 min worst-case alarm lag). **Do not lengthen it** without re-checking the
  severe-storm time-to-alarm cost (a 1 h window ≈ 30 min lag).
- **Backtest displayed actual = centered 3-h median** (`_centered_median_smooth`, zero lag — the
  backtest is historical). **Display only:** metrics (RMSE, peak error) are computed on the raw
  per-hour-median gauge, because a centered median softens true storm peaks and would flatter the
  model on exactly the comparison the backtest exists to make.

### 2026-06-13 — sloped overtopping crest from the Emergency Action Plan
**New source:** the Crystal Lake EAP gives the overtopping geometry in gauge readings
(sensor frame, +338.375):

- **3.30 ft** (~341.68 abs) — "water up two feet" → Mandatory Alert (DSO/SMO/RCEC), begin sandbagging.
- **3.90 ft** (~342.28 abs) — **overtopping starts, 25 ft east of the bridge**; close the bridge.
- **4.40 ft** (~342.78 abs) — **bridge deck just overtopped**; "imminent failure" → evacuate. Failure
  criterion is overtopping by 6 in for 12 h (~343.2 abs sustained).

**What it tells us:** the road/dam crest is **not level**. Overtopping begins at a low sag and the
deck (the high point) is overtopped only **~0.50 ft higher** (4.40 − 3.90). The old model switched the
whole 60 ft crest on at once at 342.2 — too much relief, too abruptly.

**Change:** added `overtopping.bridge_deck_elev_ft` and modeled overtopping as a **linearly-sloped
(triangular) weir**: the wetted crest grows from a point at the sag (`crest_elev_ft`) to the full
`crest_length_ft` at the bridge deck, so `Q = C·β·[(h−z_low)^2.5 − max(0,h−z_top)^2.5]/2.5` with
β = 60/0.5 = 120 ft/ft. Onset is much gentler (e.g. ~22 cfs vs the old ~68 cfs at the bridge-deck
level). The **low point is anchored to the established dam-crest 342.2** (the EAP's *absolute*
readings carry the same ~0.12 ft datum uncertainty as the sensor offset — see open item), and only
the EAP's **relative** 0.50 ft interval is used → `bridge_deck_elev_ft = 342.7`.

**Anchors:** less near-crest relief raises the Step 6 saturated peak **342.78 → 342.92 ft** (toward the
343.1 ± 0.5 target center; more margin above the 342.6 floor). Dry-eq unchanged (339.67). `pytest` green;
`test_m6_overtopping_*` rewritten for the sloped onset (the old "1 ft over the crest sheds >150 cfs"
assertion encoded the flat-weir model and no longer holds).

**Open:** a clean crest-crossing datum read (open item below) would let us place the *absolute* sag/
deck elevations directly instead of inheriting them from the dam-crest anchor. The linear length-growth
also assumes a ~constant crest grade (0.5 ft / 25 ft ≈ 2%); a crest survey would refine the profile.

**Follow-on (same day): bridge-deck made a first-class threshold end-to-end.** Added
`thresholds_abs_ft.bridge_deck = 342.7` (mirrors `overtopping.bridge_deck_elev_ft`). It now flows
through the prediction (`p_cross_bridge_deck`, per-scenario `hours_to_bridge_deck`), the UI (chart
line, risk stat, scenario column), and the notifications. The default alert ladder gained a top
`EVACUATE:bridge_deck:0.30:evacuate` level — the EAP "imminent failure" / evacuate-downstream
trigger (NORCOM/KCDOT) above the existing `CRITICAL` dam-overtopping level — and the email/SMS now
carry a distinct bridge-deck / road-closure likelihood line alongside the gauge-keyed EAP action
blocks. No model/hydrology change; anchors unaffected.

### 2026-06-16 — risk-% machinery: fat upper tail, lead-aware confidence (no hydrology change)

A statistical-validity review of the displayed risk percentages and the forecast-confidence
indicator. **No calibrated parameter, model, or anchor changed** — this is downstream of the
hydrology, in `predict._exceedance_probability`, the confidence helper, and the templates. Step 6
peak and dry-equilibrium are untouched (re-confirmed with `validate`).

**Problem 1 — overtop/bridge risk read a hard zero.** `dam_crest` (342.2) and `bridge_deck` (342.7)
sit above the high-scenario peak in almost every forecast, so the old **clamped linear
extrapolation** produced `P=0` for any threshold above `peak_high + 0.25·(peak_high−peak_median)`
and a near-step function of the single high peak below it. That denied exactly the fat,
under-forecast upper tail the EAP cares about. **Fix:** keep the linear CDF in the *interior*
(between the low/high peaks, where the synthetic quantiles anchor it) but use **log-linear
(exponential-survival) tails** outside it. The upper tail's decay scale is derived from the
q50→q90 spacing, so a wider (uncertain / far-out / summer) band gives a fatter tail and a genuinely
higher — never zero — crest risk. Verified: a calm forecast (high peak ~1 ft below crest) still
yields sub-1% crest risk, well under the 0.10 WATCH cutoff, so no spurious escalation.

*Alert-ladder cutoffs were re-verified, not re-tuned.* The interior of the CDF is unchanged
(byte-for-byte) and old/new diverge **only** in the deep upper tail, where both functions return
**< 0.10** (they share the q90 anchor at exactly 0.10). Every action cutoff that gates on these
probabilities — WATCH `dam_crest ≥ 0.10`, DANGER `0.30`, CRITICAL `0.60`, EVACUATE
`bridge_deck ≥ 0.30`, ADVISORY/WARNING on `early_warning`, and the render `≥ 0.15`
vehicle-relocation hint — therefore evaluates in the unchanged interior/boundary. A 30,870-point
grid sweep over plausible (median, high) peak positions found **zero** firing-decision flips, so no
cutoff was adjusted. Consequence to note: the newly-surfaced sub-10% overtop/bridge risk is
**informational only** — no level acts below 0.10. If acting on a small-but-nonzero tail is ever
wanted, that is a deliberate new low cutoff, not a side effect of this change.

**Problem 2 — comonotonic band labels (documented, not changed).** The low/high branches are
**per-hour multiplicative ratios applied in lockstep** (`scenarios.synthesize_scenarios`). Summing
per-hour q10/q90 over a storm only yields the q10/q90 of the *total* under perfect hourly
correlation, so the q=0.10/0.90 labels fed into the CDF are an **upper bound on dispersion** —
conservative-wide, with a non-constant bias. We have no data to estimate the true hourly-error
correlation, so this is recorded as a known bias to resolve with the spec §3.5 logged
forecast-vs-gauge fit (which also replaces the placeholder `lead_ratio_by_day`). It propagates into
the new tail, whose heaviness is band-driven.

**Problem 3 — confidence was storm-blind and over-precise.** The live path hardcoded lead **day 1**,
so confidence varied only by month and ignored when the dangerous rain lands. It is now keyed to the
**risk-relevant lead** (earliest median threshold crossing, else the heaviest-rain hour), matching
the simulator path, and surfaced as an **ordinal skill score** (`~N% QPF skill at this lead`), not a
calibrated event probability. The underlying `skill_by_day / season_factor` heuristic is unchanged —
turning it into a real probability also needs the §3.5 data.

### 2026-07-03 — Critical-path review: emergency-accuracy findings (no model change)

A scoped review of `model.py`, `spillway.py`, `geometry.py`, `scenarios.py`, `predict.py`,
`units.py`, `artifact.py`, and `crystal_lake_v0.json` (design-digest + subagent review),
looking specifically for problems that get *worse*, or only manifest, precisely when the
lake is dangerously high or the input feed is degraded — as opposed to calibration-precision
issues under normal conditions. **No code or parameter changed by this entry; findings only.**
Ranked most-severe-for-emergency-accuracy first.

**1. The >343.1 ft regime silently extrapolates unvalidated, internally-inconsistent
geometry.** `geometry.py`'s `in_valid_range()` (a guard the module's own docstring claims is
enforced — "values are clamped with a warning flag rather than silently extrapolated") has
**zero callers** anywhere in `src/` (grep-confirmed). Nothing in `model.step` or `predict`
clamps to, or warns on, `valid_elev_range_ft` (338.8–343.1). Separately, the two geometry
fits are supposed to be consistent (`stage_area` = dS/dh of `stage_storage`), but they
diverge, and the divergence **grows with stage**: at the dam crest (342.2 ft) the linear
`stage_area` fit reads 6.4% below the quadratic `stage_storage` derivative; at 343.1 ft it's
7.6% low and still climbing. `model.m5_lake_update` (`model.py:171,174`) converts net flow to
elevation change via `surface_area_acres(h)`, so every dam-crest / bridge-deck-level
prediction — the highest-stakes output this system produces — runs on an increasingly wrong,
completely unflagged extrapolation of a fit that was never validated above 343.1 ft. (The
divergence direction makes the model over-predict rise, i.e. conservative in isolation — but
the Step 6 anchor was calibrated with this inconsistency already baked in, so behavior above
343.1 ft is both unanchored and internally inconsistent, not just "extrapolated.")
[**Correction from the #1b follow-up (below):** the linear stage-area is *not* the "wrong" fit
— it correctly matches the documented surface-area table, which is what the ODE needs. The
divergence is inconsistency in the upstream reference tables, not a bad fit; the flood-zone
extrapolation weakness is real and now flagged by #1a.]

**2. A zero-valued live rainfall forecast during an active NOAA QPF alert arithmetically caps
crossing probability near 0.5 — CRITICAL/EVACUATE can't fire.** In `scenarios.py:89-96`,
when `noaa_high_total_in` is supplied but the point forecast sums to zero, the NOAA total is
distributed *only* into the `high` scenario; `low` and `median` stay at zero rain. In
`predict._exceedance_probability`, `low`/`median` peaks then collapse to the same point
(`predict.py:49-54`) with cdf mass 0.5, so P(crossing) for any threshold above the "dry" peak
is bounded near 0.5 and decays toward the 0.10 floor as the threshold approaches the `high`
peak — regardless of how severe the NOAA-driven high scenario actually is. This is a
degraded-input failure mode that activates exactly when it's most dangerous: the live HA
forecast entity drops to zero (feed outage/gap) while an external NOAA flood watch is active.

**3. No fast-runoff/overland path exists; rapid, intense storms arrive systematically late.**
`hspf.INTFW` is loaded and schema-validated (`artifact.py:15`, value 6.0 in the artifact) but
is **never referenced anywhere in `model.py`** (grep-confirmed) — it is dead configuration.
All effective rainfall passes through the soil-moisture bucket, and even saturation-excess
overflow leaves only via the interflow store at a fixed IRC-derived ~2.85%/hr release
(`model.py:135`) plus the 4.6h lag. Rainfall *intensity* therefore barely affects arrival
timing: a sharp multi-inch cloudburst over a few hours and the same depth spread over days
produce nearly identical, smeared lake-response timing. This is distinct from the documented
peak-vs-recession structural tension (single-reservoir tradeoff) — it's the absence of any
saturation-excess *overland/quickflow* component, which is `INTFW`'s intended job in real
HSPF. The Step 6 anchor (10.27 in over 72h, a frontal storm) never exercises this path, so
`hours_to_crest` / `hours_to_bridge_deck` for a convective or rain-on-saturated-ground burst
would read hours later than reality — the wrong direction for evacuation lead time.

**4. Gappy trailing rainfall biases the hindcast state only toward "too dry," flagged solely
by a boolean.** In `predict.py:150-162,217`, missing hours in `trailing_rainfall_in` strictly
under-charge SM/S_if/S_agw (state-charging is monotone in rain), so a hindcast with holes
produces an under-forecast starting state indistinguishable from a clean one except for the
`data_fresh` flag — no band-widening or confidence penalty follows from it. Telemetry gaps are
disproportionately likely during the storms this system exists to warn about.

**Checked and found clean:** explicit-Euler integration at dt=1h remains stable near extreme
peaks (time constant ≈ 6–9h at 343–344 ft, since dQ/dh ≈ 200–290 cfs/ft against A ≈ 150–165
ac), and evaluating outflow at start-of-step h biases the peak slightly *high* (conservative),
not low. The spillway/overtopping discharge law (`spillway.py`) is continuous and strictly
monotone increasing through both legs' soffit transitions and the triangular overtopping
integral all the way past the bridge deck — no plateau or inversion near the thresholds that
matter.

**Follow-up:** these are tracked as open items below; none has been fixed yet.

### 2026-07-03 — Fix #1a: flag (don't clamp) out-of-validated-geometry projections

First half of finding #1. **No hydrology or calibrated parameter changed; anchors
unaffected** (Step 6 342.92, dry-eq 339.666 — identical to before). Wired up the
previously-dead `geometry.in_valid_range` (it had zero callers, and the module docstring's
claim that out-of-range values were "clamped with a warning flag" was false — nothing clamped
or warned). The predictor now sets a new `PredictionResult.peak_outside_validated_geometry`
flag whenever any scenario peak leaves `valid_elev_range_ft` (338.8–343.1 ft), and the CLI
prints a caveat line. **Estimates are still produced above the band** — that regime (dam-crest
/ bridge-deck overtopping) has never been gauged, so there is nothing to clamp to; the point is
only to stop an extrapolated number being read as a measured-range result. Corrected the
`geometry.py` module docstring to say the curves are extrapolated-but-flagged, not clamped.
Added `test_predict_flags_out_of_validated_geometry`; full suite green (138 tests).

*Note on why this matters here specifically:* the linear stage-area and quadratic
stage-storage curves agree to ~2% everywhere the lake has actually been observed (they cross
at ~340.15 ft) and diverge to 7–8% only in the 342–343 ft overtopping band — the exact regime
this flag marks. So the flag fires precisely where the geometry is both extrapolated *and*
internally disputed. Fix #1b (below) investigated the disputed-geometry half.

### 2026-07-03 — Fix #1b: keep the linear stage-area; the disputed geometry is *source-data* inconsistency

The plan was to "finish the volumetric upgrade" — drive surface area from the storage curve's
derivative (`dS/dh`) on the theory that the quadratic stage-storage was the authoritative
"measured geometry" the linear stage-area was meant to replace. **Attempting it disproved the
premise.** `test_geometry_matches_documented_hard_points` enforces documented Reference-3.1 hard
points for *both* curves, and they are **mutually inconsistent**:

- **Storage** (338.8→131, 340.0→230, 342.15→486 ac-ft) — the **quadratic** matches these.
- **Surface area** (338.8→75.1, 340.0→96.1 ac) — the **linear stage-area** matches these.
- But `dS/dh` of the storage curve is **69.4 ac at base**, not the documented 75.1 ac (~8% low).

So the linear stage-area is *not* a crude leftover — it is independently anchored to the
documented **surface-area** table, which is exactly the quantity the lake-level ODE needs
(`Δh = Q·Δt·0.0826 / A(h)`). The 6–8% divergence Fable's review flagged is real, but it is an
inconsistency in the **upstream reference tables** (area and storage tabulated/processed
differently), not a bug in either fit. No single polynomial satisfies both.

The two curves cut opposite ways by regime: at normal pool the linear area matches the doc
(75.1) while `dS/dh` is ~8% low; in the flood zone (342.15) the linear area is a 2-point
extrapolation (133.8 ac) while `dS/dh` (142.2 ac) is anchored by the documented 486 ac-ft
storage point — and, since HEC-HMS reservoir routing is storage-based, `dS/dh` is also
consistent with how the 343.1 Step-6 target was generated.

**Decision (do no harm):** keep the linear stage-area. It is correct for surface area where the
lake actually operates (99% of the time near normal pool); the flood-zone extrapolation
weakness is already *flagged* by #1a. Switching to `dS/dh` to gain internal consistency with an
unused curve would degrade the physical surface area exactly at normal pool. **What changed:**
only the false `surface_area_acres` docstring claim ("Also equals dS/dh" — it does not, and
cannot, given the inconsistent source data) plus a `storage_acft` docstring note that it is not
consumed by the level update and is not derivative-consistent with the area fit. The
`stage_storage` curve and `storage_acft` are **retained** (the hard-points test validates them
against the documented storage, and they'd seed any future volumetric routing). **No code path,
parameter, or anchor changed** (Step 6 342.92, dry-eq 339.666).

*Recorded for the future two-timescale/geometry work:* if a proper reconciliation is ever
wanted, the defensible route is a **synthesized surface-area curve** honoring the documented
area points at low pool AND the storage-anchored area in the flood zone (e.g.
`A(x)=1.18x²+16.1x+75.1` through (0,75.1),(1.2,96.1),(3.35,142.2)) — deferred as inventing
geometry from sparse, inconsistent data, and unnecessary until a flood-zone gauge observation
or a real crest survey exists to check it against.

### 2026-07-03 — Fix #2: blend a NOAA high-end QPF into the median (unlock CRITICAL/EVACUATE)

Finding #2 from the emergency review. **No hydrology or anchor changed** (Step 6 342.92, dry-eq
339.666); this is upstream of the model, in scenario synthesis.

**Bug:** `synthesize_scenarios` poured a NOAA high-end QPF total *only* into the `high` branch.
When the automated point forecast read ~0 — most plausibly a **dropped forecast feed during an
active flood watch** — `low` and `median` stayed dry, their peaks collapsed to the current lake
level, and `_exceedance_probability` deduped them to a single support point at cdf 0.50. That
**mathematically caps P(crossing) at 0.50** for every threshold above the current level, so
CRITICAL (`dam_crest ≥ 0.60`) was *arithmetically impossible* and EVACUATE nearly so — exactly
in the feed-outage-under-flood-watch case. The deeper defect is incoherence: a band whose median
is dry but whose 90th percentile is a major storm is not a valid distribution (50%+ mass at zero
rain). *Currently latent in the live path* (`live_ha` passes `noaa_high_total_in=None`); it bit
the snapshot/API/simulate paths and would bite live the moment NOAA is wired in.

**Fix (blend-into-median, option B):** the NOAA total now lifts the **median** storm total to
`max(point_total, f · noaa_total)`, `f = uncertainty.noaa_median_fraction = 0.5`; `low`/`high`
are the normal lead band around that median, and the NOAA total still anchors `high` as an upper
bound. `f = 0.5` keeps NOAA a *high-end* number (median sits below it) and matches the
median/high ratio the model's own day-2/3 band implies for a 90th-pct total; raise toward 1 for a
more conservative center, lower to lean on the point forecast (tune with spec 3.5 data). Only the
NOAA-dominant case changes; the no-NOAA path is byte-for-byte unchanged.

**Verified:** with a dropped feed (all-zero point forecast) + NOAA 16 in from 341.9 ft, the
blended median storm peaks at 342.44 ft (crosses the 342.2 crest) and **P(cross crest) = 0.66**,
i.e. CRITICAL now fires — where the old high-only behavior was capped ≤ 0.50. Correctly, when the
*median* storm does **not** reach a threshold, P stays below 0.5 (no spurious escalation): the fix
removes an artificial cap, it does not inflate risk. Note the single-reservoir routing heavily
attenuates short storms (a 24 h storm's low/median peaks stay close), so CRITICAL still requires
the central storm to genuinely cross — which is the right condition. New tests:
`test_noaa_blends_into_median_when_forecast_dry`, `test_noaa_dry_feed_unlocks_critical_crossing`,
`test_noaa_below_forecast_leaves_median_untouched`; full suite green (141).

**Related, not fixed here:** a zero point forecast is indistinguishable from a genuine dry
forecast (same theme as finding #4). This fix leans on the NOAA signal when present; it does not
add independent detection of a silently-dropped feed.

### 2026-07-03 — Fix #4: floor the state on a *truthful* degraded-data signal (not a dry-confounded proxy)

Finding #4 from the emergency review. **No hydrology or anchor changed** (Step 6 342.92, dry-eq
339.666).

**Two problems.** (a) Gappy trailing rainfall under-charges the hindcast toward "too dry"
(missing hours read as no rain), biasing the forecast low — the dangerous direction, and gaps
correlate with the very storms we warn about. (b) The signal that was supposed to catch this was
unsound. `hourly_from_accumulator` flagged gaps from **record coverage** (`< 25 %` of hours have a
record) — but the HA recorder only stores a row on value *change*, so a dry-but-healthy
accumulator sitting at 0 produces few records and looks **identical to a real outage**. It was a
dryness detector wearing a gap-detection costume; every caller already discarded it, and
`rainfall_has_gaps` was instead populated from a point-in-time lake-liveness ping.

**The right trigger is an actual failure to retrieve the data**, which the source can report
truthfully at fetch time. `rainfall_has_gaps` is now set when the rain-history request **throws**
(caught → degrade, don't crash the prediction) or returns **zero usable records** over the whole
trailing window — which, unlike sparse coverage, is *not* confounded with dry weather (a healthy
sensor always returns ≥ 1 record) — OR the lake gauge is genuinely stale. The coverage heuristic
is retired to a coarse diagnostic with a comment warning it must not gate safety logic.

**Compensation (chosen: floor the state).** When `rainfall_has_gaps`, the predictor floors the
spun-up SM/AGW at the month's climatological seed — the *same* seasonal seed the no-trailing-rain
path already uses (`predict.py`, hindcast branch). Absent reliable recent rain, assume normal
wetness for the season, not a dry basin. One-directional (`max` only), so a genuinely wet hindcast
is untouched, and it is protective exactly in the wet season (Nov–Mar seed ≈ saturated). Verified:
a 20-day dry trailing window in July drains SM below the seed, and the floor lifts it back to the
seed while leaving a wet hindcast alone. New tests: `test_rain_fetch_http_error_flags_gaps...`,
`test_empty_rain_history_flags_gaps_even_when_gauge_fresh`,
`test_gappy_data_floors_state_at_seasonal_normal`; 144 pass.

**Known limits (honest):** this catches retrieval failures *in the current pull*; a gap that sat
mid-window in already-stored history and has since recovered is **not** reconstructable from the
recorder (it drops unchanged 0s) — that needs HA **recorder statistics** (already a README
caveat). And "down now" is point-in-time: it can't tell whether 15 min or 3 days of the window is
missing, so flooring the whole subsurface on a brief blip is blunt but conservative (fails toward
caution).

### 2026-07-03 — Fix #3: wetness-driven interflow generation (research-corrected; PERC_coeff 0.20→0.31)

Finding #3 flagged "no fast-runoff path; rainfall intensity barely affects timing; INTFW dead." A
Fable review and a **PNW-literature check** reshaped it substantially, and the first attempt was
reverted.

**What the research established** (Ecology WWHM App. III-B; EPA BASINS Tech Note 6; forested-
hillslope hydrology literature — see the #3 web sources):
- HSPF `INFILT` is *not* a Hortonian intensity threshold; it is a soil-moisture-dependent index
  that divides moisture between infiltration and surface/interflow.
- In **PNW forested till**, Hortonian (infiltration-excess) overland flow is **rare** — macropore-
  rich forest soils infiltrate readily. Storm runoff is **subsurface stormflow (interflow)** over
  the glacial-till hardpan: a perched-water-table / variable-source-area process that grows with
  **wetness**, not rain intensity.
- So the basin genuinely is *not* very intensity-sensitive; an intense burst on **dry** till is
  largely absorbed. A first implementation that added an intensity-based infiltration-excess
  (Hortonian) term was **reverted** as unphysical for this basin (it made dry soil over-respond).

**The change (correct mechanism).** `m2_soil_bucket` now generates interflow **below** full
saturation: a fraction `(SM/LZSN)**INFEXP` of incoming rain sheds to the interflow store, the rest
infiltrates; saturation excess still routes to interflow so the fraction reaches 1 continuously at
LZSN. `INFEXP=2.0` is the Till nonlinearity already used for percolation — **no new knob**. Dry
soil absorbs; wet soil sheds. (`INTFW` stays reserved: it governs the small HSPF surface-runoff
residual, which needs a SURO store we deliberately did not add.)

**Why it required re-deriving PERC_coeff, and why that's disciplined.** Adding the wetness-interflow
path diverts some wet-soil rain from GW recharge to fast interflow, which dropped the modeled
post-rain baseflow to **2.90 cfs** — worse than the gauge-calibrated **~3.7 cfs**. But the same
change **raised the Step 6 saturated peak (342.92→343.02)**, freeing the flood-peak margin the log
had said pinned `PERC_coeff` at its ~0.20 ceiling. Holding the **real gauge target (3.7 cfs)** fixed
and moving the parameter, `PERC_coeff 0.20→0.31` restores baseflow to **3.71 cfs** with **Step 6
back at 342.91** and **dry-eq 339.67**. All three original targets hit simultaneously. This is
re-deriving a parameter to keep fitting real data after a structural change — not chasing a modeled
number.

**Net:** this is the **two-timescale relief** the structural note called the main outstanding
project — a wetness-driven fast path (interflow) carries the peak, freeing the slow GW store to
carry the recession, so baseflow is no longer capped by the flood-peak trade-off. Anchors: Step 6
342.91, dry-eq 339.668. New/updated tests: `test_m2_soil_bucket_interflow_grows_with_wetness`,
`test_interflow_engages_below_saturation`; 145 pass.

**Honest caveats.** (1) Still uncalibratable in the flashy/moist-storm regime — grounded in PNW
literature + the one June gauge event, not a logged storm-response fit. (2) The `(SM/LZSN)**INFEXP`
shape is a defensible reduced form of HSPF's moisture-dependent division, not HSPF's exact
INFILT/INTFW machinery. (3) IRC=0.5/day still governs interflow *release*, so this improves *which*
rain becomes fast runoff and *when it starts* (below saturation), not the ~1-day release timescale.
(4) `PERC_coeff` is now ~3× the HSPF nominal; the gauge, not the nominal, is the anchor — revisit
with a true dry-down and the Wolock BFI≈0.67.

### 2026-07-06 — Fix: spillway capacity-ratio fallback honors a raised crest (no canonical-model change)

Code correctness fix in `spillway._leg_flow`, in the branch used **only when a leg has no
`crest_length_ft`**. That fallback anchored its rated head at the *active* control
(`rated_elev − control_elev`), so at the rated elevation `head == h_rated` for any stop-log
setting and it returned the full bare-sill `capacity_cfs_at_342` regardless of how high the crest
was raised — overstating spillway outflow (and under-predicting lake level) in the summer
high-board regime near the crest. Now anchored at the leg's **bare sill**
(`rated_elev − leg.control_elev_ft`), making the fallback the exact algebraic equivalent of the
crest-length-known physical branch (`Q = capacity·(head / (rated − leg.control))**n`).

**No effect on the canonical model or the anchors:** both legs carry measured crest lengths
(primary 10.0 ft, aux 7.5 ft), so the artifact always takes the physical branch; the fallback is
latent. Anchors unchanged (Step 6 342.91 ft; dry-equilibrium 339.668 ft). Guarded by
`test_m6_capacity_ratio_fallback_matches_physical_and_honors_raised_crest`. The test-locked weir
exponent (1.5) is untouched.

---

### 2026-07-07 — Assimilate the backtest's initial state from recorded history (any weather) + bulletproof gap-aware archive

**No hydrology or anchor changed** (Step 6 342.91, dry-eq 339.668; 241 tests pass). This is a
*state-seeding* change to the backtest plus a robustness overhaul of the continuous archive — not
a parameter change.

**Symptom.** On a long-lookback backtest (e.g. 240 h) over a dry window, the predicted line drifts
*up* toward the dry equilibrium while the actual gauge recedes, growing with the window. The
backtest anchors *elevation* to the gauge at T0 but seeds the *subsurface* stores from climatology.
The slow active-groundwater store `S_agw` (~23-day half-life) can't be set by the ~10-day HA
hindcast, and at full zoom-out T0 sits at the edge of HA retention with no pre-T0 rain to spin up
from at all; the seasonal `seasonal_agw_default_in` seed then runs the lake to equilibrium
regardless of the real (drier) antecedent state. (The seasonal **SM** seed also percolates into
`S_agw` during spin-up, charging it further — observed `S_agw` ≈ 0.53 in at T0 vs a July 0.165.)

**Fix — history spin-up estimator (`antecedent.estimate_state`).** Seed the state seasonally and
replay the recorded rain forward to T0 — a pure forward spin-up, no fit. `run_backtest` now takes a
full `state0`; `LiveHASource.fetch_backtest` computes it from the continuous archive and falls back
to the seasonal spin-up only when there's no usable history at all. Recovery on a > 5-half-life
synthetic record with periodic storms: the T0 groundwater tracks the model's true end state within
**~15 %**, versus a seasonal seed that can be off by several-fold.

**A smooth seasonal→historical blend (not an on/off switch).** The replay window is *capped* at
`window_half_lives` × the groundwater half-life (AGWRC=0.97 → ~23 d), default **5 half-lives ≈ 114 d**,
and the seed is placed at `max(record-start, T0 − 114 d)`. When **less** history exists we use *all*
of it: seeding seasonal at its start assumes the seasonal average held in the unrecorded period
before our record began, and that seed then drains through the observed hours. So the estimate
transitions *continuously* — with a few days of history it leans on the seasonal prior; as the
record lengthens the seed drains and the state becomes essentially historical; by the ~114 d cap
the seasonal seed has drained to ~10 % of the seasonal baseflow target and older history is
redundant (verified smooth + monotone on a dry-summer synthetic: s_agw 0.11 → 0.05 → 0.02 in over
10 → 45 → 114 d). It is *not* a threshold that flips from all-seasonal to all-historical — that
abrupt version wasted the partial history we already have while waiting for more.

**Why 5 half-lives is the cap.** Over a *truly dry* window the whole seasonal seed drains to ~10 %
at 5 half-lives — not just the `S_agw` seed (which decays directly), but the **pulse the seasonal
soil-moisture seed percolates into groundwater**. SM drains fast (~2 weeks) *into* the slow store,
so that pulse peaks ~2 weeks in and only *then* decays at the 23-day half-life; because of the late
peak it takes ~5 (not ~3) half-lives to reach ~10 % (residual-vs-half-life table in the 2026-07-08
discussion). An earlier bounded-window + level-RMSE *fit* was tried and dropped: with a window this
long the fit is barely constraining and it mis-scored the seed-transient early window. Pure replay
is simpler and matches the physics.

**Soil-moisture seeding — the subtlety worth remembering.** Seeding SM = seasonal *average* and
letting it percolate is what inflates baseflow in a genuinely dry spell (the seasonal seed says
"normal-June-wet" but the soil is drier). Draining it through the observed record over the ~114 d
window is the fix: by T0 the SM (and its groundwater imprint) reflect what actually happened, not
the seed. When the season and reality disagree in an anomalous way the model/data mismatch is large
even over the drained tail — the tail-RMSE gate catches that and falls back to seasonal.

**Bulletproof gap-aware archive.** The archive is now **sharded by UTC day**
(`data/continuous/crystal_lake/YYYY-MM-DD.json`): the hourly append rewrites only the current day,
completed days are immutable (incremental-backup-friendly), and readers pull just the window they
need (`load_window`) instead of loading all history. `HourSample.rain_in` became `float | None` so
a data gap is *preserved as missing* rather than fabricated as `0.0` dry. Gap-aware hourly fillers
(`hourly.py`) carry a healthy dry/steady feed forward within a **6 h staleness horizon** and mark
longer silence `None`; the merge never overwrites a real value with a gap, so re-pulling HA fills
recoverable holes while genuinely-missing hours stay missing. On startup the archive scheduler
**backfills as much history as HA retains** (`CALIB_BACKFILL_DAYS`, default 400 d; HA truncates to
its own retention), off-thread and idempotent. A monolithic pre-sharding archive is migrated to
shards on first load.

**Fallback (fall back to seasonal only when we truly can't estimate).** `estimate_state` returns
`None` — keeping the seasonal spin-up — only when there is *no* usable (gap-bounded) history at all,
or the replay is grossly wrong over its **drained tail** (last ~2 half-lives; RMSE > 0.5 ft —
broken data, e.g. a bad datum, not a seed transient). Otherwise it always returns the blend, using
whatever history exists. On the current ~24-day archive it now produces a mostly-seasonal blend
that leans more historical as the record grows. Tests: `tests/test_antecedent.py` (recovery over a
>5-half-life record, the seasonal→historical **smooth-blend** monotonicity + cap, end-to-end
backtest), archive sharding/missing-preservation and
gap-vs-staleness tests in `tests/test_calibration.py` / `tests/test_live_ha.py`.

**Scope / safety.** Wired into the **backtest only** (an accuracy diagnostic). The live/alert path
deliberately keeps the seasonal *floor* (#4, 2026-07-03): under-seeding groundwater under-warns,
the dangerous direction. Because the assimilated estimate is confident (it reproduces the level
history), extending it to the live predictor as a *floor-only* correction is a reasonable
follow-up — deferred. See the Structural-findings item below.

## Structural findings & open items

- **Initial-state spin-up — history-replay seam added for the backtest (2026-07-07/08).**
  `antecedent.estimate_state` seeds seasonally and replays the recorded rain forward, a **smooth
  seasonal→historical blend** capped at **5 groundwater half-lives ≈ 114 d**: it leans seasonal
  with little history and becomes essentially historical by the cap (seed drained to ~10 %). The
  backtest uses it whenever any usable history exists. **Open:** (a) extend to the live/alert
  predictor as a *floor-only* correction (preserve the #4 safety asymmetry — never let it lower a
  warning); (b) the analytic `antecedent.infer_s_agw` (dry-recession baseflow inversion) is retained
  as a standalone tool but not used by the estimator; (c) `window_half_lives=5` and the tail-RMSE
  0.5 ft gate are judgement calls — 5 leaves ~10 % seed residual for a *June*-magnitude SM seed; a
  saturated wet-season seed followed by an anomalously dry stretch would need more, but the tail-RMSE
  gate catches that mismatch and falls back. Revisit against a real multi-month record. **Trade-off:
  the ~10 % residual is a floor** — the cap deliberately stops draining the seed further even when
  more history exists (the bounded-window choice), so the estimate is never 100 % historical.
- **The flood-peak vs sustained-recession tension — substantially relieved (#3, 2026-07-03).**
  It *was* structural: with interflow generated only at full saturation, every lever that
  sustained the recession (more percolation) attenuated the Step 6 peak. The #3 wetness-driven
  interflow generation (interflow engages below saturation, `(SM/LZSN)**INFEXP`) added the fast
  path that carries the peak, which let `PERC_coeff` rise 0.20→0.31 to strengthen the slow-store
  recession **without** hurting Step 6. Residual: interflow *release* is still one timescale
  (IRC=0.5/day); a fuller distributed/two-store routing remains a future refinement, but the
  binding trade-off is gone.
- **Step 6 margin — no longer tight.** With #3, Step 6 sits at **342.91 ft** and `PERC_coeff=0.31`
  is set by the gauge baseflow target, not throttled by the flood peak. Still re-verify both
  anchors on any subsurface change.
- **Baseflow calibration is one event, one season** (mid-June 2026, after a wet spell). Confirm
  with a true late-summer dry-down and against the Wolock-grid BFI ≈ 0.67 (brief §C/D.1).
- **Sensor offset / drift unresolved** (~0.12 ft). Get the crest-crossing reading above.
- **Sensor is genuinely noisy** (~0.6–1.4 in within-hour, on the `_smoothed` entity). Smoothing
  conventions mitigate it but the noise floors the live anchor (~0.21 in) — a hardware / mounting /
  HA-filter item worth chasing, and worth checking whether it correlates with wind (a real seiche,
  harmless) vs electrical/ultrasonic noise.
- **The 4.6 h basin lag is weakly grounded** (a doc "4–5 h" ballpark + one suspect dashboard
  read) and is a pure translation delay. It affects timing, not the duration of delivery; a
  distributed routing belongs with the two-timescale fix.
- **Geometry above ~340 ft is 2-point-extrapolated (surface area), and the documented area &
  storage tables are mutually inconsistent** (dS/dh ≠ documented area; a source-data issue, not
  a bad fit). *Resolved (#1a/#1b, 2026-07-03):* `in_valid_range` is now enforced/flagged, and we
  keep the linear stage-area (correct for surface area where the lake operates). A synthesized
  flood-zone-anchored area curve is deferred until there's data to check it — see the #1b entry.
- ~~**A zero live-forecast reading during an active NOAA QPF alert caps crossing probability
  near 0.5**~~ *Resolved (#2, 2026-07-03):* NOAA now blends into the median
  (`uncertainty.noaa_median_fraction`), so the band is coherent and CRITICAL/EVACUATE can fire
  when the central storm crosses. Residual: a dropped feed still looks like a dry forecast when
  no NOAA total is supplied (theme shared with #4).
- **No fast-runoff/overland path — `INTFW` is loaded but dead code.** Rainfall intensity
  barely affects arrival timing, so rapid/convective storms are predicted systematically late.
  See 2026-07-03 entry.
- ~~**Rainfall-gap handling only biases the hindcast state dry**~~ *Resolved (#4, 2026-07-03):*
  `rainfall_has_gaps` now reflects an actual data-retrieval failure (not a dry-confounded coverage
  proxy), and on that signal the predictor floors SM/AGW at the seasonal climatological seed.
  Residual: recovered mid-window historical gaps still need HA recorder statistics (README caveat).

---

## How to use this when changing the model

1. Before re-tuning a parameter in the table above, read its provenance here and in the
   artifact comment. If it's gauge-calibrated, treat the gauge number as the target.
2. After any subsurface/spillway change, re-run `lake-rise validate` (both anchors) and
   `pytest`; if you have HA access, re-run the post-rain comparison against the live trace.
3. Record the change here with a dated entry and update the artifact `*_comment`.
