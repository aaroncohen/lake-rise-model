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
| `hspf.PERC_coeff` | **0.42** (~4× HSPF nominal) | **Calibrated to the 2026-06-12 gauge recession** (~3.7 cfs sustained delivery 66 h after rain). 0.10→0.20 (06-12) →0.31 (07-03, after the #3 interflow change) were fitted with ~1.5×-high rain (2.89 vs 1.94 in). **Re-derived on 2026-11-04** with the true rain: 0.49 on the 10-day seasonal-seed hindcast, then 0.43 on the archive-replay spin-up the live forecast now uses (same code for fit and forecast), then **0.42** once lake evaporation entered the balance and leakage went to 0 (June target 3.70 − 0.69 + 0.68 = 3.69 cfs). The event constrains it weakly (target 2.3–4.6 cfs within gauge noise; solve 0.085–0.49 by spin-up start). Re-fit on the Nov 2026 AR recession. **2026-11-27:** with the surveyed crests the June target re-derives to **3.10 cfs**; the refit (0.13) worsens every November backtest, so 0.42 stays (June 3.52, inside the band). | weakly constrained (1 noisy event); alert-path-consistent |
| `hspf.IFEXP` | **3.0** (was 2.0 = `INFEXP`) | **New 2026-11-14.** Interflow-generation exponent, split from `INFEXP`: a (SM/LZSN)^IFEXP share of rain sheds to interflow. From the fall 2026 below-crest storms, mainly Nov 9–10 (0.156 ft modeled at 2 vs ~0.01 measured; 0.048 at 3). Lowers Step 6 342.80 → 342.72. | provisional (one storm) |
| `deep_zone` | capacity **4 in**, et_coeff **1.0**, gate_exp **2** | **New 2026-11-14.** Deficit-gated recharge: percolation refills the deep zone before recharging groundwater; deep roots draw it down only for ET the soil can't meet. Hand-fitted to June 2026 (3.52 cfs vs 3.7 target) against the eight fall 2026 storms (0.24 ft modeled vs ~0.09 measured, from 1.00). capacity 2–6 in and gate_exp 1–2 fit about as well. | provisional |
| `ensemble_rain.calibration_by_day` | per lead day, e.g. day 1 mean_scale **0.945**, spread_scale **0.889**, added_spread **0.218** | **New 2026-11-14.** Square-root-space bias/spread correction of the 50 ECMWF members that now drive the live forecast. Fitted by quantile score to the gauge over 531 runs (2026-02-21 to 11-13); out of sample it leaves 9–13 % of forecast-wet periods above the 90th percentile (raw 25–26 %). Replaces `uncertainty.lead_ratio_by_day` live; that band is now only the fallback. **Since 2026-11-14 (later entry) the crossing chances are member counts**: the share of the 50 peaks reaching a level, in 2 % steps; the fallback band keeps its smoothed CDF. | gauge-calibrated |
| `spillway.leakage.cfs_per_ft2` | **0.0** (no seam seepage) | 0.0557 → 0.040 (06-12) → **0 (2026-11-04)**: with open-water evaporation in the lake balance the 3-log dry-equilibrium holds at 339.667 ft with no seepage — the ~0.6 cfs once attributed to leakage was evaporation. Operator: no outflow through the boards. Mechanism kept for a future gauge-clean re-fit; below the documented 0.5–2.0 cfs seepage bounds, which are not enforced. | gauge + operator consistent |
| `lake_evaporation_in_per_day` | monthly table, 0.03–0.17 in/day | **New 2026-11-04.** Jun–Aug: FAO-56 reference ET from the carport T/RH + WH85 wind, cross-checked by the no-outflow Jul–Aug drawdown (0.14–0.18 in/day). Sep–Nov: mass transfer from the lake probe (lake 2–3 °C warmer than the air). Dec–May extrapolated. | measured Jun–Nov; provisional Dec–May |
| `baseflow_assimilation` | 7-day window; gates rain ≤ 1.5 in, quickflow ≤ 0.5 cfs, SE ≤ 0.5 cfs, 0.05 ft below crest; scale 0–3 | **New 2026-11-05.** Scales the replayed groundwater store at T0 to the lake's measured water balance (stopgap for the single store's fall over-delivery). Gates set from the Jul–Nov 2026 archive. **Mostly idle since 2026-11-14:** with the deep zone the replayed fall baseflow is below its 0.1 cfs floor. | provisional |
| `seasonal_agw_default_in` | seasonal table | Standing-baseflow seed so the slow store isn't empty at the start of every prediction (the short hindcast can't charge a multi-week store). Provisional magnitudes from a recharge-fraction estimate; the **seed sets the baseline, not the post-rain trend** (that's `PERC_coeff`). | provisional |
| `hspf.IRC_per_day` | 0.5 | Brief Till value. Fast interflow, ~1-day half-life. **Note:** higher IRC = *faster* drain in this model (`1-(1-IRC)^(dt/24)`). | brief |
| `spillway` weir law | exponent **1.5**, C≈1.9 | **Corroborated** — both legs independently imply the same C≈1.9; enforced by `test_m6_physical_crest_lengths_corroborate_reported_capacity`. Do **not** change the exponent to reduce spill without strong cause; it breaks that corroboration. | corroborated |
| `spillway.overtopping.bridge_deck_elev_ft` | **342.7** (= crest 342.2 + 0.50 ft) | **EAP-grounded.** The crest sags to a low point ~25 ft east of the bridge (overtopping starts) and rises 0.50 ft to the bridge deck (full crest engaged). Low point anchored to the established dam-crest 342.2; the **0.50 ft sag→bridge interval** is the EAP's solid *relative* number (sensor 3.90→4.40 ft). Effective weir length grows linearly 0→60 ft over that interval. | EAP-grounded (relative); absolute tied to dam-crest |
| `stop_logs` (per-spillway crests) | 1 log: main **0.98**, aux **0.99** ft stick; **0.417 ft (5 in) per log**; winter normal **1**, summer normal **2** | **Laser-level survey, 2026-11-25** (user). Counted the EAP's way: "1 log" = the aux's single log + the main's three 3.5-in base boards. Each log raises both ~5 in; 0 logs = the aux log out (aux 0.573) with the main's base boards in (main 0.98). Below 1 log the legs differ; the main's bottom base board is mortared, so in an emergency only two boards come out (main 0.397). Replaces the EAP-derived board heights (2026-11-05). | measured (crests); log height approximate |
| `spillway.{primary,auxiliary}.rated_crest_elev_ft` | 338.8 / 340.0 | The crests the documented 110 / 40 cfs were rated over; they set each leg's C (~1.9) and Step 6's spillway only. Since 2026-11-25 both legs spill over their surveyed stop-log crests; the aux is no longer a fixed 340.0. | reasoned (documented ratings) |
| `lake_filter` | **dry weather only** (P16); `wet_window_hours` **3**, `offset_minutes` **30**, `rate_sd_wet_ft_per_min` 1e-4, `wet_full_in` 0.1; the rest the prototype's | **New 2026-11-15; dry-only since 2026-11-16 (user).** The live lake reading's Kalman filter (docs/plans/lake-filter.md). Any rain, or unknown or stale rain, within `wet_window_hours` gives no estimate, so the live reading is the 30-min median; the wet values only shape the transition back to dry. Picked by `lake-rise lake-filter-study` (216 combinations, every one passing P12; pick: p99 no worse than the median's, then coverage): dry jitter 0.009 ft against the median's 0.025, p99 0.039 against 0.040, the live anchor on 49 % of hourly calls. Known limit: a rise while the rain gauge reads zero. Re-run after the first wet-season storm and the laser remount. | provisional (synthetic storms on real noise) |
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

*(Action wording and contacts superseded by the revised action table, 2026-11-23 entry. The
3.90 / 4.40 ft overtopping geometry below is unchanged.)*

- **3.30 ft** (~341.68 abs) — "water up two feet" → Mandatory Alert (DSO/SMO/RCEC), begin sandbagging.
- **3.90 ft** (~342.28 abs) — **overtopping starts, 25 ft east of the bridge**; close the bridge.
- **4.40 ft** (~342.78 abs) — **bridge deck just overtopped**; "imminent failure" → evacuate. Failure
  criterion is overtopping by 6 in for 12 h. *(Corrected 2026-11-23: the 6 in is over the low point,
  i.e. this 4.40 ft deck level held 12 h — not 6 in over the deck, ~343.2 abs, as first read here.)*

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
the surface near shore, mostly shaded. Two sensors share the entity: the **old** probe (2025-07-29 →
2026-05-28) had a 40 °F floor and was later damaged, so it reads the −40 °F sentinel from 2025-11
on; the **replacement** (reporting since 2026-09-03) reads to at least freezing. Both run on AA
cells: battery 1.60 → 1.46 V over the old probe's 10 months (a normal light-load decline), 1.50 →
1.48 V on the new one since September — healthy, not low. The RH is relative to the *raw*
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

**Sensor notes.** Dec–May evaporation is provisional only because the replacement probe started
2026-09-03 (the old one couldn't read below 40 °F). It reads to at least freezing, so this winter
can be measured: re-derive Dec–May from probe + carport data as it accumulates, or replace the
table with a daily station-driven estimate.

### 2026-11-05 — Backtest uses the same archive-replay spin-up as the live forecast

**Finding.** The backtest still seeded T0 from `antecedent.estimate_state`, whose 0.5 ft
level-fit gate rejected at every lookback this fall (the modeled lake drifts from the gauge —
the fall-baseflow issue), so every backtest silently ran the 10-day seasonal-seed hindcast: a
different starting state from the live forecast it is meant to score. Storm records froze no
state at all, so offline scoring had the same gap.

**Change.** `_backtest_inputs` computes the T0 state with `LiveHASource.archive_spinup` (the live
forecast's `replay_subsurface`, topped up with the pulled rain before T0). `fetch_backtest` runs
from it (`gw_seed_source: "assimilated"`), `capture_storm` freezes it into
`StormRecord.spinup_state`, and `storm_record.score` replays from it, so offline scoring equals
the live backtest. Fallback when the archive can't cover T0: the short seasonal-seed hindcast,
as live. `estimate_state` is no longer on any live path; it stays as a diagnostic, like
`infer_s_agw`. `predict.spinup_to_state` is shared by the predictor and the backtest.

**Effect on the backtest** (2026-11-05 08Z, same observed inputs; before = deployed seasonal
fallback → after = archive replay, 69–77 d of history):

| lookback | RMSE (ft) | peak err (ft) | end err (ft) | predicted vs actual change (ft) |
|---|---|---|---|---|
| 24 h | 0.070 → 0.062 | +0.014 → −0.008 | +0.064 → +0.042 | +0.059 → +0.037 vs −0.005 |
| 48 h | 0.073 → 0.056 | +0.042 → −0.001 | +0.092 → +0.049 | +0.107 → +0.064 vs +0.015 |
| 72 h | 0.157 → 0.124 | +0.151 → +0.090 | +0.206 → +0.145 | +0.151 → +0.090 vs −0.055 |
| 120 h | 0.217 → 0.155 | +0.229 → +0.121 | +0.314 → +0.206 | +0.249 → +0.141 vs −0.065 |
| 168 h | 0.249 → 0.166 | +0.303 → +0.158 | +0.388 → +0.243 | +0.358 → +0.213 vs −0.030 |
| 216 h | 0.235 → 0.160 | +0.311 → +0.165 | +0.401 → +0.255 | +0.446 → +0.300 vs +0.045 |

RMSE falls 12–33% and peak error roughly halves beyond a day. The remaining over-prediction
(still +0.04 to +0.26 ft at the end) is the fall-baseflow open item: the model's groundwater
delivers ~1.3 cfs where the lake implies ~0.

### 2026-11-05 — Data we keep for accuracy; gauge dropouts in the rain; correctness pass

**Gauge dropouts (live, 2026-11-04 afternoon onward, in the rain).** The laser (Ecowitt LDS01)
intermittently loses the water surface (~23–25 in air gap) and reads the lake bottom (~46 in).
`sensor.air_gap_filtered` rejects those, so `sensor.crystal_lake_depth` goes `unknown` and the
10-min statistics helper `crystal_lake_depth_smoothed` (keep-last-sample) holds its last value with
`source_value_valid: false`, while the raw air-gap sensor (our liveness signal) keeps reporting.
Nov 4 14:25 → Nov 5 07:00 PST there were ~14 invalid windows, mostly 1–20 min; the longest were
20:33 (48 min), 22:02 (46 min) and **23:32–00:25 (53 min)**. Each time the gauge recovered on its
own, and valid readings agree with the prior week (0.4–0.55 ft). An earlier note here called this
a sustained failure from 23:33; that was a snapshot taken inside the longest dropout. The sensor
is working, but expect more dropouts in heavy rain. During each dropout, forecasts were anchored to the held value and
flagged fresh, the observed EAP check saw a flat lake, and the archive carried the held level
forward for up to 6 h. **Fixed:**
`LiveHASource.gauge_problem` treats `source_value_valid: false` as a degraded gauge for the
forecast's freshness flag (→ #4 seasonal floor) and the observed EAP reading (not confirmed);
the archive's level now comes from the raw `crystal_lake_depth` and stops carrying a level forward
once the sensor goes invalid. Worth a look before the atmospheric river: if a rain-shield or
mounting change can keep the water-surface reading in heavy rain, the dropouts will be shorter.

**False "rain gaps" at HA's retention edge (found live, fixed).** From 05:00 PST Nov 5 every
forecast reported `rainfall_has_gaps: true` / `data_fresh: false`, which discards the archive
spin-up and floors the state at the seasonal seed (SM 0.50 → 3.15 in): a strong upward bias on
any forecast rise. Cause: the trailing rain window is 10 days and HA keeps ~10 days of raw
history. The daily accumulator writes a row only when it changes, so after the dry spell at the
window's start the nightly purge removed the row holding its value. HA then returns no
start-state row, and the ~20 h before the first retained row counted as an outage (≥ 6 h ⇒
degraded). `live_ha._rain_gap_hours` now counts only hours from the oldest retained row onward.
A real outage still records `unknown` rows. Verified against live HA: no gaps, archive replay,
SM 0.75 in.

**Gauge daily cycle (verified, cause open).** HA hourly statistics show a 0.05–0.11 ft
hour-of-day cycle on the flat, outflow-free lake (Jul 1–16 0.056, Aug 1–28 0.107, Oct 18–27
0.055 ft) — the size of the fall storm rises and of the June 66 h rise-rate target. Its relation
to air temperature is inconsistent (−0.02, +0.01, −0.02 ft / 10 °F; the low hour moves 18:00 →
08:00), so a temperature or sunlight effect on the laser or its mount is a candidate, not a finding (the
LDS01 is rated for less range in daylight than in darkness; see the 2026-11-08 entry). Use daily means for
any water-balance inversion or recession fit.

**Data now kept (and used).**
- **Weather in the continuous archive** (`HourSample.air_temp_f`, `air_temp_raw_f`, `rh_pct`,
  `wind_mph`, `water_temp_f`), hourly means from HA long-term statistics: backfilled at startup
  over the full window and re-merged each hourly tick. Out-of-range values (the old lake probe's
  −40 °F sentinel, water below 32 °F) are stored as missing. Shards omit unrecorded fields.
- **Dry HA-outage gaps in rain are filled.** If the accumulator's statistics `sum` is unchanged
  across a gap, its hours are observed-dry 0.0, not missing. The 2026-08-19 08Z 8 h outage was
  dry, so the replay no longer stops there (~180 d of history instead of ~77).
- **Recorded lake evaporation** (`evaporation.py`): per local day, mass transfer from the lake
  probe when it covers the day, else FAO-56 reference ET with Hargreaves radiation, else the
  monthly table. Backtests and storm records run with it (`model.run(lake_evap_in_per_day=…)`);
  forecasts keep the monthly table. Site constants in the artifact (`lake_evaporation_model`).
  `lake-rise calibration evaporation` reports monthly estimates vs the table, for re-deriving
  Dec–May once the replacement probe has a winter on record (it reproduces the 2026-11-04 numbers).
- **Storm records from the archive** (`storm_record.from_archive`, `capture-storm
  --from-archive --t0 --end`): same T0 replay state and recorded evaporation as the live backtest,
  with no ~10-day retention deadline — capture the atmospheric river any time after it.
- **Forecast log** (`data/forecasts/*.jsonl`) had never been written: every deploy restarted the
  hourly alert job's 60-min first-run clock. The alert and observed jobs now first run ~1 min after
  startup, with a 4-min misfire grace.

**Correctness pass (independent review; fixed unless noted).**
- *Critical:* a failed/empty forecast collapsed every probability and could send **ALL_CLEAR**
  mid-storm — the forecast run now holds every alert track when there are no forecast hours, and
  an empty forecast is flagged degraded. Every deploy **overwrote the NAS alert/calibration state**
  with the developer's local copies (`.dockerignore` didn't exclude them; `deploy.sh` copied the
  whole `artifacts/`) — now excluded, and only the model + registry are copied. The observed EAP
  page aborted if the forecast pipeline raised — it now renders from a forecast-less fallback and
  leaves the forecast ladder alone. Live bundle I/O and the forecast log moved outside
  `ALERT_STATE_LOCK`; dry runs don't take it. A ladder level on a label `predict` never scores
  is now rejected at parse time.
- *High:* the live archive top-up overwrote archived (statistics-repaired) rain with zero-filled
  unavailable hours — archived values now win. A small dip in the daily accumulator counted as a
  midnight reset (1.50/1.52/1.51/1.52 → 1.54 in) — only a drop > 10 % (HA's `total_increasing`
  rule) resets now.
- *Low:* P(cross) is 1.0 when the lake is already over the threshold (was ~0.9); one bad SMS number
  no longer stops the rest of the fan-out; the drill uses local calendar time; archive shard
  writes are serialized across the backfill and hourly jobs. **Not fixed (noted):** the
  "gauge not reporting" notice line is shown for any degraded input; `LAKE_RISE_ARTIFACT` is
  ignored by calibration training at v0; the stop-log default flips on the UTC date (4–5 PM
  local on Mar 15 / Sep 15).

**Brainstormed accuracy improvements (Fable review; not implemented — ranked).** (1) a two-store
groundwater (shallow perched-till + deep) with deficit-gated recharge — the likely structural fix
for the fall baseflow (Step 6 is set by the interflow path; AGWRC 0.99 / DEEPFR 0.30 keep both
anchors) -- **done 2026-11-14** as the deep zone (see that entry); (2) the gauge daily cycle above; (3) a simple observed-inflow bias correction
(assimilation) with the safety asymmetry kept in the alert cutoffs; (4) storm-total QPF bands fit
from the forecast log instead of the comonotonic per-hour placeholder; (5) log met.no / NWS QPF
beside Apple (**done 2026-11-05, NWS only**); (6) a stop-log `input_number` helper (**done
2026-11-05**, with per-board heights); (7) datum from the fill-to-spill and
recession knees; (8) `signatures.recession_agwrc` should fit watershed inflow, not lake outflow;
(9) runoff coefficient and lag from the first fill; (10) a flagged 168 h outlook.

### 2026-11-05 — Per-board stop-log heights; operator stop-log helper

**Boards are not all the same height.** The model assumed 0.292 ft per board (the 0.875 ft
3-board stack split three ways). The operator reports that the boards differ, the base and second
in particular. The artifact now holds each board's height, base first (`board_heights_ft`), and
the crest with N boards is the sill plus the bottom N. Until the boards are measured, the heights
come from the EAP pool levels in the HA helpers. In the sensor frame (+338.375) those are 1 board
0.65 ft (339.025), 2 boards 0.97 ft (339.345) and 3 boards 1.30 ft (339.675). That gives a base
board of 0.225 ft (2.7 in) and upper boards of 0.32 / 0.33 ft (3.8 / 4.0 in), matching the EAP's
4-inch lowering steps. **Effect:** the 3-board crest and both anchors are unchanged (Step 6 runs
at 0 boards, dry-equilibrium at 3). The winter 2-board crest drops 339.383 → **339.345**
(−0.04 ft), so winter spill starts slightly sooner. The 1-board crest drops 339.092 → 339.025.
*Caveat:* each pool level sits a little above its crest (weir head), so these heights are close
but not exact. A tape measurement of each board replaces them.

**Operator helper.** `input_number.crystal_lake_stop_logs` (0–3 boards; set to 2 on creation) is
now the live board count. The seasonal default (summer 3 / winter 2) is used only when the helper
is missing, unavailable or out of range. The continuous archive records the count each hour
(`HourSample.stop_log_count`; the helper's raw history lasts only ~10 days, and an `input_number`
gets no long-term statistics). Archive storm captures use the recorded count at T0. **Set the
helper whenever boards go in or come out**, especially during the atmospheric river.

### 2026-11-05 — NWS precipitation forecast logged beside Apple's

Every hourly forecast-of-record line now also carries the NWS gridpoint forecast for the site
(`nws_outlook`: grid cell SEW 133,75, issue time, QPF in its native 6 h blocks, PoP intervals).
HA's NWS entity has PoP only, so this comes from api.weather.gov directly. It is **logged only,
not modeled**: the forecast still runs on Apple WeatherKit. With both on record for the
atmospheric river, each source's storm totals and timing can be scored against the gauge's rain.
That decides whether NWS, Apple or a blend should drive the scenarios, and it feeds the
storm-total uncertainty bands (brainstorm #4). First fetch (Nov 5, 18 UTC issue): NWS 0.51 in
over the next 72 h, 2.2 in over 7 days.

### 2026-11-05 — Groundwater store matched to the lake's water balance (backtest over-rise)

**Problem.** Backtests kept predicting a rise the lake never made. Over the 10 days to Nov 5
(1.14 in rain) the model rose +0.33 ft and the gauge fell 0.02 ft. Breakdown: baseflow +0.29 ft,
direct rain +0.10, runoff +0.01, evaporation −0.06. September's 3.5 in on dry soil had charged the
single groundwater store from 0.15 to 0.66 in. Dry soil keeps percolating in the model (≈0.02
in/day at 11 % of LZSN, more than the store drains), so the store held 1.2–1.4 cfs of baseflow
through the fall. The lake's own balance (daily medians, rain on the lake and evaporation
removed) shows 0.10–0.20 cfs mean for Jul–Oct and about 0 since late October.

**Change (stopgap, `antecedent.assimilate_baseflow`).** At T0 the replayed groundwater store is
scaled so the model's baseflow equals the lake-implied watershed inflow over the prior 7 days,
less the model's own quickflow. Each 24 h block's median of `h − cum. lake rain + cum.
evaporation` gives one point, which cancels the 0.05–0.11 ft daily cycle, and the slope through
those points is the inflow. The fit is skipped (pure replay) when window rain exceeds 1.5 in, the
model's quickflow exceeds 0.5 cfs, any block is within 0.05 ft of the crest, the standard error
exceeds 0.5 cfs, or the model baseflow is under 0.1 cfs. The scale is clamped to 0–3, so a lake
running *higher* than the model raises the store. Used by the live forecast, the backtest and
archive storm records; the `PERC_coeff` fit keeps the pure replay. Settings in
`baseflow_assimilation` (provisional).

**Fitted scales (NAS archive):** Aug 10 1.48, Aug 28 0.29, Sep 10 0.96, Sep 25 0.46, Oct 10 0.24,
Oct 20 0.25, Oct 26–Nov 5 0.0–0.02 (observed −0.04 to −0.21 ± 0.11–0.26 cfs). The window is
skipped in June (rain) and mid-July (lake at the 3-board crest).

**Backtests (to Nov 5, live HA + NAS archive):**

| Window | Rain | Gauge | Before | After | RMSE before → after |
|---|---|---|---|---|---|
| 48 h | 0.14 in | −0.055 | +0.067 | +0.004 | 0.091 → 0.053 |
| 96 h | 0.14 | −0.045 | +0.119 | +0.006 | 0.088 → 0.051 |
| 144 h | 0.38 | −0.065 | +0.189 | +0.041 | 0.147 → 0.069 |
| 192 h | 0.60 | −0.047 | +0.250 | +0.071 | 0.177 → 0.062 |
| 240 h | 1.14 | −0.020 | +0.334 | +0.101 | 0.204 → 0.069 |

**Safety direction.** This lowers predicted rises. With the store cut to 15 %, big-storm peaks
move only 0.06 / 0.03 / 0.02 ft (3 / 6 / 9 in storms), because peaks come from the interflow path.
Step 6 and the dry-equilibrium anchor don't use the replay and are unchanged.

**New finding: an unmodeled lake loss.** Since late October the lake-implied inflow is slightly
*negative* (−0.04 to −0.21 cfs). The lake falls faster than the evaporation estimate allows, even
with no watershed inflow, and that is the remaining +0.04–0.10 ft in the longer backtests.
Candidates: November evaporation underestimated (lake ~50 °F above the air; the table's 0.07
in/day), seepage through or under the dam below the sill (seam leakage is 0 in the model), or
gauge drift. Worth checking before tuning anything.

**Retire with the two-store groundwater model** (brainstorm #1): the assimilation hides the
structural error rather than fixing it. Re-check both against the atmospheric-river recession.

### 2026-11-08 — The lake gauge is a laser (Ecowitt LDS01); Nov 7 storm; remount height

**Sensor identity (correction).** The lake gauge is an **Ecowitt LDS01 laser distance sensor**
reporting through the GW3000B (`sensor.gw3000b_air_gap_1`, inches), not an ultrasonic. Earlier
notes here and in the code said "ultrasonic"; they now say laser. Published specs: range **0–3.5
m in daylight, 0–4 m in darkness** (no minimum distance given), accuracy **±2 % of the
distance**, resolution 0.01 m, measured from the sensor's bottom edge. The manual warns that in
water shallower than ~20 cm "the laser may penetrate the liquid surface" and read the bottom.
Ecowitt's FAQ ties jumpy readings to levelling (centre the built-in bubble) and to the heater
counter running above its normal < 50/day. Sources: a reseller listing (Crondall Weather) for the
range and accuracy, the ManualsLib copy of the manual, and Ecowitt's FAQ. Ecowitt's own product
page wasn't readable from here; confirm the range there.

**Mount geometry (from the HA calibration helpers).** Depth (ft) = 1.36 + (13.58 − air gap in) / 12,
so the sensor face sits at **~2.49 ft gauge ≈ 340.87 ft abs**. That is *below* the 341.0
early-warning level: the gauge can't read any storm that matters. To keep the face dry at the top
of the Step 6 band (343.6 ft) it must go up **~2.7 ft**; **~3–3.25 ft** adds margin for waves and
the ~0.12 ft datum uncertainty (no minimum-range allowance needed for this sensor). The gap at
today's level then becomes ~5–5.5 ft (≤ 6 ft at summer low), well inside the 3.5 m daylight
range. Accuracy goes from about ±½ in to about ±1.3 in at that distance; the smoothed entity
averages it down.

**Nov 7 storm, as seen by the gauge.** ~0.93 in of rain, Sat Nov 7 20:10–23:30 PST, peaking
~1.35 in/hr near 21:00. The 72 h backtest predicted +0.10 ft (338.815 → 338.918 by Sun 20:00);
the gauge reads 338.875–338.895, so the model is within ~0.03 ft. The backtest's
reported 0.2 ft peak miss is a gauge artifact:
- The air gap sat at ~46 in (laser seeing the bottom, depth `unknown`) Sat 19:00–20:04, then
  re-locked on the surface at **~22.5 in at 20:05**, six minutes *before* the rain started. Before
  the dropout it read ~24.5 in, so the lake appeared to jump +0.17 ft with no rain and held a
  false ~339.02 ft until Sun ~14:30.
- A second bottom-reading spell, Sun ~14:30–18:02 (brief surface reads at 14:39–14:45 and 15:05–15:11),
  re-locked at **~24 in**, ~1.5 in lower than before.
- Sunday evening the raw readings scatter ±1.5 in minute to minute.
The rain-roughened surface reflected the laser; calm, clear water lets it through to the bottom
(~1.8 ft below the surface at the mount). Each re-lock can land on a different reflection.
Those 1.5–2 in steps are the same size as the backtest residuals and the "unmodeled lake loss"
above, so check gauge steps before fitting anything on the atmospheric-river recession.

**Heater counter (`sensor.gw3000b_heater_on_counter_1`, HA long-term statistics).**
- **Jun 11 – Jul 7:** 25–704 heater cycles a day, averaging ~250. That is far above Ecowitt's
  normal (< 50/day) and matches the noisy readings logged then (open item below).
- **Jul 8 on:** flat at 8042. Reset to 0 on Sep 3, which looks like the sensor restarting, and 0
  ever since.
- **Last report:** HA last received the field on Oct 24 (`last_reported`), although the air gap
  still reports every minute.

There is no replacement entity in HA: the GW3000B device has only this one heater counter. The
gateway's own live data (`http://192.168.1.109/get_livedata_info`, firmware GW3000B_V1.0.9, update
available) reports no heater count for the LDS01, only air gap, battery 1 and 2.36 V.

**What triggered it in June: unstable readings, not weather.** The LDS01 has no user heater
settings. Its June–July cycles follow bad readings, not temperature or humidity. The worst days
had the most hours with bottom reads (air gap > 35 in): Jun 9 had 12 such hours and 673 cycles,
Jun 16 had 9 and 704, Jun 26 had 10 and 609. Quiet days with none ran 25–85. By hour of day
(Jun 9 – Jul 7, PST), the cycles peaked at 12:00–16:00 (≈ 500–600 per hour-of-day total) and
were lowest overnight (16–42 at 00:00–04:00). Dew would form the other way round: at night and
dawn. This matches Ecowitt's FAQ that the heater counter climbs when readings are unstable: the
firmware seems to heat when it suspects a fogged lens. The midday peak points to **sunlight**
(the sensor is rated for less range in daylight), which is also a candidate for the gauge's
daily cycle. Since Jul 8 the counter has stayed flat even on days with bottom reads, so the
heater stopped responding; it wasn't just quiet.

**Why the heater stopped is open.** The LDS01's battery (`sensor.gw3000b_lds_1_battery`) fell
from 2.82 V to 2.48 V while the heater was cycling in June, dipped to 2.24 V under load on Jul 7,
and the heater stopped the next day. Since then the voltage has barely moved (2.46 → 2.34 V),
which fits no heater load. From the unit's cell count, the operator calculates that its
low-voltage cutoff is below these levels, so the sensor isn't near shutdown. A battery cause
would need a separate, higher heater-only threshold that the Jul 7 dip crossed; the manual we
could read doesn't describe one. Other candidates: a failed heater element, or a setting or
firmware change around Jul 7–8. Either way the lens has been unheated through the fall's cold,
wet nights, a plausible contributor to the bottom readings and noise. At the remount, check
whether the heater runs on fresh batteries (watch the counter). If it doesn't, the heater has
failed.

**Operator actions (not model changes).** Raise the mount ~3–3.25 ft. Prefer aiming the laser at a
floating target (a white float plate in a stilling pipe that reaches from below the summer
low to above 343.6 ft): it removes the bottom readings and damps waves. **After any remount, update
`input_number.crystal_lake_calibration_air_gap` by the exact raise (or recalibrate both helpers
against a staff reading; a float adds its thickness as an offset)**. Otherwise the depth jumps by
the raise and the forecasts and observed alert checks start from a wrong level. A clean
crest-crossing reading would also settle `datum.sensor_to_absolute_offset_ft`.

No model parameter or anchor changed.

### 2026-11-14 — Deep zone gates groundwater recharge; interflow exponent 2 → 3 (fall storms)

**Data.** The forecast atmospheric river came as four modest bursts: ~2.7 in Nov 6–14
(0.93 in Nov 7, 1.2 in Nov 9–10, 0.4 in Nov 13). The lake stayed below the 2-board crest
(339.345) throughout, so the lake level is a closed water balance: the lake rose ~0.21 ft
(daily medians 338.81 on Nov 5 → 339.02 on Nov 14), about the rain on the lake less evaporation
(0.17–0.19 ft). After each burst the level went flat instead of rising for a day, as watershed
inflow would make it do. The gauge reads high *during* rain and settles within a few hours (a
real lake can't fall that fast with no outflow), so storms are measured from settled 24 h
medians: the 24 h before the first rain hour against 6–30 h after the last.

**All eight below-crest storms since Aug 29** (6.9 in) delivered ~**0.09 ft** of watershed water
(rise − rain on the lake + evaporation), about 0.5 % of the rain. Single storms are within the
gauge's ±0.1 ft. The model on its pure replay delivered **1.00 ft**, and 0.51 ft with the
baseflow assimilation. The Nov 5–14 week backtest ended 339.49 against 339.02 measured (+0.45 ft,
about 0.11 ft of it a noisy T0 reading). Its 0.40 ft of watershed water was half interflow off a
soil that was still dry (SM 0.5 → 1.3 in) and half groundwater recharged by in-storm percolation:
the assimilation had zeroed the store, and it refilled anyway. A 72 h backtest from Nov 11, a
window too wet for the assimilation, ended +0.29 ft after 0.4 in of rain.

**Root cause: the model couldn't tell June from November.** Its soil is about as dry in both
(SM 0.65 in on 2026-06-13, ~0.5 in in November), but June followed a wet spring and delivered
3.7 cfs (3.56 of the model's 3.69 is groundwater), while November follows a dry summer and
delivers ~nothing. The single groundwater store recharged identically in both.

**Change (structural, two parts):**
1. **Deep zone** (`deep_zone`, `model.m8_deep_zone`, new state `S_dz`): percolation refills it
   before recharging groundwater, recharge = perc × (S_dz/capacity)^gate_exp. Deep roots draw it
   down only for the ET demand the soil can't meet (et_coeff × unmet × S_dz/capacity), so it
   stays full through a wet spring and empties over a dry summer. Seeded each month at capacity ×
   the soil's seasonal wetness fraction (full Dec–Mar). capacity 4 in, et_coeff 1.0,
   gate_exp 2. A first version drew deep ET from the full PET; it drained the zone in spring
   and cut June to ~3.0 cfs.
2. **Interflow-generation exponent** `hspf.IFEXP` 2 → 3, split from `INFEXP` (which still drives
   percolation): a dry soil sheds (SM/LZSN)³ instead of ². Unchanged at saturation.

| | Before | Deep zone only | Both (adopted) |
|---|---|---|---|
| 8 fall storms, watershed water, pure replay (measured ~0.09 ft) | 1.00 ft | 0.42 ft | **0.24 ft** |
| Nov 5–14 week backtest, end error | +0.45 ft | +0.34 | **+0.19** |
| 72 h from Nov 9 / Nov 11, end error | +0.14 / +0.29 | +0.11 / +0.13 | **+0.02 / +0.05** |
| June 13 06Z delivery (target 3.7; band 2.3–4.6) | 3.69 cfs | 3.39 | **3.52** |
| Step 6 peak (343.1 ± 0.5) | 342.80 | 342.80 | **342.72** |
| Dry equilibrium (339.6–339.8) | 339.667 | 339.661 | **339.661** |

The backtests run the way the live forecast does (archive replay plus assimilation). The
per-storm modeled watershed water is now 0.015–0.086 ft, against measured −0.13 to +0.10.
`PERC_coeff` stays 0.42: under the gate it barely moves June (0.50 → 3.07 cfs in the prototype)
while lowering Step 6 fast (0.60 → 342.45, fail), and June at 3.52 is inside its band.

**Step 6 margin.** The interflow change lowers the saturated peak 0.08 ft, because the soil dips
below LZSN between bursts. That leaves **0.12 ft above the 342.6 floor**. The deep zone doesn't
touch it (January start, zone full).

**Baseflow assimilation is now mostly idle.** With the deep zone the replayed fall baseflow is
below `min_model_baseflow_cfs` (0.1), so it skips every one of the eight storms. Its one
non-trivial scale was 0.2 on the Nov 9 window. Kept as a safety net for now; retire it once a
wet-season recession confirms the deep zone. Its tests run on an artifact without the deep zone
(the synthetic wet September now mostly refills the zone).

**Weakly constrained, PROVISIONAL.** The deep zone has three hand-fitted knobs pinned by two
contrasts (June after a wet spring vs fall after a dry summer): capacity 2–6 in and gate_exp 1–2
fit about as well. IFEXP rests mostly on one storm (Nov 9–10: interflow 0.156 ft at IFEXP 2,
0.048 at 3, against ~0.01 ft measured). Re-check both on the first storm onto a wet basin. The
zone should be filling now (2.3 in of 4 on Nov 14), so recharge, and with it baseflow, comes
back as the wet season goes on. That recession is the real test.

### 2026-11-14 — Gauge calibration from a 24-hour median (HA script)

**Problem.** `sensor.crystal_lake_depth` = (`calibration_air_gap` − air gap) / 12 +
`calibration_depth`. Both helpers were entered by hand on 2026-06-04: a staff reading of 1.36 ft
and an air gap of 13.58 in, almost certainly a single reading. Readings scatter 0.6–1.4 in within
an hour, and the gauge has a 0.05–0.11 ft daily cycle, so that one value can carry ~0.1 ft of
error into every depth since. The EAP pool levels and our datum (`sensor_to_absolute_offset_ft`
338.375) are in this depth frame, so the fix keeps the frame and makes it match the staff gauge
better.

**Change (Home Assistant).**
- `sensor.crystal_lake_air_gap_24h_median`: a statistics helper, the median of
  `sensor.air_gap_filtered` (bottom reads already excluded) over 24 h, up to 3000 samples. A full
  day averages out the daily cycle, and the median shrugs off the minority of stray reads.
- `script.crystal_lake_calibrate_gauge`: run it with the staff reading. It sets
  `calibration_depth` to the reading and `calibration_air_gap` to the 24 h median, and logs old and
  new values to the logbook. It refuses if the Ecowitt event-rain counter is above 0 (rain within
  ~24 h: the gauge reads high in rain and the lake is moving) or if the median covers < 90 % of the
  day.

**Re-run against the June 4 reading (HA hourly statistics of `air_gap_filtered`).** The 13.58 in
entered at 17:33 PDT sits inside that hour's spread (mean 13.37, 12.09–13.86 in). The 24 h median
of hourly means is 13.95 in trailing (23 h of data), 13.85 centred and 13.69 following, so the
June calibration would move **+0.01 to +0.03 ft**. That is negligible next to the datum's ~0.12 ft
disagreement. But that day's cycle was large: afternoons ~13.1–13.4 in, overnight ~15.0 in, about
0.15 ft. The lake can't move that much in a day without rain, so it is the sensor. The staff
reading was taken in the afternoon low, and a 24 h median assumes the truth sits mid-cycle, so
which phase is true is a ±~0.07 ft uncertainty this method can't remove. Taking staff readings at
two times of day (afternoon and early morning) would tell.

**When calibrating.** Pick a dry, calm day, and check the gauge's 24 h history has no re-lock step
(a jump of 1–2 in after an unknown spell, 2026-11-08 entry), since the median can't remove that.
Then record it here: the date, the staff reading, the median, and the resulting shift in depth.
Every archived level after that time moves by the shift, so backtests that span it need the
difference.

**At the remount, as well as the raise.** `sensor.air_gap_filtered` only accepts 5–30 in. With
the face ~3 ft higher the normal gap is ~55–65 in, and bottom reads ~80+ in. Widen the accepted
band first (about 10–70 in), or every reading will be thrown out. Then set `calibration_air_gap`
roughly (old value + the raise) to keep depths sensible, and run the script after 24 h of dry
weather on the new mount.

### 2026-11-14 — Rain forecasts vs the gauge (Nov 5–14); NBM and ECMWF ensembles archived

**No model parameter changed.** Scored the 229 hourly forecast-log runs from Nov 5–14
(four storms, ~2.7 in) against the archived gauge rain:

| Lead | Apple, forecast/measured | NWS, forecast/measured |
|---|---|---|
| next 6 h | 0.74 | 0.60 |
| next 24 h | 0.77 | 0.53 |
| 24–48 h | 0.78 | 0.45 |
| 48–72 h | 0.62 | 0.53 |

- **Apple is the better source** (24 h MAE 0.14 vs 0.19 in) and calls wet and dry days well (90
  of 91 wet 24 h windows, 2 false alarms). Both forecasts run low on the bursts; Nov 7's 0.93 in
  (1.35 in/hr peak) was forecast 0.50 by Apple and 0.33 by NWS the day before.
- **Longer windows help up to ~3 days, not after.** Apple's MAE relative to the mean rain falls
  from 0.59 (6 h) to 0.31 (72 h) as timing errors cancel, but the bias stays ~0.75 at every
  length, and beyond 3 days the totals stop tracking the weather (correlation 0.27 at 5 days).
- **The model's rain band is too narrow on the high side.** Its median scenario is Apple's
  forecast, so it inherits the shortfall. Measured rain exceeded the "high" scenario in 52 of 92
  wet 24 h windows and 41 of 156 wet 72 h windows, and was never below "low". The high scenario
  drives the earliest-crossing times and the alert probabilities, so in a big storm this would
  make warnings late. Not changed yet: ten days and four storms are too few to fit it.

**The gauge itself (Ecowitt piezo).** The finding above assumes the gauge is right. Against
the four nearest airports (Iowa Environmental Mesonet ASOS hourly, Nov 5–13) the gauge read 2.67
in vs 1.56–2.11 in. That isn't a correction factor: Paine Field (the nearest, 21 km) matched its
Nov 7 storm (0.77 vs 0.93 in), other days go both ways, and the site is inland of the lowland
airports, where more rain is normal. The lake is the better check. With the basin below the
outlets and delivering ~nothing (2026-11-14 deep-zone entry), the settled rise plus evaporation
over the eight fall storms is 1.15× the gauge's rain on the lake (0.93–1.13 on the cleanest three),
so the gauge is within about −20 % to +15 % and probably not reading high. A side-by-side
reference (a manual 4-in gauge or an Ecowitt WH40 tipping bucket) is what would calibrate it.
The fix would then go in the Ecowitt app's per-rain-rate piezo gain, so every consumer gets it.

**Ensemble forecasts archived for calibration** (`qpf_archive`, `sources/nbm.py`,
`sources/ecmwf_ens.py`, `qpf_verify`; `lake-rise qpf fetch|score`; hourly NAS job). The archive
holds the 00Z/12Z runs back to the gauge record's 2026-02-21 start, one small JSON per run. The
history is pulled once, on the Mac, and copied to the NAS before the job is deployed, so nothing is
downloaded twice: updates skip stored runs, and a per-source lock keeps a manual fetch and the
hourly job from overlapping.
- **NBM** (QMD GRIB2 on AWS): the 5–95th percentiles for days 1–3 and the 3-day total at the
  nearest 2.5 km grid point.
- **ECMWF** (IFS ENS 0.25°, Google Cloud mirror; AWS throttles a single client hard): all 50
  members' daily and 3-day totals, bilinear at the site.

Only the rain messages are downloaded (byte ranges from each file's index, ~40–60 MB a run) and
decoded with ecCodes via `pygrib==2.1.8`. Two things to know:
- NBM's percentiles for different accumulation periods are not consistent with each other: on
  2026-11-10 12Z its day-1 median (1.28 in) exceeded its own 3-day median (0.78 in). They are
  stored as published; the scoring will show which periods to trust.
- Scoring uses each source's 00Z/12Z runs, with Apple and NWS taken from the log line at the
  same hour, so `--common` compares the sources on the same weather.

**First scores (2026-02-21 to 11-14, 531 runs each; `lake-rise qpf score`).** Day 1 / day 2 /
day 3, "wet" meaning a period with at least 0.1 in measured:

| | median ÷ measured, all | median ÷ measured, wet | wet periods above the 90th percentile |
|---|---|---|---|
| NBM | 0.93 / 0.82 / 0.78 | 0.85 / 0.72 / 0.65 | 21 / 22 / 25 % |
| ECMWF | 0.97 / 0.88 / 0.81 | 0.82 / 0.74 / 0.66 | 37 / 41 / 38 % |

Both read low, increasingly with lead, and are too narrow on wet days: a well-spread ensemble
puts about 10 % of outcomes above its 90th percentile. Some of the wet-only shortfall comes from
picking periods by what was measured, so the all-period ratios are the honest bias. NBM's spread
is the better calibrated of the two; ECMWF's median is the less biased. Over the 13–17 recent runs
every source has (the forecast log only began recording Apple and NWS rain recently), Apple's 3-day
total reads 0.68× measured and NWS's 0.50×, against 0.70× for NBM and 0.82× for ECMWF. So the
ensembles are at least as good as Apple, and NWS is the worst of the four.

**Next:** done the same day: the calibrated ECMWF ensemble now drives the live forecast (next
entry).

### 2026-11-14 — The calibrated ECMWF ensemble drives the live forecast
**What changed.** The live forecast used to take Apple's hourly point forecast as the median and
make low/high by fixed multipliers by lead day (`uncertainty.lead_ratio_by_day`, a literature
placeholder). Now it takes the newest archived ECMWF run (`qpf_archive.latest`, kept current by
the hourly job) and runs the model on all 50 members, each corrected for bias and spread
(`ensemble_rain`, `scenarios.ensemble_members`):
- **low / median / high** are the members at the 10th/50th/90th percentile of lake peak. Each is
  a real member's coherent 3-day rain, not per-hour multiples of one forecast. That removes the
  old band's known over-spread (every hour pushed to its 90th percentile at once).
- **Crossing probabilities** come from the members' peaks: a CDF through their 5th–95th
  percentiles, with the existing exponential tails beyond (`predict._ENSEMBLE_QUANTILES`).
- **Apple now only times the rain within each day.** A run's days count from its cycle, so at
  `as_of` the first is partly past and gets the share of its total still ahead. Each member keeps
  Apple's hourly rain, scaled down if the member is drier. Any rain beyond Apple's is spread
  evenly over the day's hours. A wet member's day therefore doesn't pile into the hour or two
  Apple has a shower: putting all of it into Apple's shape made 1.7 in/hr bursts out of the
  2026-04-12 run.
- **Fallback:** without a run at most `max_run_age_h` = 30 h old (normally 9–21 h), the old band
  stands in. The page simulator still uses the band. `rain_source` and `ensemble_cycle` are in
  the result's `input_summary` and the forecast log.

**What it shows.** On the dashboard's live tab the lines are the **wet / middle / dry case**
(formerly high / median / low), drawn in the forecast's blue with different dashes. Shading
around them shows where the lake sits each hour across all 50 members: nested bands holding 9 in
10, 8 in 10 and half of them, darkest where most agree (`elevation_bands_ft`). The confidence badge
and the alert emails' "Accuracy" line now give **forecast agreement**: the share of members whose
lake peak is within `ensemble_rain.agreement_tolerance_ft` = 0.1 ft of the middle case's. 0.1 ft is
about the gauge's own daily wobble. It is labelled High ≥ 70 %, Medium ≥ 45 %, otherwise Low,
replacing the lead-time skill table (still used without an ensemble). It is display only and
changes no prediction.

**The correction** (per lead day, square-root space, z = √member):
`z' = mean_scale·z̄ + spread_scale·(z − z̄) + added_spread·z̄·u`, where u is the member's rank
as a standard-normal quantile. It is fitted by quantile score (pinball loss over the
5–95th percentiles) against the gauge's day totals, 531 runs from 2026-02-21 to 11-13.

Out of sample (each month scored with parameters fitted to the others), on forecast-wet periods
(raw median ≥ 0.1 in; conditioning on the forecast, not the outcome, so no selection bias):

| | quantile score, in (lower is better) | median ÷ measured | above the 90th pct (ideal 10 %) |
|---|---|---|---|
| Day 1: raw ECMWF / corrected / raw NBM | 0.129 / **0.105** / 0.116 | 0.96 / 0.86 / 0.98 | 26 / **11** / 15 % |
| Day 2 | 0.124 / **0.107** / 0.121 | 0.89 / 0.87 / 0.87 | 26 / **9** / 12 % |
| Day 3 | 0.145 / **0.129** / 0.145 | 0.83 / 0.76 / 0.84 | 25 / **12** / 13 % |
| 3-day total (sum of corrected days) | 0.175 / **0.156** / 0.193 | 0.92 / 0.90 / 0.77 | 25 / **13** / 25 % |

The corrected median reads lower than the raw one because the correction widens a right-skewed
distribution: the median falls further below the mean while the upper tail grows. That upper
tail is what the warnings use.

Summing the separately corrected days gives as good a 3-day total as correcting the total
directly (0.156 vs 0.156), so member trajectories stay coherent.

Fitted values (`ensemble_rain.calibration_by_day`; refits from four starting points agree):

| Day | mean_scale | spread_scale | added_spread |
|---|---|---|---|
| 1 | 0.945 | 0.889 | 0.218 |
| 2 | 0.990 | 1.072 | 0.182 |
| 3 | 0.972 | 0.585 | 0.335 |

**Big storms.** Of the 15 wettest measured 3-day periods, the raw ensemble had all 50 members
below the measured total in 11. Corrected, 13 of the 15 fall inside the ensemble, mostly in its
top 10–20 %. Heavy storms are still under-forecast at the median (about 0.5–0.6× measured). A
fourth, storm-size term (mean_scale·z̄^p) did not help out of sample: it was better on day 2 and
worse on days 1 and 3, so it was not adopted.

**Day 4.** A run is 9–21 h old when used, so three days fall up to 21 h short of the 72 h
horizon. `ecmwf_ens.STEPS` now includes 96 h from this deploy on. Day 4 reuses day 3's
correction until enough runs exist to fit it. Runs archived earlier have three days, and hours
past them take Apple's forecast for every member.

**Caveats.**
- The fit is against our gauge, which may read up to ~20 % low or ~15 % high in rain (Nov 5–14
  entry). The correction carries that bias, which is consistent with the gauge-driven rest of
  the model.
- The record is nine months with one spring wet season. Refit as the archive grows, the same
  way: quantile score, leave one month out, Nelder-Mead from a few starting points. The fit used
  numpy and scipy outside the project, which depends on neither.
- NBM stays archived and scored but isn't used live: corrected ECMWF beat it on every period.

**Live check (2026-11-15 04Z, the 11-14 12Z run, quiet weather):**
- 72 h rain: members 0.30 / 0.65 / 1.23 in (10th/50th/90th), against the old band's
  0.23 / 0.78 / 1.29 in.
- Peaks: 339.11 / 339.17 / 339.28 ft, against 339.11 / 339.20 / 339.32 ft.
- No change to the anchors: Step 6 342.72 ft, dry equilibrium 339.661 ft (they drive the model
  with fixed rain).

### 2026-11-14 — With an ensemble, the chances are member counts (no parameter changed)
**What changed.** With an ensemble, the chance of reaching a level is now the share of the 50
members whose 3-day lake peak reaches it (`predict`, `ensemble_peaks_ft`): 11 of 50 above the
crest reads 22 %. It replaces the CDF through the members' 5th–95th percentile peaks with
exponential tails (previous entry). It applies to every consumer of `predict`: alert ladder,
dashboard and forecast log. "Already over it = 100 %" stays. The fallback band (no run within
`max_run_age_h`) keeps the smoothed CDF through its low/median/high peaks, unchanged.

**Why.** The field view draws the 50 peaks as dots, and its percentages must match the alert
emails'. With the smoothed CDF they could disagree (the design's example: 6 of 50 peaks over
the bridge deck is 12 %, but the CDF says 15 % from a tail beyond the highest dot). Counting makes the page and the
emails the same number by construction. 50 members give 2 % steps, fine for the ladder's 10–60 %
cutoffs. The ensemble is calibrated for spread (previous entry), so its members already carry
the upper tail the CDF used to extrapolate.

**Trade-off (accepted).** A level above every member's peak reads 0 %, not a small tail chance.
The smoothed tail was deliberately fat, to surface under-forecast storms (2026-06-16 entry). With
the calibrated ensemble, 13 of the 15 wettest measured 3-day periods fell inside the members,
so a storm beyond all 50 is rare. It is still possible, and the alerts will no longer hint at it.
An alert-level replay over the archived runs, old method against new, shows how often the
level changes; its result is the next entry.

**Shown stick values.** The field view shows the overtopping levels on the stick as crest
342.2 − 338.375 = 3.825 → **3.83** and bridge deck 342.7 − 338.375 = 4.325 → **4.33**, rounded
half-up. The design's "3.82 / 4.32" were truncations. Counting uses the unrounded absolute
thresholds, so the page and the emails agree at the boundary.

**Anchors.** Unchanged (they drive the model with fixed rain): Step 6 342.72 ft, dry
equilibrium 339.661 ft.

### 2026-11-14 — Alert-level replay: counted vs smoothed chances (no parameter changed)
**What.** `lake-rise alerts-replay` reruns every archived 12Z ECMWF run since 2026-02-21 as the
live forecast would have at cycle + 12 h: the lake at the archive hour, the seasonal stop-log
count, the archive spin-up and the 50 calibrated members. It compares the default alert ladder
from the old smoothed CDF through the member peaks' 5th–95th percentiles with the ladder from
member counts (previous entry). An hour with no lake reading (the gauge archive has none before
June 29) assumes the lake at the spill crest for that date's default stop logs (339.68 ft with 3
boards, 339.35 ft with 2), a typical wet-season level, so the spring runs are covered. Data: the
NAS archive and QPF copies of 2026-11-14.

**Result.** `256 runs (120 with lake assumed at control), 0 differ, 0 lower`. No run's level
dropped, and none rose.

**Coverage.** 267 12Z runs are archived. 10 (Feb 21 – Mar 2) were skipped because the archive
spin-up returned nothing, and the 11-14 run because its hour hasn't happened yet. The 120 runs
with the lake assumed span Mar 3 – Nov 8, almost all before June 29. Every replayed run is
NORMAL under both methods. The highest member peak in any run was 340.86 ft (03-18, lake
assumed at 339.68), 0.14 ft under early warning (341.0); with measured lake readings it was
339.81 ft (06-29). Counts give 0 % at every threshold in every run, and the smoothed CDF at
most 0.3 % (early warning, 03-16), far below the ladder's lowest cutoff (ADVISORY, 30 %).

So the switch changed no past level. But no archived run comes near a threshold, so the replay
doesn't exercise the cases where the two methods can disagree (one member far above the rest:
the `test_they_can_differ_when_one_member_is_far_above_the_rest` case, NORMAL → WATCH). Re-run
it after the first wet-season storm.

### 2026-11-15 — Lake filter tuned on real minute noise and synthetic storms

*Superseded by the next entry (the re-tune after the whole-change review).*

**What changed.** `lake_filter.wet_window_hours` 6 → **3**. `rate_sd_wet_ft_per_min` (1e-4) and
`offset_minutes` (20) were gridded and stay at the prototype's values. The other `lake_filter` values
are the prototype's and weren't gridded. Set by `lake-rise lake-filter-study --write`
(docs/plans/lake-filter.md, T3; design D47). Nothing calls the filter until the live wiring (T4).

**Data.** The minute archive, 2026-11-06 04:43 to 11-16 04:42 UTC (10 days: `sensor.crystal_lake_depth`,
`sensor.crystal_lake_depth_smoothed`, the daily rain accumulator, stop logs at 2 throughout). 3.95 in
of rain fell in 64 wet hours. The model's inflow is the pure archive replay (NAS copy of 2026-11-16)
to 11-16 04Z, sliced by hour. Every estimate is a stateless 12 h call, as live, with the rain a live
call would see (completed hours spread evenly, the current hour's accumulator rise so far). Runtime
450 s for the 36 combinations.

**Method.**
- **Real noise.** One call every hour. Jitter is the sd of the hour-to-hour change. **One split for
  every combination:** a pair is dry when no rain fell (and none is unknown) in the 12 h before the
  later call, the grid's widest window; otherwise it is wet. "Truth" is the centred 4 h median of the
  raw depth: not independent of the gauge. p99 is the 99th-percentile |estimate − truth|. Today's
  anchor is the 30-min median of the smoothed sensor at the same minutes.
  - **Common calls:** both estimators are scored on the calls where each has a value. The filter
    gives no estimate (P9: under 20 valid raw minutes in the last 30) in 57 of the 228 hourly calls,
    the raw depth's `unknown` spells. At those calls the smoothed sensor holds a stale value, so
    today's anchor scores much worse there: 0.037 / 0.062 / 0.119 ft over all calls, against
    0.027 / 0.047 / 0.071 on the common 171. That leaves 76 dry and 95 wet pairs.
- **Synthetic storms.** Ramps of 0.05 and 0.1 ft/h for 6 h are added to the raw and the smoothed
  readings, on top of the measured rain. There are three shapes:
  - **abrupt:** full rate from the onset; rain of 0.25 in/h from 2 h before it until the ramp ends.
    Checked at 30, 60 and 120 min after the onset.
  - **gradual:** reaching full rate linearly over 2 h, with the same rain and checks.
  - **outlasts** (the rise outlasts the rain): the abrupt ramp for its full 6 h, but the rain
    stops 2 h after the onset. Checked at 30, 60 and 120 min after the onset, and at 60, 120 and
    240 min after the rain stops (180, 240 and 360 min after the onset).

  The model cases are right (the ramp's inflow, T1's recipe: the rise less the storm's extra rain
  on the lake while it rains, clipped at 0, added to the replay), half, and blind (the replay only).
  Means are taken over the ramps where both estimators have a value. A case fails if the filter has
  no value on more than a third of its ramps. The truth must be defined at every check, up to 6 h
  after the onset, so 12 of the 15 onsets are used. The filter has no estimate on 1–2 of them at
  some checks, so each check averages 10–12 ramps.
- **Two measurement rulings (controller, issue #68, after the first run).** The first run used
  one onset every 24 h and gated on absolute "behind" (truth − estimate). No combination passed
  (best margin −0.003 ft). The failures were the 120-min "no more than today" check and the
  right/half 30-min checks, by amounts the size of what each estimator does at that time of day
  with no storm at all. Rain only, no ramp, at 08:43 PST: filter 0.016 / 0.005 / 0.000 ft behind
  at 30 / 60 / 120 min, today 0.019 / 0.014 / −0.003. That is the daily cycle: the centred truth
  leads both trailing estimates. Shifting every onset by 6, 12 or 18 h flipped pass and fail for
  the 1e-4 and 2e-4 rows. P12 itself is unchanged. The rulings changed how it is measured:
  1. onsets every **15 h**, so the ramps rotate through 8 times of day;
  2. the gating quantity is each estimator's **storm-induced lag**: behind on the ramped series
     minus behind on the same series without the ramp, at the same minutes, the storm's rain
     kept in both so the wetness is identical. The absolute "behind" is still reported (below),
     so a time-of-day weakness stays visible.

  A third ruling added the **outlasts** shape. The re-pick to a 3 h wet window exposed a gap: every
  other synthetic storm rains for the whole ramp, but a real lake keeps rising for hours after the
  rain stops (~4.6 h basin lag). A filter that stiffens once its wet window empties would lag that
  rise, and nothing tested it.
- **The bar (P12)**, on the mean lag. Blind: at most today's + 0.03 ft at the in-rain checks 30 and
  60 min after the onset, and no more than today's at every other check, including every check after
  the rain stops. Right and half: no more than today's at every check. The choice is
  the passing combination with the lowest dry jitter (to 0.001 ft), ties to the lower wet jitter.

**Grid** (ft). Today's anchor on the same calls: dry jitter **0.027**, wet 0.047, p99 0.071. Margin
is the smallest P12 slack. Skipped is the number of ramp checks (of 864) without a filter estimate.

| rate_sd_wet | window h | offset min | filter dry | filter wet | filter p99 | margin | P12 | skipped |
|---|---|---|---|---|---|---|---|---|
| 3e-05 | 3 | 15 | 0.010 | 0.028 | 0.066 | -0.020 | fail | 48 |
| 3e-05 | 3 | 20 | 0.010 | 0.028 | 0.066 | -0.023 | fail | 48 |
| 3e-05 | 3 | 30 | 0.011 | 0.027 | 0.067 | -0.025 | fail | 48 |
| 3e-05 | 6 | 15 | 0.010 | 0.031 | 0.061 | -0.020 | fail | 48 |
| 3e-05 | 6 | 20 | 0.010 | 0.030 | 0.060 | -0.023 | fail | 48 |
| 3e-05 | 6 | 30 | 0.011 | 0.030 | 0.060 | -0.025 | fail | 48 |
| 3e-05 | 12 | 15 | 0.010 | 0.032 | 0.061 | -0.020 | fail | 48 |
| 3e-05 | 12 | 20 | 0.010 | 0.031 | 0.060 | -0.023 | fail | 48 |
| 3e-05 | 12 | 30 | 0.011 | 0.030 | 0.061 | -0.025 | fail | 48 |
| 5e-05 | 3 | 15 | 0.010 | 0.031 | 0.062 | -0.005 | fail | 48 |
| 5e-05 | 3 | 20 | 0.010 | 0.030 | 0.062 | -0.005 | fail | 48 |
| 5e-05 | 3 | 30 | 0.011 | 0.030 | 0.062 | -0.004 | fail | 48 |
| 5e-05 | 6 | 15 | 0.010 | 0.034 | 0.071 | -0.005 | fail | 48 |
| 5e-05 | 6 | 20 | 0.010 | 0.034 | 0.069 | -0.005 | fail | 48 |
| 5e-05 | 6 | 30 | 0.011 | 0.033 | 0.068 | -0.004 | fail | 48 |
| 5e-05 | 12 | 15 | 0.010 | 0.035 | 0.068 | -0.005 | fail | 48 |
| 5e-05 | 12 | 20 | 0.010 | 0.034 | 0.067 | -0.005 | fail | 48 |
| 5e-05 | 12 | 30 | 0.011 | 0.034 | 0.067 | -0.004 | fail | 48 |
| 0.0001 | 3 | 15 | 0.010 | 0.036 | 0.071 | 0.000 | pass | 48 |
| 0.0001 | 3 | 20 | 0.010 | 0.035 | 0.070 | 0.001 | pass | 48 |
| 0.0001 | 3 | 30 | 0.011 | 0.035 | 0.069 | 0.001 | pass | 48 |
| 0.0001 | 6 | 15 | 0.010 | 0.040 | 0.070 | 0.000 | pass | 48 |
| 0.0001 | 6 | 20 | 0.010 | 0.039 | 0.068 | 0.001 | pass | 48 |
| 0.0001 | 6 | 30 | 0.011 | 0.038 | 0.068 | 0.001 | pass | 48 |
| 0.0001 | 12 | 15 | 0.010 | 0.041 | 0.075 | 0.000 | pass | 48 |
| 0.0001 | 12 | 20 | 0.010 | 0.040 | 0.074 | 0.001 | pass | 48 |
| 0.0001 | 12 | 30 | 0.011 | 0.039 | 0.074 | 0.001 | pass | 48 |
| 0.0002 | 3 | 15 | 0.010 | 0.040 | 0.075 | 0.001 | pass | 48 |
| 0.0002 | 3 | 20 | 0.010 | 0.040 | 0.074 | 0.001 | pass | 48 |
| 0.0002 | 3 | 30 | 0.011 | 0.039 | 0.075 | 0.001 | pass | 48 |
| 0.0002 | 6 | 15 | 0.010 | 0.044 | 0.076 | 0.001 | pass | 48 |
| 0.0002 | 6 | 20 | 0.010 | 0.044 | 0.075 | 0.001 | pass | 48 |
| 0.0002 | 6 | 30 | 0.011 | 0.043 | 0.075 | 0.001 | pass | 48 |
| 0.0002 | 12 | 15 | 0.010 | 0.045 | 0.084 | 0.001 | pass | 48 |
| 0.0002 | 12 | 20 | 0.010 | 0.045 | 0.082 | 0.001 | pass | 48 |
| 0.0002 | 12 | 30 | 0.011 | 0.044 | 0.081 | 0.001 | pass | 48 |

Every combination roughly halves the jitter of today's anchor: dry 0.010–0.011 ft against 0.027,
wet 0.027–0.045 against 0.047. A `rate_sd_wet` of 3e-5 or 5e-5 lags a storm too much. The P12
result hardly depends on the window or the offset.

**Chosen: `rate_sd_wet_ft_per_min` 1e-4, `wet_window_hours` 3, `offset_minutes` 20.**
- Dry jitter is **0.010 ft against today's 0.027**, wet jitter 0.035 against 0.047, and p99 error
  0.070 against 0.071.
- Every passing row ties on dry jitter at 0.010 ft (offsets 15 and 20). Among those, the 3 h window
  with a 20 min offset has the lowest wet jitter: a shorter window drops back to the dry mode sooner
  after the rain stops.
- The P12 margin is +0.0006 ft, binding at 60 min on the gradual 0.05 ft/h ramp (half), where both
  lags are 0.004–0.005 ft. Each of the 18 rows at 1e-4 or 2e-4 passes with about the same margin.
- On the **outlasts** shape the margin is +0.004 ft overall and +0.012 ft after the rain stops. After
  the rain the filter is at or ahead of the truth (lag −0.004 to +0.001 ft), and today's anchor
  lags by 0.013–0.027. The filter keeps the rate it learned in the rain (the residual rate
  `c` holds) as it stiffens, so it doesn't fall behind a rise that continues.
- The p99 error is no better than today's on the common calls. Its tail is the daily cycle and the
  high readings in rain, which the filter follows, as the median does.
- An earlier pass of this study (same day, before review) chose a 12 h window. It split dry from wet
  by each combination's own window, which gave the 12 h windows fewer, drier pairs, and it scored
  the two estimators on different calls.

Storm-induced lag (gating), mean over the ramps, ft, filter / today. Rain through the ramp:

| ft/h | onset | model | 30 min | 60 min | 120 min |
|---|---|---|---|---|---|
| 0.05 | abrupt | right | 0.004 / 0.014 | 0.004 / 0.011 | 0.002 / 0.011 |
| 0.05 | abrupt | half | 0.007 / 0.014 | 0.007 / 0.011 | 0.003 / 0.011 |
| 0.05 | abrupt | blind | 0.010 / 0.014 | 0.010 / 0.011 | 0.004 / 0.011 |
| 0.05 | gradual | right | 0.002 / 0.002 | 0.004 / 0.005 | 0.003 / 0.010 |
| 0.05 | gradual | half | 0.002 / 0.002 | 0.004 / 0.005 | 0.005 / 0.010 |
| 0.05 | gradual | blind | 0.002 / 0.002 | 0.004 / 0.005 | 0.008 / 0.010 |
| 0.1 | abrupt | right | 0.004 / 0.025 | 0.003 / 0.023 | 0.001 / 0.024 |
| 0.1 | abrupt | half | 0.011 / 0.025 | 0.011 / 0.023 | 0.005 / 0.024 |
| 0.1 | abrupt | blind | 0.019 / 0.025 | 0.020 / 0.023 | 0.009 / 0.024 |
| 0.1 | gradual | right | 0.003 / 0.005 | 0.004 / 0.009 | 0.001 / 0.022 |
| 0.1 | gradual | half | 0.003 / 0.005 | 0.006 / 0.009 | 0.008 / 0.022 |
| 0.1 | gradual | blind | 0.003 / 0.005 | 0.008 / 0.009 | 0.015 / 0.022 |

The rise outlasts the rain (rain stops 120 min after the onset):

| ft/h | model | 30 | 60 | 120 | 180 | 240 | 360 min |
|---|---|---|---|---|---|---|---|
| 0.05 | right | 0.004 / 0.014 | 0.004 / 0.011 | 0.001 / 0.011 | -0.004 / 0.013 | -0.002 / 0.013 | 0.000 / 0.013 |
| 0.05 | half | 0.007 / 0.014 | 0.007 / 0.011 | 0.003 / 0.011 | -0.002 / 0.013 | -0.001 / 0.013 | 0.000 / 0.013 |
| 0.05 | blind | 0.010 / 0.014 | 0.010 / 0.011 | 0.004 / 0.011 | 0.001 / 0.013 | -0.000 / 0.013 | -0.000 / 0.013 |
| 0.1 | right | 0.004 / 0.025 | 0.003 / 0.023 | 0.001 / 0.024 | -0.004 / 0.025 | -0.002 / 0.025 | 0.000 / 0.027 |
| 0.1 | half | 0.011 / 0.025 | 0.011 / 0.023 | 0.004 / 0.024 | -0.002 / 0.025 | -0.001 / 0.025 | 0.000 / 0.027 |
| 0.1 | blind | 0.019 / 0.025 | 0.020 / 0.023 | 0.009 / 0.024 | 0.001 / 0.025 | -0.001 / 0.025 | 0.000 / 0.027 |

Absolute behind (not gating), the same calls. Rain through the ramp:

| ft/h | onset | model | 30 min | 60 min | 120 min |
|---|---|---|---|---|---|
| 0.05 | abrupt | right | 0.015 / 0.022 | 0.012 / 0.017 | 0.018 / 0.030 |
| 0.05 | abrupt | half | 0.018 / 0.022 | 0.015 / 0.017 | 0.020 / 0.030 |
| 0.05 | abrupt | blind | 0.021 / 0.022 | 0.018 / 0.017 | 0.021 / 0.030 |
| 0.05 | gradual | right | 0.013 / 0.010 | 0.012 / 0.011 | 0.020 / 0.028 |
| 0.05 | gradual | half | 0.013 / 0.010 | 0.012 / 0.011 | 0.022 / 0.028 |
| 0.05 | gradual | blind | 0.013 / 0.010 | 0.012 / 0.011 | 0.024 / 0.028 |
| 0.1 | abrupt | right | 0.015 / 0.032 | 0.011 / 0.030 | 0.018 / 0.042 |
| 0.1 | abrupt | half | 0.023 / 0.032 | 0.019 / 0.030 | 0.021 / 0.042 |
| 0.1 | abrupt | blind | 0.031 / 0.032 | 0.028 / 0.030 | 0.025 / 0.042 |
| 0.1 | gradual | right | 0.014 / 0.012 | 0.012 / 0.016 | 0.018 / 0.041 |
| 0.1 | gradual | half | 0.014 / 0.012 | 0.014 / 0.016 | 0.025 / 0.041 |
| 0.1 | gradual | blind | 0.015 / 0.012 | 0.016 / 0.016 | 0.032 / 0.041 |

The rise outlasts the rain:

| ft/h | model | 30 | 60 | 120 | 180 | 240 | 360 min |
|---|---|---|---|---|---|---|---|
| 0.05 | right | 0.015 / 0.022 | 0.012 / 0.017 | 0.018 / 0.030 | -0.009 / -0.002 | -0.008 / 0.012 | -0.008 / 0.005 |
| 0.05 | half | 0.018 / 0.022 | 0.015 / 0.017 | 0.020 / 0.030 | -0.007 / -0.002 | -0.007 / 0.012 | -0.008 / 0.005 |
| 0.05 | blind | 0.021 / 0.022 | 0.018 / 0.017 | 0.021 / 0.030 | -0.004 / -0.002 | -0.006 / 0.012 | -0.008 / 0.005 |
| 0.1 | right | 0.015 / 0.032 | 0.011 / 0.030 | 0.018 / 0.042 | -0.009 / 0.011 | -0.008 / 0.024 | -0.008 / 0.019 |
| 0.1 | half | 0.023 / 0.032 | 0.019 / 0.030 | 0.022 / 0.042 | -0.006 / 0.011 | -0.007 / 0.024 | -0.008 / 0.019 |
| 0.1 | blind | 0.031 / 0.032 | 0.028 / 0.030 | 0.026 / 0.042 | -0.004 / 0.011 | -0.006 / 0.024 | -0.008 / 0.019 |

On absolute behind, the filter is never further behind than today's at 120 min or after the rain.
At 30 and 60 min on the gradual ramps, and at 60 min on the 0.05 ft/h abrupt and outlasts ramps, it trails by up
to 0.003 ft: the time-of-day effect above, not storm lag.

**Real storms.** Every rise in the hourly archive of ≥ 0.10 ft between the 24 h medians either
side of an hour, with ≥ 0.5 in of rain in the 24 h around it. **4 found.** The plan expected zero;
that was wrong. Two have minute readings:

| Hour (UTC) | Settled rise | Rain | Mean behind over the next 24 h, filter / today |
|---|---|---|---|
| 2026-09-04 15Z | +0.11 ft | 0.92 in | no minute readings |
| 2026-09-05 05Z | +0.10 ft | 0.92 in | no minute readings (probably the same storm, split by the hour-merging rule) |
| 2026-11-08 02Z | +0.19 ft | 0.97 in | 0.001 / −0.022 ft |
| 2026-11-10 02Z | +0.13 ft | 0.50 in | 0.002 / 0.003 ft |

Today's −0.022 on Nov 8 is the gauge reading high in rain (2026-11-14 entry). None of these is
big enough to tune on.

**Known limits.**
- The truth is a median of the same gauge.
- The gauge's 0.05–0.11 ft daily cycle (2026-11-05) goes into the filter's level and rate.
- The gauge reads high during rain (2026-11-14). With `wet = 1` the filter trusts it most, so it
  biases the anchor high in storms: the safe side for alerts.
- Adding the ramp straight to the smoothed sensor ignores that sensor's own lag, which favours
  today's anchor.
- The storms are synthetic. The archive's largest real one is a 0.19 ft settled rise in a day.
- The P12 margin is thin, and 12 ramps over 10 days is a small sample.
- The study doesn't apply P7's stop-log cut. The stop logs didn't change in this window.

Re-run the study (`lake-rise lake-filter-study --minute-dir … --archive-dir … --write`) after the
first wet-season storm (its minute readings are kept in `data/gauge_minute/`), after the laser
remount, and on any change to the replay's inflows or the spillway.

**Anchors.** Unchanged (the filter is not in the model): Step 6 342.72 ft, dry equilibrium
339.661 ft.

### 2026-11-15 — Lake filter re-tuned after the whole-change review (#74)

*Superseded by the next entry: the filter now runs in dry weather only.*

**What changed.** `lake_filter.rate_sd_wet_ft_per_min` 1e-4 → **3e-4**, `offset_minutes` 20 → **30**,
`wet_full_in` 0.2 → **0.05**; `wet_window_hours` stays **3**. Set by `lake-rise lake-filter-study --write`
on the same minute archive and NAS replay as the previous entry. The study now measures what a live call
sees, and has a storm shape it lacked:
- **A rise that starts with the rain** (codex's critical finding). After a dry spell the filter needs
  `wet_full_in` of rain in its window before it loosens: at 0.25 in/h and 0.2 in that took ~48 min, so
  a blind 0.1 ft/h rise beginning with the rain lagged today's anchor by 0.044 ft more than P12 allows
  at 60 min. New gated shape **rain_starts**: ≥ 12 h with no measured rain, then the abrupt ramp with
  0.25 in/h from the onset through the 6 h ramp; all three model cases, checked at 30, 60 and 120 min.
  Its onsets (ruling on #74) are every eligible point on a 3 h grid, each its own ramped series with its
  no-ramp twin (so they may overlap); the other shapes keep 15 h onsets. Onsets: abrupt, gradual and
  outlasts 12 each, rain_starts **27**; 10–26 ramps with both estimates per check.
- **The replay inflow as live sees it.** The study handed each call the completed current hour's replay
  inflow; a live call holds the previous hour's over the current one (the replay ends at the top of the
  hour). Both now use `lake_filter.inflow_by_minute`. The synthetic ramp's own inflow (the right and half
  cases) stays minute-resolved, an idealisation of a model that knows the storm.
- **Grid** adds `wet_full_in` {0.02, 0.05, 0.1, 0.2} and, after a first extended run found nothing
  passing (best −0.0011 ft on rain_starts 0.05 ft/h half at 30 min, ruling on #74), `rate_sd_wet`
  {3e-4, 5e-4}: 6 × 3 × 3 × 4 = 216 combinations, P12 and the pick rule unchanged. Runtime 514 s across
  10 processes.

**Live changes in the same review** (no parameter): the stop-log cut (P7) no longer cuts the rain
history, so rain before a change still counts toward the wetness; a rain gauge that hasn't reported
within `lake_stale_minutes` makes the current hour's rain unknown, so wet (P4); `/live/predict` starts
every option from the filtered level (P11 amended).

**Grid** (ft). Best P12 margin over the 9 window/offset pairs, with how many of them pass. Dry jitter is
0.009–0.010 in every row (today 0.025), wet 0.027–0.053 (today 0.048), p99 0.055–0.082 (today 0.072).
Skipped: 156 of 1350 ramp checks without a filter estimate, every row.

| rate_sd_wet | full_in 0.02 | 0.05 | 0.1 | 0.2 |
|---|---|---|---|---|
| 3e-5 | −0.063 (0/9) | −0.066 (0/9) | −0.071 (0/9) | −0.081 (0/9) |
| 5e-5 | −0.031 (0/9) | −0.034 (0/9) | −0.041 (0/9) | −0.055 (0/9) |
| 1e-4 | −0.006 (0/9) | −0.007 (0/9) | −0.009 (0/9) | −0.016 (0/9) |
| 2e-4 | −0.000 (0/9) | −0.001 (0/9) | −0.002 (0/9) | −0.007 (0/9) |
| 3e-4 | +0.002 (9/9) | +0.000 (3/9) | −0.001 (0/9) | −0.003 (0/9) |
| 5e-4 | +0.002 (9/9) | +0.002 (9/9) | −0.000 (3/9) | −0.002 (0/9) |

The previous values (1e-4, 3 h, 20 min, 0.2) score −0.016. The window hardly matters; a lower
`wet_full_in` and a higher `rate_sd_wet` both help the rain_starts shape, at the cost of wet jitter.

**Chosen: `rate_sd_wet_ft_per_min` 3e-4, `wet_window_hours` 3, `offset_minutes` 30, `wet_full_in` 0.05.**
- Dry jitter **0.009 ft against today's 0.025**, wet 0.047 against 0.048 (70 dry and 89 wet pairs
  on the common calls), p99 error **0.079 against 0.072**: worse than today's in the tail, the price of
  a looser filter in rain (the gauge reads high in rain, 2026-11-14).
- The pick rule (lowest dry jitter to 0.001 ft, ties to the lower wet jitter) takes it over the 0.02 in
  rows, whose margin is larger (+0.002) but whose wet jitter is 0.048–0.051.
- **P12 margin +0.0003 ft, binding on rain_starts 0.05 ft/h, model at half rate, 30 min after the onset**:
  storm lag 0.0117 against today's 0.0119, over 23 ramps (4 without a filter estimate). Next: gradual
  0.05 ft/h right/half at 30 min, +0.0015.
- The blind case codex probed now passes with room: rain_starts 0.1 ft/h blind lags 0.033 / 0.019 /
  0.004 ft at 30 / 60 / 120 min against today's 0.024 / 0.024 / 0.021 (P12 allows +0.03 at 30 and 60).

Storm-induced lag (gating), mean over the ramps, ft, filter / today. Rain through the ramp:

| ft/h | onset | model | 30 min | 60 min | 120 min |
|---|---|---|---|---|---|
| 0.05 | abrupt | right | 0.003 / 0.014 | 0.003 / 0.011 | 0.000 / 0.011 |
| 0.05 | abrupt | half | 0.004 / 0.014 | 0.003 / 0.011 | 0.000 / 0.011 |
| 0.05 | abrupt | blind | 0.005 / 0.014 | 0.004 / 0.011 | 0.000 / 0.011 |
| 0.05 | gradual | right | 0.001 / 0.002 | 0.002 / 0.005 | 0.001 / 0.010 |
| 0.05 | gradual | half | 0.001 / 0.002 | 0.002 / 0.005 | 0.001 / 0.010 |
| 0.05 | gradual | blind | 0.001 / 0.002 | 0.002 / 0.005 | 0.002 / 0.010 |
| 0.05 | rain_starts | right | 0.007 / 0.012 | 0.005 / 0.012 | 0.003 / 0.010 |
| 0.05 | rain_starts | half | 0.012 / 0.012 | 0.007 / 0.012 | 0.003 / 0.010 |
| 0.05 | rain_starts | blind | 0.016 / 0.012 | 0.010 / 0.012 | 0.003 / 0.010 |
| 0.1 | abrupt | right | 0.005 / 0.025 | 0.003 / 0.024 | 0.000 / 0.024 |
| 0.1 | abrupt | half | 0.008 / 0.025 | 0.004 / 0.024 | 0.000 / 0.024 |
| 0.1 | abrupt | blind | 0.011 / 0.025 | 0.006 / 0.024 | 0.001 / 0.024 |
| 0.1 | gradual | right | 0.002 / 0.005 | 0.003 / 0.010 | 0.000 / 0.022 |
| 0.1 | gradual | half | 0.002 / 0.005 | 0.004 / 0.010 | 0.002 / 0.022 |
| 0.1 | gradual | blind | 0.002 / 0.005 | 0.005 / 0.010 | 0.004 / 0.022 |
| 0.1 | rain_starts | right | 0.006 / 0.024 | 0.005 / 0.024 | 0.003 / 0.021 |
| 0.1 | rain_starts | half | 0.019 / 0.024 | 0.011 / 0.024 | 0.004 / 0.021 |
| 0.1 | rain_starts | blind | 0.033 / 0.024 | 0.019 / 0.024 | 0.004 / 0.021 |

The rise outlasts the rain (rain stops 120 min after the onset):

| ft/h | model | 30 | 60 | 120 | 180 | 240 | 360 min |
|---|---|---|---|---|---|---|---|
| 0.05 | right | 0.003 / 0.014 | 0.003 / 0.011 | -0.000 / 0.011 | -0.001 / 0.013 | 0.002 / 0.013 | 0.001 / 0.013 |
| 0.05 | half | 0.004 / 0.014 | 0.003 / 0.011 | 0.000 / 0.011 | -0.000 / 0.013 | 0.002 / 0.013 | 0.001 / 0.013 |
| 0.05 | blind | 0.005 / 0.014 | 0.004 / 0.011 | 0.000 / 0.011 | -0.000 / 0.013 | 0.002 / 0.013 | 0.001 / 0.013 |
| 0.1 | right | 0.005 / 0.025 | 0.003 / 0.024 | -0.000 / 0.024 | -0.001 / 0.026 | 0.002 / 0.024 | 0.001 / 0.027 |
| 0.1 | half | 0.008 / 0.025 | 0.004 / 0.024 | 0.000 / 0.024 | -0.000 / 0.026 | 0.002 / 0.024 | 0.001 / 0.027 |
| 0.1 | blind | 0.011 / 0.025 | 0.006 / 0.024 | 0.001 / 0.024 | -0.000 / 0.026 | 0.002 / 0.024 | 0.001 / 0.027 |

Absolute behind (not gating): the filter is never further behind than today's at 60 or 120 min or after
the rain. At 30 min it trails by up to 0.003 ft on the gradual ramps (the time-of-day effect of the
previous entry) and by 0.001 ft on rain_starts 0.1 ft/h blind (0.023 / 0.022).

**Real storms.** 11-08 02Z (+0.19 ft settled): mean behind over 24 h, filter 0.002, today −0.005 ft.
11-10 02Z (+0.13 ft): filter −0.004, today 0.003 ft. The two September storms have no minute readings.

**Known limits** (in addition to the previous entry's, which stand; the study still doesn't apply P7's cut):
- **A rise while the rain gauge reads zero** (snowmelt, a frozen piezo) keeps the filter in its dry
  mode, so it lags: Fable's probe on the previous values, a blind 0.1 ft/h rise with the rain reading 0,
  +0.047 ft more lag than today's anchor at 60 min and +0.093 at 120; on these values +0.045 and +0.090
  (the T1 synthetic, 20 seeds). Not loosened on an unexplained rise (user, 2026-11-15): a known limit
  until snowmelt is modelled deliberately. A rain gauge that stops reporting does count as wet.
- The margin is thin (+0.0003 ft) and the rain_starts sample comes from 4 dry spells in 10 days.
- p99 error is 0.007 ft worse than today's anchor's.

**Comment-only fix (no parameter).** `antecedent.assimilate_baseflow`'s comment said the replay records
are stamped at hour start; they are stamped at hour end, so the baseflow-assimilation window covers the
hour before the one intended. Both reviewers rated the one-hour offset negligible, and the stopgap is
mostly idle since the deep zone; only the comment changed (ruling on #74).

**Anchors.** Unchanged (the filter is not in the model): Step 6 342.72 ft, dry equilibrium 339.661 ft.

### 2026-11-16 — The lake filter runs in dry weather only (user decision, #74)

**Decision (user, 2026-11-16; plan P16, design D47).** `estimate_lake` returns no estimate whenever the
wetness at the newest minute is above 0: any rain, or unknown or stale rain (P4), within
`wet_window_hours`. The live reading is then today's 30-min median (`build_bundle`), and `/field`'s
trend is the median's. **Why:** in the previous entry's grid no passing setting beat today's median
in rain. The settings that held P12 (margins up to +0.002 ft, binding on a rise whose rain starts with
it) had wet jitter no better than the median's (0.047–0.053 ft vs 0.048) and a worse p99 (0.079–0.082
vs 0.072). In dry weather the filter cuts the jitter by two thirds. The wet parameters now only shape
the transition back to dry: the run still filters its wet minutes before the window empties.

**What changed.** `lake_filter.rate_sd_wet_ft_per_min` 3e-4 → **1e-4**, `offset_minutes` **30** and
`wet_window_hours` **3** unchanged, `wet_full_in` 0.05 → **0.1**. Set by `lake-rise lake-filter-study
--write` on the same data, with:
- **The live anchor in the storm checks.** Where the filter yields to rain the study scores today's
  median, as live; a filter with no estimate for want of readings (P9) is still counted as missing.
  So while wet the checks compare the median with itself, and they test the filter only once its
  window is dry.
- **rain_starts aligned** (codex re-review). Its rain began one minute before the ramp's first rise;
  every shape's rain is now timed from the first rise (one minute later for the others too, which have
  a 2 h lead).
- **A new pick rule** (ruling on #74), among the P12-passing rows: (1) a p99 no worse than today's
  anchor's; (2) then the most hourly calls on the filter (coverage, a new column); (3) then the lowest
  p99; (4) then the lowest dry jitter. If no row meets (1), the lowest p99, and the CLI says so. The old
  rule (dry jitter, ties to the wet jitter) tied every row on dry jitter and let a wet jitter scored on
  window-dependent calls decide: it picked a 6 h window with a p99 of 0.048 against the median's 0.040.
- P12 and the grid are unchanged. Runtime 66 s across 10 processes. Onsets: abrupt, gradual and
  outlasts 12 each, rain_starts 27.

**Grid.** All 216 combinations pass P12 at a margin of 0.000 ft: the binding checks are in rain, where
the live anchor is the median. Dry jitter is **0.009 ft in every row, against the median's 0.025** on the
same 70 dry pairs. The rest depends on the window (only the window decides when the filter resumes):

| wet window | filter wet jitter | today wet | filter p99 | today p99 | rows with p99 ≤ today's | wet pairs | on filter |
|---|---|---|---|---|---|---|---|
| 3 h | 0.008–0.012 | 0.024 | 0.039–0.047 | 0.040 | 46 of 72 | 28 | **112 of 229** |
| 6 h | 0.006–0.018 | 0.025 | 0.039–0.048 | 0.040 | 59 of 72 | 17 | 97 of 229 |
| 12 h | none (never runs within 12 h of rain) | – | 0.038 | 0.040 | 72 of 72 | 0 | 75 of 229 |

"Wet" pairs are those with rain in the 12 h before the later call, where the filter has resumed; p99
is over the calls where both have a value, so the call set varies with the window. "On filter" is the
coverage: hourly calls whose live anchor is the filter (the rest are today's median, in rain or for want
of readings). Only the window sets it.

**Chosen: `wet_window_hours` 3, `offset_minutes` 30, `rate_sd_wet_ft_per_min` 1e-4, `wet_full_in` 0.1.**
- Among the 46 three-hour rows with today's tail, the lowest p99: **0.039 ft against the median's 0.040**.
  Dry jitter **0.009 against 0.025** (70 pairs); after rain, once resumed, 0.010 against 0.024 (28 pairs).
- **Coverage: the filter is the live anchor on 112 of the 229 hourly calls (49 %)**; today's median on
  92 in rain (40 %) and 25 for want of readings (11 %). With a 6 h window it would be 97, with 12 h 75.
- **The rise that outlasts the rain:** at 180 and 240 min after the onset (1 and 2 h after the rain) the
  window is still wet and the live anchor is the median: 0.013 ft behind on the 0.05 ft/h ramp, 0.025 on
  the 0.1 ft/h ramp. At 360 min (4 h after the rain) the filter has resumed and trails less than the
  median: 0.0074–0.0075 against 0.0129 (0.05 ft/h) and 0.0153 against 0.0273 (0.1 ft/h), right to blind,
  over 11 ramps (1 without an estimate).
- **Real storms**, mean behind over the next 24 h, live anchor / today: 11-08 02Z −0.005 / −0.005 ft,
  11-10 02Z −0.006 / −0.006 ft (the day after a storm is mostly inside the window).

**Known limits.**
- **A rise while the rain gauge reads zero** (snowmelt, a frozen piezo) is filtered as dry weather, so
  it lags: on a blind 0.1 ft/h rise, +0.020 / +0.045 / +0.090 ft more than the median at 30 / 60 / 120 min
  on these values (the T1 synthetic, 20 seeds; Fable's probe on the earlier values: +0.047 at 60 and +0.093
  at 120). Not loosened on an unexplained rise (user, 2026-11-15); a known limit until snowmelt is
  modelled deliberately. A rain gauge that stops reporting counts as rain.
- Coverage is half the hourly calls on this window; the live reading alternates between the filter and
  the median around every rain (that jitter is unscored).
- The earlier entries' limits (the truth is the gauge's own median, the daily cycle, the small sample)
  stand; the study still doesn't apply P7's cut.

**Anchors.** Unchanged (the filter is not in the model): Step 6 342.72 ft, dry equilibrium 339.661 ft.

### 2026-11-23 — EAP action table revised (KI08-0195); no parameter changed

**New source:** a revised Water Level / Action Table from the Emergency Action Plan (user,
2026-11-23). WQ-pole (stick) readings, the table's colour, and the action:

| Stick | Colour | Action |
|---|---|---|
| 0.65 ft | green | Winter, one extra stop log removed |
| 0.97 ft | green | Winter, normal stop logs |
| 1.30 ft | green | Summer, normal stop logs |
| 2.30 ft | yellow | **Emergency Level 1** — alert DSO. Water up one foot: monitor daily, track the rate of rise; if the forecast is bad, optionally lower the stop logs 4 in or more |
| 2.60 ft | yellow | Lower the stop logs 4 in or more, if not already done |
| 3.30 ft | red | **Emergency Level 2** — alert NORCOM, KCOEM, DSO. Water up two feet: contact east-side residents who may want their cars on the west side |
| 3.90 ft | red | Bridge closed to all vehicles. Overtopping starts 25 ft east of the bridge. Tarps and plastic, sandbagged, on the downstream slope at the point of overtopping. Remove the remaining stop logs |
| 4.40 ft | red | **Emergency Level 3** — alert NORCOM, KCOEM, DSO. Bridge deck overtopped; overtopping by 6 in for 12 h: "imminent failure of the dam" |

**What changed from the earlier EAP:** two new rows below 3.30 ft (Emergency Level 1 at 2.30,
the stop-log step at 2.60); the agencies are now NORCOM, KCOEM and DSO (SMO, RCEC and KCDOT are
gone); sandbags and plastic move from 3.30 to 3.90 ft, at the point of overtopping; pulling the
last stop logs is now an EAP step at 3.90; and the table no longer says "evacuate downstream".

**What did not change:** the pool levels (0.65 / 0.97 / 1.30 ft, so `board_heights_ft` and the
seasonal default stand) and the overtopping geometry (starts at 3.90, bridge deck at 4.40, the
0.50 ft interval behind `bridge_deck_elev_ft`). The model's thresholds and the datum are untouched,
so the anchors are unchanged: Step 6 342.72 ft, dry equilibrium 339.661 ft.

**Code (design D49):** `alerting/eap.py` holds the five action rows; every row pages on the observed
track (2.30 and 2.60 to ops; then emergency, road, evacuate as before), re-arming below 2.25 ft
(was 3.25). The field view gains an "EAP 1" line at 2.30; the 3.30 line is "EAP 2" (key `eap2`,
was `eap_plastic`). The forecast ladder's EVACUATE level is shown as "Imminent Failure".

**"Overtopping by 6 in for 12 hours" means 6 in over the low point (user, 2026-11-23).** The
interval from where overtopping starts to the bridge deck is 4.40 − 3.90 = 0.50 ft, exactly 6.0 in,
and the wording sits on the 4.40 ft row. So the failure condition is the lake held at the bridge
deck (4.40 ft stick; model 342.7 abs, ~22 cfs over the low point) for 12 h. The other reading,
6 in over the deck, would be 4.90 ft stick (model 343.2 abs, ~103 cfs across the whole crest), a
level the table gives no row for. The 2026-06-13 entry, the artifact's overtopping comment and
design U8 said "over the deck" and are corrected. The page fires at the 4.40 ft crossing and prints
the wording; it does not time the 12 h.

**Pending:** measured stop-log heights are due within days (user, 2026-11-23). They replace the
provisional `board_heights_ft` (2026-11-05 entry), which come from these pool levels; re-check the
dry-equilibrium anchor (3 boards) then.

### 2026-11-25 — Stop-log crests from a laser-level survey; the aux carries logs too

**Observed (user, 2026-11-25):** a laser-level survey of the stop logs against the lake stick,
and the configuration explained:

- The current setup is what the EAP calls **"1 log"**: one log in the aux spillway, and **three
  base boards (3.5 in each)** in the main, so the two tops sit at about the same height.
- Tops: **main 0.98 ft, aux 0.99 ft** on the stick (estimated within 3/8 in of each other).
- Each log added to the spillways raises them **~5 in**.
- **Winter normal is 1 log** (the EAP's 0.97 ft winter pool), so summer normal is 2 logs and the
  EAP's "one extra removed" (0.65 ft) is 0 logs. (The current setup was described first as a test
  configuration; the user then confirmed winter normal = 1 log.)
- The main's logs are rarely pulled below 1 log. Its **bottom base board is mortared in place**,
  so only the other two 3.5-in boards can come out, and only in an emergency.
- In summer the two crests will be levelled at the main's (lower) height.
- **The EAP is not changed** by this (user): its levels and its 0.65 / 0.97 / 1.30 pool numbers
  stand. Only the forecast model uses the survey.

**What was wrong:** the model took its board heights from the EAP pool levels (1 board 0.65,
2 boards 0.97, 3 boards 1.30 ft, all on the primary). It held the aux at a fixed 340.0 ft
(1.625 ft stick), independent of the stop logs. In fact the aux carries logs too, and its 1-log
crest is 0.99 ft, 0.6 ft lower. The old board counts were one more than the EAP's logs (old 2 = new
1, old 3 = new 2).

**Change:**
- `stop_logs` now holds each leg's surveyed 1-log crest in stick ft, the log height (0.417 ft),
  and summer 2 / winter 1. Pulling to 0 logs drops the aux by one log and leaves the main's base
  boards in. Crests in abs ft (offset 338.375): 0 logs 339.355 / 338.948, 1 log 339.355 / 339.365,
  2 logs 339.772 / 339.782, 3 logs 340.189 / 340.199 (main / aux). Emergency floor
  (`Artifact.emergency_control`, not a count): main 338.772 (0.397 stick), aux 338.948.
- The spillway takes both crests (`SpillwayControl`). Each leg keeps its weir coefficient from
  its documented rating (`rated_crest_elev_ft` 338.8 / 340.0, C ~1.92 / 1.89, so the
  corroboration test stands). The leg then spills over its stop-log crest.
- Seam bottoms moved to the surveyed bare sills (main 338.48, aux 338.948). Leakage is 0, so
  there is no effect.
- **Recorded counts:** the operator helper and the archive have been in boards. Counts recorded
  before `stop_logs.board_counts_before` read as count − 1. Storm records now carry
  `stop_log_unit` ("boards" for older ones), and their replay rebuilds both crests from the count.
  The live helper is read as logs, so it **must be set to 1** when this deploys. Set
  `board_counts_before` to that instant.
- The dry-equilibrium anchor is now at summer normal (`dry_equilibrium_summer_ft`, the same
  observed 339.6–339.8 band). Step 6 runs on the design study's rated crests (`rated_control`),
  as before.
- Alert/field wording: the 0-log option is no longer captioned "Pull all", and the 0-log action
  says only the main's emergency base boards are left (not "bare sill").

**Effect:**
- **Anchors:** Step 6 is unchanged at 342.72 ft. The dry equilibrium is 339.757 ft (1.38 stick),
  just under the 2-log crest (was 339.661 under 3 boards).
- **Summer outflow** (2 logs) is about the old 3-board outflow: 28.4 vs 27.1 cfs at 2.30 ft stick,
  111.5 vs 109.1 at 3.90.
- **Winter outflow** (1 log) is markedly higher, because the aux spills from 0.99 rather than
  1.625 ft: 5.9 vs 3.6 cfs at 1.30 ft stick, 50 vs 37 at 2.30, 145 vs 125 at 3.90. So winter
  peaks forecast lower than before.
- **June 11 field check:** stick 1.5, a ~1-in stream over the primary and a trickle over the aux.
  That fits the 2-log crests (heads ~0.10 / 0.09 ft) better than the old 1.30 / 1.625 crests did.

**Not yet re-derived:**
- *(a)* The June 12 baseflow target. At 339.91 the spillway now passes 1.63 cfs, not 2.19, so the
  lake-implied delivery drops from ~3.69 to ~3.1 cfs. The model delivers 3.52, inside the event's
  2.3–4.6 noise band. `PERC_coeff` (0.42) is left alone; re-fit it on the replay rather than hand-tune.
- *(b)* `lake-rise lake-filter-study`. It needs the NAS minute archive; re-run it after deploy.
- *(c)* The aux soffit stays at 341.3 until we know which sill its 2.5 ft was measured from
  (341.45 if from the aux's bare sill).
- *(d)* The datum offset. The survey ties the logs to the stick, not to absolute elevation; the
  overtopping levels and the 338.375 offset stand.
- *(e)* The EAP's pool numbers (0.65 / 1.30) sit 0.08 / 0.10 ft from the surveyed 0- and 2-log
  crests (0.57 / 1.40, at 5 in per log; the EAP implies 4-in steps). That is noted, not acted on.

### 2026-11-27 — Far-echo gaps are routine; the field page waits 6 h to flag them (no parameter changed)

**Observed (Home Assistant sensor work, 2026-10-27 → 11-27, handed over 2026-11-27):**

- `sensor.air_gap_filtered` now keeps raw air-gap readings of **5–30 in** (was 25 in at the top).
- The raw laser also reports a **fixed far echo at ~46.3 in** that doesn't move with the lake
  level. At times it makes up most of the readings. That's expected (the bottom through clear
  water), not a fault. New far-echo sensors give an implied bottom of **−1.38 ft** on the stick,
  with the refraction factor at 1.00.
- On calm days the laser reads **0.89 ft against the stick's 0.90 ft**, so the June calibration
  holds (2026-06-11 tape-down, 2026-11-14 24-h median).
- Runs of far echoes leave **no valid water-surface reading for up to 4.7 h** (the 2026-11-26
  storm), and for ~3 h routinely in October. In HA's last 10 days `sensor.crystal_lake_depth`
  was `unknown` in 98 separate spells, ~24 h in all (~10 % of the time). The raw sensor keeps
  reporting about once a minute throughout.

**What changed (UI only).** The field page flagged "gauge reading is invalid" from the first
invalid minute, so it showed most days in normal operation. `LiveHASource.gauge_alert` now holds
that flag back until the surface has been gone for **`HAConfig.lake_invalid_alert_hours` = 6 h**
(above the 4.7 h worst case seen; env `LAKE_RISE_LAKE_INVALID_ALERT_HOURS`). The flag then reads
"no valid water-surface reading for N h". The gap starts at the raw depth sensor's
`last_changed` when it went `unknown`. Until the flag shows, the details row says
"no surface reading for H h M min, showing the last one" (`details.surface_age_min`). A silent or
unreachable sensor still flags after 15 min (`lake_stale_minutes`): the raw sensor reports about
once a minute, so that check can stay tight.

**Not changed:** `gauge_problem` still treats the gauge as degraded from the first invalid
reading. That keeps the forecast's freshness flag (→ #4 seasonal floor) and the observed-EAP
reading (not confirmed) as the 2026-11-05 entry set them.

**Open:** the alert level still reads Normal while the depth is `unknown`. A missing-surface
alert would need the same ≥ 5–6 h window. A silent-sensor alert can be much tighter.

### 2026-11-27 — The fallback lake reading skips far-echo gaps (no parameter changed)

**Found (user: "fairly active variability in the starting point").** The live forecast starts at the
lake filter's level only in dry weather with ≥ 20 valid minutes in the last 30 (P9, P16). Otherwise it
used the median of `crystal_lake_depth_smoothed`'s rows over the last 30 min, and with no rows the
sensor's current state. Through a far-echo gap (previous entry) the raw depth is `unknown` and the
smoothed sensor holds the sample it took as the laser lost the surface. Those held samples are often
off: 1.06, 1.08, 1.09, 1.04 and 0.72 ft in the 2026-11-26 storm, and 1.00 ft at 11:26 on 11-27, when
the lake had read ~0.90. So the starting level jumped between the filter's steady reading and a held
outlier.

**Change (P17 in docs/plans/lake-filter.md).** The fallback is `lake_filter.median_anchor`: the median
of the smoothed depth over the latest 30 minutes in which the raw depth had a valid reading, looking
back up to 12 h (`ANCHOR_LOOKBACK_HOURS`). Without a valid minute in 12 h it is still the smoothed
sensor's current state (and the gauge flag is up by then). The `/field` trend's fallback uses the same
reading now and an hour ago; the lake-filter study's "today" comparator uses it too, so the study
compares the filter with the reading the page would actually show.

**Evidence** (minute readings 2026-11-17 → 11-27, 226 hourly calls, 24 of them in a gap; error
against the study's truth, the centred 4 h median of the raw depth):

| Fallback reading | jitter outside gaps | jitter in gaps | p95 error | p99 error | worst gap error |
|---|---|---|---|---|---|
| Before: median of the smoothed rows | 0.026 ft | 0.069 ft | 0.040 | 0.060 | 0.110 |
| After: smoothed, valid-surface minutes only | 0.028 ft | 0.035 ft | 0.040 | 0.060 | 0.090 |
| (not chosen) raw, last 60 valid minutes | 0.024 ft | 0.026 ft | 0.030 | 0.057 | 0.075 |

A 60-minute raw median is steadier still, but it lags a storm rise by ~30 min instead of ~15, and
P16 kept the 30-min median in rain on purpose. Live at 13:50 PST on 11-27, 146 min into a gap: the
forecast starts at 0.89 ft (was 1.00), trend steady.

**Not re-run:** `lake-rise lake-filter-study` needs the NAS continuous archive (the local copy ends
2026-07-08). Re-run it there: its comparator changed, though the filter's values did not.
In a storm gap, holding the last valid half hour still under-reads a rising lake. The gauge flag
(previous entry) shows after 6 h.

### 2026-11-27 — `PERC_coeff` re-examined against the surveyed crests: held at 0.42; filter study re-run; spillway drawing

The follow-ups the 2026-11-25 survey left open, run on the NAS archive (continuous 2025-06-05 →
2026-11-27, minute 2026-11-06 → 11-27; copied read-only over SSH).

**June target re-derived: 3.69 → 3.10 cfs.** The archive has no lake levels before 2026-06-29, so
this re-prices the logged 2026-06-12 balance, not new data. Under the survey, June's "3 stop logs"
(old board count) are the 2-log summer setup, with crests 1.397 / 1.407 ft. At the June level
(1.535 ft stick, 339.91) **both** spillways spill: 1.63 cfs, where the old model had the primary
alone at 2.19. The June 11 field note fits this: a stream over the primary, a trickle over the aux.
Target = rise 0.79 + spill 1.63 + evaporation 0.68 = **3.10 cfs**.

**Refit and checks.** The June delivery comes from the pure archive replay (111 d from 2026-02-21).
The November backtests run the way the live forecast does (archive replay plus assimilation, from
the archive, 1 log). The fall storms use the 2026-11-14 method, rebuilt: the same 8 storms and
6.8 in (logged 6.9), with modeled 0.18 ft at 0.42 (logged 0.24). End and peak errors are ft, model
minus gauge.

| `PERC_coeff` | June 06Z (target 3.10) | Step 6 | 8 fall storms, modeled (measured −0.01) | Nov 5–14 end | 72 h Nov 9 end | 72 h Nov 11 peak / end | Nov 25–27 peak / end |
|---|---|---|---|---|---|---|---|
| **0.42 (kept)** | 3.52 | 342.72 | 0.18 ft | +0.00 | +0.16 | +0.12 / +0.14 | +0.26 / +0.44 |
| 0.30 | 3.40 | 342.88 | 0.18 ft | +0.01 | +0.17 | +0.13 / +0.14 | +0.33 / +0.51 |
| 0.13 (fits June) | 3.10 | 343.08 | 0.21 ft | +0.06 | +0.21 | +0.17 / +0.18 | +0.62 / +0.80 |

Delivery *rises* with `PERC_coeff` here: more percolation fills the deep zone and groundwater by
June. A lower value keeps the soil wetter, so more rain sheds to interflow in a storm.

**Decision: hold 0.42.** The June target is one noisy event (the old band was 2.3–4.6 cfs), and
3.52 is still inside its re-priced band. The November backtests are several recent storms,
including the first wet-season one, and every one gets worse as the value drops. Fitting June
alone would double the over-prediction of the Nov 25–27 storm. The dry equilibrium is unchanged
at 339.757 in every row.

**Nov 25–27 storm (2.82 in): the balance doesn't close.**
- The lake settled from ~0.82 ft (Nov 25) to ~0.885 ft (Nov 27), +0.065 ft, against 0.235 ft of
  rain falling on the lake alone. That is −0.17 ft of watershed water, which isn't possible without
  an outflow.
- The 0.95–1.08 ft readings during the storm fall in the laser's far-echo period (2026-11-27
  entries), so whether the lake reached the 0.98 ft crests is unknown. Spill over a 0.98 crest can't
  take it down to 0.89 anyway.
- Candidates: the gauge, the rain gauge over-reading, or an outflow the model lacks. For example,
  the aux log's seams now sit ~0.3 ft under the lake (its bare sill is 0.57), and seam leakage is 0
  in the model.
- Not fitted. Ask the operator whether water went over the logs on Nov 26.
- The model at 0.42 peaks at 1.34 ft stick (+0.26) and ends +0.44 ft. With the old crests (aux at
  1.625) it would spill less and run higher still.

**Lake-filter study re-run** (`lake-rise lake-filter-study`, first wet-season storm plus the
spillway change).
- Pick: `rate_sd_wet` 3e-5, window 3 h, offset 15 min, `wet_full` 0.2 in. Current: 1e-4 / 3 / 30 /
  0.1.
- The two rows are equal within 0.002 ft: dry jitter 0.010 both, wet 0.010 vs 0.012, p99 0.047 vs
  0.048. Both pass P12 (margin 0.000).
- On the six real storms the live anchor runs at most 0.011 ft behind.
- **Not written:** a tie, and the lake-filter work is in progress (the 2026-11-27 entries above).
  Re-run once that work settles.

**Spillway drawing.** `docs/spillway-elevation.svg`, made by `lake-rise spillway-drawing` from the
artifact (`stop_logs`, `spillway`) and the EAP table (`alerting.eap`, now with the pool rows as
`EAP_POOL_ROWS`). It is a front elevation: vertical to scale in stick feet, horizontal
representative. It shows each board and log with its height and top, the winter and summer
overflow points, every EAP line, and the model's overtopping levels where they differ from the
EAP's. Redraw it, don't edit it, when the survey or the EAP changes.

**The bridge spans the main spillway, not the aux** (operator, 2026-11-27). So the main's opening
top (`spillway.primary.soffit_elev_ft` 342.0, stick 3.63, still inferred from the rating) is the
bridge underside, and the aux's measured 341.3 (stick 2.93) is only that opening's top. The
artifact comments said otherwise; no value changes.

## Structural findings & open items

- **Fall baseflow 3–6× too high (top open item, 2026-11-04; worked around 2026-11-05 by
  `baseflow_assimilation`; structural fix 2026-11-14: the deep zone).** Lake-implied inflow after
  the fall storms was ~0.5 → ~0.2 → ~0 cfs; the replayed model held 0.8–1.7 cfs, and now holds
  0.16–0.25 cfs Sep–Nov. One 23-day groundwater store couldn't serve both the June 3.7 cfs target
  and the dry fall. The deep zone separates them; re-fit it, `PERC_coeff`, `AGWRC` and `DEEPFR`
  on a wet-season recession.
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
- ~~**Stop-log heights: measurements due (2026-11-23).**~~ *Resolved 2026-11-25:* the laser survey sets
  each spillway's crest (2026-11-25 entry). Re-fit and filter study done 2026-11-27 (`PERC_coeff`
  held at 0.42). Still open: which sill the aux soffit's 2.5 ft was measured from, and why the
  Nov 25–27 storm's water balance doesn't close (2026-11-27 entry).
- **Sensor is genuinely noisy** (~0.6–1.4 in within-hour, on the `_smoothed` entity). Smoothing
  conventions mitigate it but the noise floors the live anchor (~0.21 in) — a hardware / mounting /
  HA-filter item worth chasing, and worth checking whether it correlates with wind (a real seiche,
  harmless) vs electrical noise or the laser re-locking on the water surface (2026-11-08 entry).
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
