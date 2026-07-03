# Calibration & field-observations log

This file is the **source of truth for why each calibrated parameter has the value it
does**, grounded in field measurements and gauge-derived analysis. The research basis is
[`baseflow_parameter_research_brief.md`](../baseflow_parameter_research_brief.md); the
values themselves live in [`artifacts/crystal_lake_v0.json`](../artifacts/crystal_lake_v0.json)
(each has an inline `*_comment` with its provenance). Keep those comments and this log in
sync.

> **Maintain this file.** When you (human or agent) change a calibrated parameter, make a
> field/gauge observation, or resolve an open item, add a dated entry below and update the
> parameter table. Calibration here is grounded in real observations — do not silently
> re-tune a value away from a gauge-anchored number without recording why.

---

## Calibrated parameters & provenance

| Parameter (in `crystal_lake_v0.json`) | Value | Basis | Confidence |
|---|---|---|---|
| `hspf.AGWRC_per_day` | **0.97** (~23-day half-life) | Moderate subsurface store. The Ecology Till nominal 0.996 (~173 d) returned ~nothing within an event window, so soil drainage routed there was lost to the near-term budget and the modeled lake drew down after rain while the gauge held/rose. Within EPA Tech Note 6 typical 0.92–0.99. | reasoned; gauge-consistent |
| `hspf.PERC_coeff` | **0.20** (2× HSPF nominal) | **Calibrated to the 2026-06-12 gauge recession** (see log). Reproduces the observed ~3.7 cfs sustained baseflow and the rising post-rain trend; at 0.10 the model gave ~2.5 cfs and fell. Also sets post-rain dynamics (soil keeps feeding the store for days → baseflow still building → lake rising). | gauge-calibrated (1 event, 1 season) |
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
internally disputed. Fix #1b (below) addresses the disputed-geometry half.

### 2026-07-03 — Fix #1b: (pending) minimal volumetric geometry

Planned, not yet done. Switch `surface_area_acres` to return the analytic derivative of the
`stage_storage` curve (`dS/dh = 2a·x + b`) so the surface area the lake-level update uses is
*consistent with* the storage curve we treat as authoritative (the intended-but-never-wired
"measured geometry" — `storage_acft` has been dead code since the initial commit). Remove the
now-redundant `stage_area` block from the artifact + schema. This is a **consistency** fix, not
a validated-accuracy one: the two curves only differ where the lake has never been gauged, so
the change can't be empirically checked — only made self-consistent. Expect the dry-eq anchor
(low in the range, ~2% area change) to hold and the Step 6 peak (high, ~7–8% larger area →
less rise) to drop toward/through its 342.6 floor. **If it breaks the floor, do not re-tune
`PERC_coeff` (pinned to the June recession) to chase a modeled target** — first revisit whether
the Step 6 target itself is geometry-consistent (see the emergency-review entry's honesty
ladder).

---

## Structural findings & open items

- **The flood-peak vs sustained-recession tension is structural.** A single lumped interflow
  reservoir cannot both produce the sharp Step 6 flood peak *and* sustain a moderate-storm /
  post-rain recession — it's the same water, delivered with opposite timing. Every lever that
  sustains the recession (more percolation, slower interflow) attenuates the Step 6 peak. The
  genuine fix is a **two-timescale subsurface**: a fast saturation-excess path for extreme
  storms plus a days-to-weeks interflow/shallow-GW store for the sustained tail. **This is the
  main outstanding model-improvement project.**
- **Step 6 margin is tight.** The peak is now **342.78 ft** vs the 343.1 ± 0.5 anchor → only
  ~0.18 ft above the floor. Baseflow/percolation increases trade against the flood peak, so
  `PERC_coeff ≈ 0.20` is near the practical ceiling for the current single-reservoir structure.
  Watch this anchor on any subsurface change.
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
- **Geometry above 343.1 ft is silently extrapolated and internally inconsistent** —
  `in_valid_range()` has no callers, and `stage_area` vs. d(`stage_storage`)/dh diverge more
  as stage rises (6.4% at the dam crest, 7.6% at 343.1 ft). Exactly the dam-crest/bridge-deck
  regime. See 2026-07-03 entry.
- **A zero live-forecast reading during an active NOAA QPF alert caps crossing probability
  near 0.5**, structurally preventing CRITICAL/EVACUATE from firing regardless of the NOAA
  severity (`scenarios.py` zero-total branch + `predict._exceedance_probability` point
  collapse). See 2026-07-03 entry.
- **No fast-runoff/overland path — `INTFW` is loaded but dead code.** Rainfall intensity
  barely affects arrival timing, so rapid/convective storms are predicted systematically late.
  See 2026-07-03 entry.
- **Rainfall-gap handling only biases the hindcast state dry**, flagged by a boolean with no
  downstream effect on bands/confidence. See 2026-07-03 entry.

---

## How to use this when changing the model

1. Before re-tuning a parameter in the table above, read its provenance here and in the
   artifact comment. If it's gauge-calibrated, treat the gauge number as the target.
2. After any subsurface/spillway change, re-run `lake-rise validate` (both anchors) and
   `pytest`; if you have HA access, re-run the post-rain comparison against the live trace.
3. Record the change here with a dated entry and update the artifact `*_comment`.
