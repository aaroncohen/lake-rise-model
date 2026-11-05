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
| `hspf.PERC_coeff` | **0.42** (~4× HSPF nominal) | **Calibrated to the 2026-06-12 gauge recession** (~3.7 cfs sustained delivery 66 h after rain). 0.10→0.20 (06-12) →0.31 (07-03, after the #3 interflow change) were fitted with ~1.5×-high rain (2.89 vs 1.94 in). **Re-derived on 2026-11-04** with the true rain: 0.49 on the 10-day seasonal-seed hindcast, then 0.43 on the archive-replay spin-up the live forecast now uses (same code for fit and forecast), then **0.42** once lake evaporation entered the balance and leakage went to 0 (June target 3.70 − 0.69 + 0.68 = 3.69 cfs). The event constrains it weakly (target 2.3–4.6 cfs within gauge noise; solve 0.085–0.49 by spin-up start). Re-fit on the Nov 2026 AR recession. | weakly constrained (1 noisy event); alert-path-consistent |
| `spillway.leakage.cfs_per_ft2` | **0.0** (no seam seepage) | 0.0557 → 0.040 (06-12) → **0 (2026-11-04)**: with open-water evaporation in the lake balance the 3-log dry-equilibrium holds at 339.667 ft with no seepage — the ~0.6 cfs once attributed to leakage was evaporation. Operator: no outflow through the boards. Mechanism kept for a future gauge-clean re-fit; below the documented 0.5–2.0 cfs seepage bounds, which are not enforced. | gauge + operator consistent |
| `lake_evaporation_in_per_day` | monthly table, 0.03–0.17 in/day | **New 2026-11-04.** Jun–Aug: FAO-56 reference ET from the carport T/RH + WH85 wind, cross-checked by the no-outflow Jul–Aug drawdown (0.14–0.18 in/day). Sep–Nov: mass transfer from the lake probe (lake 2–3 °C warmer than the air). Dec–May extrapolated. | measured Jun–Nov; provisional Dec–May |
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

**No hydrology or anchor changed** (Step 6 342.91, dry-eq 339.668; 242 tests pass). This is a
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
to the seasonal spin-up only when there's no usable history at all. Recovery on a multi-month
(~130 d) synthetic record with periodic storms: the T0 groundwater tracks the model's true end
state within **~15 %**, versus a seasonal seed that can be off by several-fold.

**A smooth seasonal→historical blend (not an on/off switch).** The lookback is *capped* at
`cap_half_lives` × the groundwater half-life (AGWRC=0.97 → ~23 d), default **8 half-lives ≈ 182 d**
(`antecedent.DEFAULT_CAP_HALF_LIVES`, a parameter), and the seed is placed at
`max(record-start, T0 − cap)`. When **less** history exists we use *all* of it: seeding seasonal at
its start assumes the seasonal average held in the unrecorded period before our record began, and
that seed then drains through the observed hours. So the estimate transitions *continuously* — with
a few days of history it leans on the seasonal prior; as the record lengthens the seed drains and
the state becomes essentially historical (verified smooth + monotone on a dry-summer synthetic:
s_agw 0.11 → 0.05 → 0.01 in over 10 → 45 → 90 d). It is *not* a threshold that flips from
all-seasonal to all-historical — that abrupt version wasted the partial history we already have.

**Why ~8 half-lives is the cap.** The cap sets how much seasonal seed survives to T0: a forward
replay always carries the initial condition as a residual of ½^(cap ÷ half-life), so the cap length
*is* the seasonal share left at T0. Over a *truly dry* window that residual is not just the `S_agw`
seed (which decays directly) but the **pulse the seasonal soil-moisture seed percolates into
groundwater** — SM drains fast (~2 weeks) *into* the slow store, so the pulse peaks ~2 weeks in and
only *then* decays at the 23-day half-life. Because of that late peak the residual runs ~10 % at 5
half-lives, ~3 % at 7, **~1 % at 8**, ~0 % at 10 (residual-vs-cap table in the 2026-07-08
discussion). 8 was chosen so that at/past the cap the estimate **effectively doesn't rely on the
seasonal prior at all** (~1 %), while still engaging on a few months of archive; lower it to engage
sooner (accepting more seed), raise it to lean even less on the seed. An earlier level-RMSE *fit*
was tried and dropped: over a window this long the fit is barely constraining and it mis-scored the
seed-transient early window. Pure replay is simpler and matches the physics — the seed just drains.

**Soil-moisture seeding — the subtlety worth remembering.** Seeding SM = seasonal *average* and
letting it percolate is what inflates baseflow in a genuinely dry spell (the seasonal seed says
"normal-June-wet" but the soil is drier). Draining it through the observed record over the ~182 d
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
multi-month record, the seasonal→historical **smooth-blend** monotonicity, the lookback cap + seed
drainage, end-to-end backtest), archive sharding/missing-preservation and
gap-vs-staleness tests in `tests/test_calibration.py` / `tests/test_live_ha.py`.

**Scope / safety.** Wired into the **backtest only** (an accuracy diagnostic). The live/alert path
deliberately keeps the seasonal *floor* (#4, 2026-07-03): under-seeding groundwater under-warns,
the dangerous direction. Because the assimilated estimate is confident (it reproduces the level
history), extending it to the live predictor as a *floor-only* correction is a reasonable
follow-up — deferred. See the Structural-findings item below.

### 2026-11-04 — First review of the saved archive (Jul–Nov 2026): rain ingestion is wrong; no parameter changed

**No code, parameter, or anchor changed.** Findings from the NAS continuous archive
(`/volume2/docker/lake-rise/data/continuous/crystal_lake`, 519 day shards; elevation complete from
2026-07-01, the earlier shards are empty backfill placeholders) cross-checked against HA long-term
statistics, ahead of the first wet-season (atmospheric-river) storms.

**1. Archived rain is ~1.6× too high, and ~70 % of dry hours are stored as missing.**
`sensor.gw3000b_hourly_rain_piezo` is a *rolling 60-min* total, not a per-clock-hour accumulator, so
taking its max within each clock hour counts every burst in two adjacent hours. Monthly totals,
archive vs the HA long-term statistic `change` of `sensor.gw3000b_daily_rain_piezo` (corroborated by
the independent `sensor.home_rain_inches`): Jul 0.43 vs 0.24, Aug 1.52 vs 0.99, Sep 5.50 vs 3.47,
Oct 3.13 vs 1.93 in. Worst on intense bursts: the 2026-09-05 cloudburst archived as 1.76 in, truly
0.93 in. Separately, the 6 h staleness rule in `hourly.rain_hourly_gapaware` marks a dry spell as
missing once the flat-at-zero sensor stops writing rows — every one of the 28 null-rain runs starts
right after a 0.00 hour, and 24 end on a wet hour. That breaks every recession segment, so the
AGWRC signature found 0 usable recessions. **The same max-per-clock-hour conversion
(`hourly_from_accumulator`) feeds the live 10-day hindcast and `capture_storm`.** Live effect is small
and conservative: about +0.08 ft on an atmospheric-river-preset peak from today's state.
**Better source:** HA long-term statistics of `sensor.gw3000b_daily_rain_piezo` — exact hourly
`change`, kept permanently, no dry-vs-missing ambiguity, available from 2026-02.

**2. The June `PERC_coeff` calibration was fitted to inflated rain.** The 2026-06-12 entry's "2.89 in
over the 8 days" was 1.94 in true (statistics, Jun 2–10). With the true rain the model would deliver
less than the 3.7 cfs target, so `PERC_coeff = 0.31` needs re-deriving once ingestion is fixed.
Not changed here.

**3. The lake has been at or below the bare sill (~338.8) since mid-August.** No spill and no seam
leakage (leakage is zero below the sill), so the lake has been acting as a closed volumetric inflow
gauge. July's rain-free drawdown was ~0.01 ft/day — open-water evaporation, which the model doesn't
have (no lake-ET term in `m5_lake_update`; it attributes the loss to board leakage while the lake is
above the sill). Consequences: (a) the AGWRC signature (spillway rating → Q) is meaningless in this
regime; (b) the BFI→PERC (0.31→0.10) and dry-eq→leakage (0.04→0.02) proposals are model-internal
fits, not fits to the observed lake — **do not approve them**; (c) replaying with the corrected rain
from 2026-08-19, the modeled lake climbs ~+0.6 ft in 30 days while the gauge stays flat (missing
evaporation plus standing baseflow the dry fall didn't produce), so `estimate_state` exceeds its
0.5 ft tail-RMSE gate and falls back to seasonal for the October storms.

**4. The dry watershed delivered almost nothing.** Six ≥0.45 in storms (Aug 29–Oct 27) produced
observed rises within the ±0.05–0.1 ft gauge wander (often less than rain on the lake alone, 0.04–0.1
ft); implied runoff coefficient ~0–0.03, vs the model's 0.01–0.08 (rising through October as the
seasonal seed takes over). The model over-predicts the early, below-crest part of a storm from this
antecedent state — the conservative direction — but this is gauge-noise-limited and not yet a
calibration number.

**5. Gauge glitch stored as data.** 2026-10-09 00–01 UTC jumped 338.83 → 339.86 for two hours and
returned. The archive needs a spike/validity filter before any signature uses it (the smoothed
entity exposes `source_value_valid` / `age_coverage_ratio`).

**Next:** fix rain ingestion (archive, storm capture, then the live hindcast so training and serving
match) and backfill rain from the statistics; add elevation spike filtering; capture the coming
atmospheric river — first fill → spill over the boards → recession of this record — and record the
actual stop-log count during it. No data is at risk meanwhile: HA keeps hourly statistics for rain
and depth permanently.

### 2026-11-04 — Fix: archive rain from the daily accumulator + statistics backfill; despike on read

**No hydrology, parameter, or anchor changed** (Step 6 342.91, dry-eq 339.668; 291 tests pass).
Data-ingestion fix for findings 1 and 5 of the entry above; the live forecast path is untouched.

- **Rain = per-hour rise of `sensor.gw3000b_daily_rain_piezo`** (`hourly.rain_hourly_from_daily_accumulator`)
  for the hourly archive pull, `fetch_backtest`, and `capture_storm`. HA writes no row while the
  accumulator holds, so silence is observed-dry `0.0`; only an `unknown`/`unavailable` state marks
  an hour missing, and the value it recovers with is a new baseline, not rain. A decrease is the
  midnight reset. Verified against HA's own hourly statistic over 240 h: identical (1.118 in both).
- **Backfill from HA long-term statistics** (`sources/ha_statistics.py`, a stdlib websocket client
  — HA has no REST endpoint for statistics). The hourly `change` of the same sensor is the same
  quantity, kept permanently. Runs in the startup backfill and `lake-rise calibration archive
  --days N --rain-statistics`; rain-only samples, so archived levels are never touched. On a copy
  of the NAS archive this reproduces the corrected record exactly (rain complete from 2026-03).
- **Gauge-glitch filter on read** (`archive.despike_levels`, applied by `load` / `load_window`; the
  stored shards stay raw): an hour more than 0.3 ft from the median of ±3 h is dropped as missing.
  It catches only the 2026-10-09 two-hour +1 ft spike on the Jul–Nov record (nothing else exceeds
  0.2 ft) and leaves the Step 6 hydrograph untouched (test-enforced).
- **Temporary training/serving skew:** the live hindcast (`fetch_snapshot`) still reads
  `hourly_rain_piezo` (~1.6× high, about +0.08 ft on an AR-preset peak — conservative), while the
  archive and backtest now carry true rain. Switching the live path is the planned follow-up; any
  parameter re-derived from the corrected archive (e.g. `PERC_coeff`) must land together with it.

### 2026-11-04 — `PERC_coeff` re-examined with the true June rain: not identifiable; held at 0.31

**No parameter changed.** The June fit's rain input was ~1.5× high (2.89 in vs 1.94 in true, Jun 4–11),
so it was re-solved with the true rain (HA statistics) and the depth statistics (hourly from Jun 5).
The original procedure was never scripted and **does not reproduce**: replaying ~1.5× rain from a
live-style seasonal spin-up gives ~5 cfs at 66 h with `PERC_coeff = 0.31`, not 3.7, and solving
for 3.7 cfs with that rain lands at the range floor (~0.05).

**Why the event can't pin it.**
- **The target is inside the gauge noise.** The rise rate at T = last rain + 66 h (2026-06-13 06Z)
  is −0.28 / −0.03 / +0.28 in/day for a 48 h centered / 72 h centered / 48 h trailing window, so
  delivery = modeled outflow (3.43 cfs at 339.95, 3 logs, today's spillway) + area × rise =
  **2.3–4.6 cfs**. The log's "~0.2 in/day still rising" sits inside that spread; with today's
  spillway it alone gives 4.2 cfs, not 3.7.
- **The answer depends on the spin-up start.** For 3.7 cfs with the true rain: **0.34** (seasonal
  seed 8 d before T), **0.49** (10 d, the live window), **0.085** (14 d, which takes in the May 29
  rain). The seasonal groundwater seed alone supplies ~1.4 of ~3.3 cfs at T.

**Why hold 0.31 rather than move toward the ~0.34–0.49 the live-style replay suggests.** Raising
`PERC_coeff` *lowers* flood peaks (more percolation, less interflow). From the 2026-11-05 live
state, Apple QPF + 3 days at 1.5 / 3.0 in/day: 0.10 → 341.49 / 342.90 ft, 0.31 → 341.18 / 342.59,
0.50 → 341.03 / 342.32; Step 6 343.12 / 342.91 / 342.72. The 72 h post-rain level moves only
~0.1 ft across the range. An unidentified change toward lower peaks is exactly what the
calibration safety veto rejects, so 0.31 stays.

**Next:** re-fit on the November 2026 atmospheric-river recession. With the lake spilling over the
boards, outflow is tens of cfs, far above the ~±0.1 ft gauge wander, and the true rain is now
archived. Script the procedure that time (target, spin-up, solve) so it is reproducible.

### 2026-11-04 — `PERC_coeff` 0.31 → 0.49 with the live hindcast on true rain (supersedes "held")

**Operator decision, same day:** forecasts are visibly over-predicting and the system isn't relied
on as production yet, so the correction was made rather than waiting for the AR re-fit.

- **Value.** Solved on the *exact* alert-path spin-up — `predict()`'s 240 h trailing hindcast from the
  seasonal seed, ending at T = 2026-06-13 06Z (last rain + 66 h), 3 logs, true rain (1.94 in) — for
  the unchanged 3.7 cfs target: `PERC_coeff = 0.489 → 0.49` (delivery 3.70 cfs; 0.31 gave 3.26).
  This is the value consistent with how the live predictor now spins up, not a firm one: the
  previous entry's caveats (target within gauge noise; 0.085–0.49 by spin-up start) all stand.
- **Shipped with the live hindcast switch** (training/serving consistency): `fetch_snapshot` and
  `fetch_conditions` now read hourly rain as the daily accumulator's per-hour rise
  (`HAConfig.rain_sensor` now defaults to `sensor.gw3000b_daily_rain_piezo`; the rolling
  `hourly_rain_piezo` and `hourly_from_accumulator` are gone). `rainfall_has_gaps` is set when the
  history can't be retrieved or the accumulator is unavailable for ≥ 6 h of the window
  (`RAIN_GAP_DEGRADED_HOURS`, the estimator's gap tolerance). With the old ~1.5×-high hindcast rain
  the same solve gives ~0.07, so the two must not be split.
- **Anchors:** Step 6 **342.73** (was 342.91; inside 343.1 ± 0.5 but only ~0.13 ft above the 342.6
  floor), dry-eq 339.670. 297 tests pass.
- **Effect** (2026-11-05 live state, start 338.94, Apple QPF + 3 days at the stated rate; PERC part
  only): 4.0 in 340.20 → 340.16, 8.5 in 341.17 → 341.03, 10 in 341.60 → 341.41, 13 in
  342.58 → 342.32 ft. The true-rain hindcast takes off a further few hundredths.

**Finding — the bigger near-term over-prediction is the seasonal groundwater seed.** From the
2026-11-05 live state with **zero** forecast rain, the model raises the lake **+0.17 ft in 72 h**
(338.83 → 339.00), while the gauge has been flat at about the sill since mid-August. The November
`seasonal_agw_default_in` seed (0.40 in) plus percolation from the spun-up soil feeds ~1 cfs of
standing baseflow into a below-crest lake with no outflow to balance it (and the model has no
open-water evaporation). It is the same drift that makes `estimate_state` fall back in October.
`seasonal_agw_default_in` is *provisional*; it was not changed here.

### 2026-11-04 — Tried and rejected: a field-capacity threshold on percolation

**No code or parameter changed** (the experiment was reverted). Hypothesis: the model lacks a
soil-moisture deficit, so a dried-out fall soil leaks to groundwater and over-drives the below-crest
lake. Test: no percolation below `FC × LZSN`, the curve rescaled over [FC, LZSN] (unchanged at
saturation), with `PERC_coeff` re-solved each time to the June 3.7 cfs target.

| FC (× LZSN) | PERC solve | Step 6 | no-rain 72 h (today) | fall storms |
|---|---|---|---|---|
| 0 (current) | 0.49 | 342.73 | +0.17 ft | over-predicted |
| 0.1 | 1.00 | 342.24 ✗ | +0.15 | unchanged |
| ≥ 0.2 | hits the 4.0 cap | 341.3–341.9 ✗ | +0.03 to +0.12 | unchanged |

With the spin-up replaying the archived true rain from 2026-02-21 instead of the 10-day
seasonal-seed hindcast: FC 0 solves at `PERC_coeff = 0.42` (Step 6 342.80) and the no-rain drift
halves to **+0.10 ft / 72 h**, but every FC > 0 again runs to the cap and breaks Step 6.

**Why it fails.** The June delivery (3.7 cfs at last rain + 66 h) needs percolation from a soil the
model holds fairly dry (0.65 in, ~14 % of LZSN, even after the wet spring: June PET and drainage
empty it fast). A threshold blocks exactly that recharge, so `PERC_coeff` balloons and drains the
saturated Step 6 soil. Either the June target is lower than 3.7 cfs (it is within gauge noise,
2.3–4.6 cfs) or that baseflow came from a longer-memory store charged in spring, which the single
23-day store cannot represent.

**The fall over-prediction is lake-side, not watershed-side.** Even when standing baseflow is cut
to ~0.03 cfs, the six Aug 29–Oct 27 storms are still modeled at +0.07 to +0.18 ft against observed
−0.03 to +0.11, and several observed rises are *smaller than the rain falling on the lake itself*.
Something sheds water at or near the 338.8 sill that the model doesn't have: seam leakage at the
bottom boards (zero in the model at the sill), open-water evaporation, or a stop-log count / datum
that differs from the model's assumption. **Field check first:** how many boards are actually in,
and is the lake leaking or spilling at the sill now?

### 2026-11-04 — Live forecast spins up from the archived true rain; `PERC_coeff` 0.49 → 0.43

**Change.** `antecedent.replay_subsurface` builds the watershed state (SM, S_if, S_agw, lag pipe,
canopy) at the forecast time by seeding seasonally at the start of the gap-free archived record
(capped at 8 groundwater half-lives, ≈182 d) and replaying the true rain forward. Unlike the
backtest's `estimate_state` it has **no lake-level fit gate**: soil and groundwater don't depend
on lake level, and the forecast starts the lake at the gauge, so the lake-side physics the model
lacks (evaporation, below-sill seepage) can't corrupt it. `LiveHASource.with_archive_spinup` tops
the archive up with the pull's own trailing hours and puts the state on the bundle
(`InputBundle.spinup_state`) for both the alert job and `/live/predict`. **Fallbacks:** the
10-day seasonal-seed hindcast when less than 10 gap-free days end at the forecast time, or when
the live rain is degraded (keeps the #4 seasonal floor, which also guards the spin-up branch).
`input_summary.spinup` records which ran.

**`PERC_coeff` re-solved on the same replay** for the unchanged June target (3.7 cfs at
2026-06-13 06Z; 111 d of history from 2026-02-21, 18.8 in): **0.428 → 0.43**. Step 6 **342.79**,
dry-eq 339.669. All caveats on this target still apply.

**Effect** (2026-11-05 live state, archive replay of 77.6 d — the replay starts after a 9 h HA
outage on 2026-08-18; deployed = 0.49 + 10-day hindcast → new):
no rain 72 h **+0.165 → +0.092 ft**; Apple 4.0 in 340.15 → 340.06; 8.4 in 341.03 → 341.03;
12.9 in 342.32 → 342.37. The near-term drift halves; big-storm peaks are unchanged (the lower
`PERC_coeff` sends slightly more rain to interflow).

**Still open.** The remaining +0.09 ft / 72 h no-rain rise is standing baseflow into a lake the
gauge shows flat; with no outflow since August (operator, 2026-11-04: the lake hasn't reached any
outflow, boards or not), the balancing losses are open-water evaporation (tiny in November:
`monthly_pet_in` 0.46 in ≈ 0.004 ft / 72 h) and seepage. Short HA outages (≥ 6 h) cut the replay;
filling them from the daily accumulator's before/after values (no change → 0 rain) would keep the
full history.

### 2026-11-04 — Open-water evaporation from on-site data; seam leakage → 0; `PERC_coeff` 0.43 → 0.42

**Data.** No outdoor met sensors on the GW3000B (indoor T/RH only), but HA has:
`sensor.carport_temp` / `_corrected` (operator's piecewise-linear correction of a warm-reading
sensor) and `sensor.carport_humidity` (hourly statistics from 2026-05-25), GW3000B/WH85
`sensor.gw3000b_wind_speed` (from 2026-02), and `sensor.lake_temperature` — a probe ~1 ft under
the surface near shore, mostly shaded (usable 2025-07-29 → 2025-10 and 2026-09 → now; it reads the
−40 °F disconnected sentinel 2025-11 → 2026-05; battery 1.48 V). The RH is relative to the *raw*
(warm) sensor temperature, so vapor pressure is taken as `RH × es(T_raw)` and the corrected
temperature is used for the air; using the reported RH directly would overstate evaporation.

**Estimates (in/day; monthly means, 2026).**

| | FAO-56 ref. ET (Hargreaves Rs, Krs 0.16–0.19) | Penman open water | Mass transfer (lake probe) | Model's `monthly_pet_in` |
|---|---|---|---|---|
| Jul | 0.17–0.18 | 0.24–0.26 | — | 0.136 |
| Aug | 0.15–0.16 | 0.22–0.23 | — | 0.120 |
| Sep | 0.09–0.10 | 0.13–0.14 | **0.17** | 0.073 |
| Oct | 0.05 | 0.08 | **0.11** | 0.031 |
| Nov (4 d) | 0.03 | 0.05 | **0.07** | 0.015 |

Mass transfer is `E = 2.6 (1 + 0.54 u₂)(es(T_w) − e_a)` mm/day (Penman 1948 wind function) on the
daily-mean water temperature; hourly vs daily-mean makes no difference (typical daily swing
3–5 °F). In fall the lake runs **2–3 °C warmer than the air**, releasing summer heat, so it
evaporates at roughly twice the radiation-based rate.

**Cross-check against the lake.** With no outflow (the lake has been below any outlet since
August — operator, 2026-11-04), a dry spell's drop + rain is a *floor* on evaporation. Jul–Aug
dry spells: **0.14–0.18 in/day**, matching FAO reference ET and above the model's PET table.
The Penman open-water estimate would need ~0.5 cfs of summer inflow to reconcile; the simpler
reading is evaporation ≈ reference ET for this small, sheltered lake. In fall, evaporation from
the probe minus the observed drop gives the **watershed inflow**: Sep 6–12 ≈ 0.5 cfs (a week after
the Sep 5 storm) → Sep 26–Oct 15 ≈ 0.2 cfs → Oct 18–26 ≈ 0 (within noise).

**Implemented.**
- `lake_evaporation_in_per_day` (artifact table) is removed from the lake in `m5_lake_update`,
  reported as `q_lake_evap_cfs` and as an *evaporation* term in the factor breakdown and UI chart.
  Jun–Aug from reference ET, Sep–Nov from mass transfer, Dec–May extrapolated (provisional).
  `infer_s_agw` includes it in its water balance.
- **Seam leakage `cfs_per_ft2` 0.040 → 0.** With July evaporation (~0.6 cfs over the lake) in the
  balance, the 3-log dry-equilibrium anchor holds at **339.667** (was 339.668) with no seepage at
  all: the ~0.6 cfs previously attributed to leakage was evaporation. Registry `min` lowered to 0.
  The two leakage-mechanism tests now use an explicit coefficient.
- **`PERC_coeff` 0.43 → 0.42** on the same archive-replay June solve. The June delivery target
  becomes 3.70 − 0.69 (no seam seepage at 339.95 ft) + 0.68 (June lake evaporation) = 3.69 cfs.
- **Anchors:** Step 6 **342.80**, dry-eq **339.667**. 302 tests pass.

**Effect** (2026-11-05 live state, 77.6 d replay; deployed → new, which also includes the
archive-replay spin-up): no rain 72 h **+0.164 → +0.082 ft**; 3.9 in 340.15 → 340.07;
8.4 in 341.03 → 341.04; 12.9 in 342.32 → 342.38 (no seepage at high stage: slightly more cautious).

**Top open item — fall baseflow is 3–6× too high.** The replayed model baseflow is 0.79 cfs
(Sep 9), 1.71 (Oct 5), 1.22 (Oct 22), 1.29 (Nov 5), against the lake-implied ~0.5 → ~0.2 → ~0.
The model's groundwater even *rises* after the late-September storms, which the lake never
showed: too much fall rain percolates to the single 23-day store. Lowering recharge conflicts
with the June 3.7 cfs target, so it is structural (one store for a wet-spring and a dry-fall
regime, plus no soil-moisture deficit — the field-capacity attempt above). It is the remaining
+0.08 ft / 72 h no-rain drift. **Re-fit `PERC_coeff` / `AGWRC` / `DEEPFR` jointly on the
November atmospheric-river recession**, with evaporation now computable alongside.

**Sensor notes.** The lake probe's winter dropout (−40 °F) and low battery will leave Dec–May
evaporation provisional; a full year of probe + carport data would let the table be derived
throughout, or replaced by a daily station-driven estimate.

## Structural findings & open items

- **Fall baseflow 3–6× too high (top open item, 2026-11-04).** Lake-implied inflow after the fall
  storms was ~0.5 → ~0.2 → ~0 cfs; the replayed model holds 0.8–1.7 cfs. One 23-day groundwater
  store can't serve both the June 3.7 cfs target and the dry fall; it's the remaining ~+0.08 ft /
  72 h no-rain drift. Re-fit `PERC_coeff` / `AGWRC` / `DEEPFR` jointly on the Nov 2026
  atmospheric-river recession (evaporation is now in the balance). See the evaporation entry.
- **Initial-state spin-up — history-replay seam added for the backtest (2026-07-07/08).**
  `antecedent.estimate_state` seeds seasonally and replays the recorded rain forward, a **smooth
  seasonal→historical blend** capped at **8 groundwater half-lives ≈ 182 d** (`DEFAULT_CAP_HALF_LIVES`,
  a parameter): it leans seasonal with little history and becomes essentially historical by the cap,
  where the seed has drained to ~1 % (effectively no seasonal). The backtest uses it whenever any
  usable history exists. **Open:** (a) extend to the live/alert predictor as a *floor-only*
  correction (preserve the #4 safety asymmetry — never let it lower a warning); (b) the analytic
  `antecedent.infer_s_agw` (dry-recession baseflow inversion) is retained as a standalone tool but
  not used by the estimator; (c) `cap_half_lives=8` and the tail-RMSE 0.5 ft gate are judgement
  calls — a saturated wet-season seed followed by an anomalously dry stretch drains slower, but the
  tail-RMSE gate catches that mismatch and falls back. **Warm-up:** the cap is also roughly the
  archive length at which the estimate is fully data-driven (~6 months); with less, it's a blend
  leaning seasonal. Revisit `cap_half_lives` against a real multi-month record.
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
