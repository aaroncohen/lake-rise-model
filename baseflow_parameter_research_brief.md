# Groundwater / Baseflow Parameters for the Crystal Lake HSPF-Style Model

**Research brief — prepared 2026-06-11**
**Scope:** add a research-grounded active-groundwater (baseflow) reservoir to the lumped, hourly, HSPF-style Crystal Lake rainfall–runoff model.
**Geology confirmed:** Crystal Lake watershed (Daniels Creek, King County, ~2,131 ac, forested, Puget lowland) is glacial **till** — the dam embankment fill and foundation are logged as glacial till over hard clay (2025 PIR), and the watershed memo records an **interflow-dominated** response, which is the diagnostic signature of compacted till ("hardpan"). The "Till Forest" PERLND lineage is therefore the correct target. Outwash values are given alongside for contrast and for any sandy sub-areas.

---

## Bottom line up front

1. **You are not missing one parameter — you are missing the entire active-groundwater limb.** Your current model deletes `deep_loss_fraction` (0.11) *and* implicitly discards the other 89% of any percolation, because there is no groundwater store. In HSPF the deep fraction (`DEEPFR`) is the *only* part that should leave permanently; the remaining `(1 − DEEPFR)` becomes **active groundwater** that returns to the lake as baseflow. Routing that 89% into a slow reservoir is the fix.

2. **The slow store is genuinely slow.** The Dinicola/Ecology calibrated Till value is **AGWRC = 0.996 /day**, a baseflow half-life of **~173 days** — three orders of magnitude slower than your interflow (IRC = 0.5 /day, ~1-day half-life). That timescale separation is exactly what produces "lake holds or rises for days on little rain."

3. **Nonlinear recession is warranted for till.** Ecology's calibrated Till value is **KVARY = 0.5 /in** (not zero) — faster recession when groundwater is high (wet season), slower when low.

4. **A citation correction:** the source you cite as "Dinicola 1990, WRIR 90-4052" is actually **WRIR 89-4052** (published 1990). See the citation note at the end.

---

## (A) Parameter table

Recommended values are for **Till Forest**, the matching PERLND for Crystal Lake. "Source" gives the document and the exact locator. Units and daily↔hourly conversions are in the notes.

| Parameter | Symbol | Recommended (Till Forest) | Range | Units | Source (citation + locator) | Notes |
|---|---|---|---|---|---|---|
| Active-GW recession constant | **AGWRC** | **0.996** | Till & outwash both 0.996 (Ecology); EPA typical 0.92–0.99, possible 0.85–0.999 | dimensionless, ratio /day | Ecology WWHM **Appendix III-B**, *PERLND Parameters* table, p. B-8 (TF column); range from EPA **BASINS Tech Note 6**, Summary Table p. 29–30, def. p. 11 | t½ = ln0.5 / ln(0.996) = **173 days**; e-folding τ = 250 days. Hourly ratio = 0.996^(1/24) = **0.999833**. EPA notes forests use higher AGWRC than open land (Chen et al. 1995: 0.996 for high-density forest). |
| Variable/nonlinear recession | **KVARY** | **0.5** | Till 0.5, outwash 0.3 (Ecology); EPA typical 0.0–3.0, possible 0.0–5.0 | /inch | Ecology WWHM **Appendix III-B**, p. B-8 (TF = 0.5, OF = 0.3); range EPA **Tech Note 6**, p. 29, def. p. 11 | >0 means recession steepens at high GW storage. Start here; confirm against your own wet- vs dry-season recession slopes. |
| Deep/inactive-GW fraction | **DEEPFR** | **0.00–0.10** (see notes; your 0.11 is at the upper edge of defensible) | EPA typical 0.0–0.20, possible 0.0–0.50 | fraction | EPA **Tech Note 6**, Summary Table p. 29, definition p. 13 | **Not set in WWHM** (WWHM discards all GW, so it never assigns DEEPFR). Tech Note 6 p. 13: lowland areas *near the discharge point* lose little to deep GW; upland/high-elevation areas lose more. Crystal Lake is a low-relief closed basin discharging to the lake → physically argues for a **low** DEEPFR (≈0.0–0.1). Your 0.11 is inside the typical band but is **not** a sourced Till value — flag and calibrate. |
| Interflow recession | **IRC** | 0.5 (your current value — correct) | Till 0.5, outwash 0.7; EPA typical 0.5–0.7, possible 0.3–0.85 | ratio /day | Ecology WWHM **Appendix III-B**, p. B-8; range EPA **Tech Note 6**, p. 30, def. p. 15–16 | Fast limb. t½ ≈ 1 day; hourly = 0.5^(1/24) = 0.9715. Separation from AGWRC ≈ 170×. |
| Interflow inflow ratio | INTFW | 6.0 (your current value — correct) | Till 6.0, outwash 0.0; EPA typical 1.0–3.0, possible 1.0–10.0 | dimensionless | Ecology WWHM **Appendix III-B**, p. B-8 | Till's high INTFW vs outwash 0.0 is *why* till is interflow-dominated. |
| Riparian ET from baseflow | BASETP | 0.00 | EPA typical 0.0–0.05, possible 0.0–0.20 | fraction of PET | Ecology WWHM **Appendix III-B**, p. B-8 (TF = 0.0); EPA **Tech Note 6** p. 29, def. p. 13 | Negligible in cool-season recession (your problem window). Only matters for summer low-flow. |
| Active-GW ET | AGWETP | 0.00 (0.7 only for wetland PLS) | EPA typical 0.0–0.05, possible 0.0–0.20; 0.3–0.7 wetlands | fraction | Ecology WWHM **Appendix III-B**, p. B-8 (TF = 0.0, Saturated = 0.7); EPA **Tech Note 6** p. 29, def. p. 13 | If you model the lake-fringe wetland as a separate unit, give it AGWETP ≈ 0.3–0.7; otherwise 0.0. |
| Lower-zone storage | LZSN | 4.5 (your current value — correct) | Till 4.5, outwash 5.0; EPA typical 3.0–8.0 | inches | Ecology WWHM **Appendix III-B**, p. B-8 | Controls percolation-to-GW via LZS/LZSN ratio. |
| Infiltration index | INFILT | 0.08 (your current value — correct) | Till 0.08, outwash 2.0; EPA typical 0.01–0.25 | in/hr | Ecology WWHM **Appendix III-B**, p. B-8 | Divides surface vs subsurface; low till value forces water subsurface (interflow + GW). |

**Reading the AGWRC = 0.996 "all soils" result:** Ecology uses 0.996 for till, outwash, *and* saturated soils. The soils differ in how much water *reaches* groundwater (INFILT, INTFW), not in how fast the GW store then drains. So the recession constant you adopt is robust; the soil-dependent action is upstream, in the percolation partition.

---

## (B) Structural recommendation — wiring the baseflow store

### B.1 What HSPF actually does (the structure to mirror)

In HSPF `PWATER`, infiltrated and percolated water is partitioned roughly as:

```
            rainfall
               │
        ┌──────┴───────┐
   (INFILT divides)     │ overland flow (SURO)  ← fast
        │
   soil column ──► interflow store ──(IRC)──► INFW   ← intermediate (your existing limb)
        │
   percolation to lower zone / groundwater (PERC)
        │
   ┌────┴─────────────────────────┐
   │ DEEPFR · PERC  → inactive GW  │  ← permanent sink (leaves basin)
   │ (1−DEEPFR)·PERC → active GW   │  ← NEW reservoir you need
   └──────────────────────────────┘
        active GW store (AGWS) ──(AGWRC, KVARY)──► AGWO baseflow → lake
```

The active-groundwater outflow in HSPF is
`AGWO = KGW · (1 + KVARY · GWVS) · AGWS`,
where `KGW` is the recession rate derived from AGWRC and `GWVS` is the groundwater-slope index that makes recession nonlinear (the KVARY term). When KVARY = 0 this collapses to a simple linear reservoir `AGWO = (1 − AGWRC_perstep)·AGWS`.

### B.2 Minimal patch to *your* simplified model

Your model is one soil bucket (fills to LZSN; sheds by overflow + ET) + one interflow linear reservoir (IRC) + lag + lake routing. Add **one** linear reservoir and re-plumb the deep-loss flux:

1. **Add a percolation flux** from the soil bucket toward groundwater. Two options, simplest first:
   - **(a) Re-plumb existing deep loss (smallest change).** You already compute a deep-loss flux of `0.11 × (percolation/infiltration)`. Stop deleting it. Instead split it:
     `to_deep_sink = DEEPFR · P` (permanent) and `to_active_GW = (1 − DEEPFR) · P` (to the new reservoir). With DEEPFR ≈ 0.05–0.11, ~90% of what you currently delete now becomes baseflow.
   - **(b) Faithful HSPF percolation (better).** Make percolation a function of soil saturation, `P = 0.1 · INFILT · INFILD · (LZS/LZSN − 1)^INFEXP` when `LZS > LZSN` (HSPF form; INFILD = 2.0, INFEXP = 2.0 from the Till column). Then apply the DEEPFR split to `P`.
2. **New active-GW reservoir** `AGWS`: each hourly step,
   - inflow `+= (1 − DEEPFR) · P`
   - outflow `AGWO = (1 − AGWRC^(1/24)) · AGWS` for the linear case, or
     `AGWO = (1 − AGWRC^(1/24)) · (1 + KVARY · GWVS) · AGWS` for the nonlinear case, where you can approximate `GWVS ∝ AGWS` (normalized) if you don't track HSPF's exact slope index.
   - `AGWS −= AGWO`; route `AGWO` straight to the lake (it is slow enough that the 4.6-hr basin lag is negligible — you may bypass the lag for this limb).
3. **Keep DEEPFR as the only true sink.** Everything else returns.

### B.3 Decisions, answered

- **DEEPFR split:** set permanent-loss to **DEEPFR ≈ 0.05–0.11**, not 0.11-as-total-loss. Physically a low-relief, closed, lake-terminating till basin should lose little out of basin (Tech Note 6 p. 13), so the lower end is more defensible; calibrate to close the annual water balance.
- **Nonlinear (KVARY) recession:** **yes, warrant it** — Ecology's calibrated Till value is 0.5 /in. If you want to start linear for simplicity, set KVARY = 0 first, get the linear store working, then add KVARY = 0.5 and check that wet-season recession steepens to match the gauge.
- **AGWRC:** start at **0.996 /day** (hourly 0.999833). This is at the slow end; if your gauge recession is faster, lower it toward the EPA typical 0.92–0.99 band, but expect a slow, sustained tail for forested till.

---

## (C) Empirical method — derive AGWRC and BFI from your own gauge

You have an hourly lake-stage series and a stage–storage–discharge (spillway) relationship, so you can convert stage to outflow Q and run standard recession/baseflow analysis. Recommended two-track approach.

### C.1 Master-recession curve → AGWRC (the direct method)

Recession is exponential: **Qₜ = Q₀ · kᵗ**, where *k* is the daily recession ratio = AGWRC (when KVARY = 0).

1. **Build outflow series.** Convert hourly stage → spillway discharge via your FTABLE; aggregate to daily mean to suppress diurnal/measurement noise (recession constants are defined on daily flow — EPA Tech Note 6 p. 11).
2. **Isolate recessions.** Select runs of **≥ 5 consecutive days of declining flow** during rain-free periods (the late tail of each recession is the groundwater limb; drop the first 2–3 days, which are still interflow). The 5-day minimum follows Eckhardt-filter practice (≥3) extended for a till basin's slow limb.
3. **Estimate k.** Two equivalent estimators:
   - **Log-linear regression (Vogel & Kroll 1996):** regress `ln Q` on time *t* across all selected recession segments; slope = `ln k`, so `k = e^slope = AGWRC`. Vogel & Kroll's recommended estimator removes the shortest segments and the noisy early points and pools many recessions into one master curve.
   - **Matching-strip / correlation (Nathan & McMahon 1990):** plot `Qₜ` vs `Qₜ₋₁`; the slope of the upper envelope of the falling-limb points is `k`. Nathan & McMahon found the matching-strip method the more robust of the automated techniques.
4. **Convert to your timestep:** hourly recession ratio = `AGWRC^(1/24)`; e-folding time τ = `−Δt / ln(AGWRC)`. (At 0.996/day, τ ≈ 250 days.)
5. **Test for nonlinearity (KVARY):** estimate *k* separately for wet-season and dry-season recessions. If wet-season *k* is meaningfully **lower** (steeper recession), KVARY > 0 is justified; the size of the gap scales KVARY (start 0.5 /in, EPA p. 11).

### C.2 Baseflow-separation filter → BFI (the cross-check)

Use a recursive digital filter on the daily Q series to split baseflow from total flow; BFI = Σbaseflow / Σtotal.

**Eckhardt (2005) two-parameter filter** — recommended:

> bₖ = [ (1 − BFImax)·a·bₖ₋₁ + (1 − a)·BFImax·yₖ ] / (1 − a·BFImax),  subject to bₖ ≤ yₖ

where `yₖ` = total flow on day k, `bₖ` = baseflow, **a = AGWRC** (the recession constant you derived in C.1), and **BFImax** = maximum long-term baseflow fraction. Eckhardt's guidance: **BFImax ≈ 0.80** for perennial streams with porous aquifers, **0.50** for ephemeral streams with porous aquifers, **0.25** for perennial streams with hard-rock aquifers. A perennial, till-mantled, glacial-sediment basin sits between the first two; the Wolock-grid BFI of **0.67** (D.1) confirms the porous end → **use BFImax ≈ 0.80** (it must exceed the observed 0.67).

**Lyne & Hollick (1979) one-parameter filter** — common alternative / sanity check:

> qf(k) = α·qf(k−1) + [(1+α)/2]·(yₖ − yₖ₋₁),  baseflow b = y − qf, clipped to 0 ≤ b ≤ y

with **α ≈ 0.925** (Nathan & McMahon 1990) and three passes (forward–backward–forward).

**USGS tools** (if you prefer turnkey, no coding): the **USGS Groundwater Toolbox** (Barlow et al. 2015, USGS TM 3-B10) bundles **PART**, **HYSEP**, **BFI**, and a recursive filter on daily-value data. HYSEP and PART use a characteristic-response interval **N = A^0.2** (A = drainage area in mi², N in days); HYSEP separates with interval **2N\*** = the odd integer between 3 and 11 nearest 2N (Sloto & Crouse 1996, WRIR 96-4040, p. 5–6). For Crystal Lake, A = 2,131 ac = 3.33 mi² → N = 3.33^0.2 = **1.27 days**, 2N = 2.5 → **2N\* = 3 days**.

**Procedure:** run Eckhardt with a = your derived AGWRC; report annual BFI; repeat with Lyne–Hollick and one USGS method; the spread across methods is your uncertainty band. Then check that your *model's* long-term `Σ AGWO / Σ total outflow` lands in the same band — that is the calibration target for DEEPFR and the percolation rate.

### C.3 Sanity-check BFI against the literature

Use derived BFI to confirm the percolation/DEEPFR split sends a sensible share of yield through groundwater. Expectation for the Puget lowland (qualitative, see open question D.1): **outwash basins have the highest BFI** (permeable sediment sustains late-summer flow), **till basins lower** — consistent with Bauer & Mastin's (1997) finding that under forested till only **13–23% of annual precipitation is available for recharge** (the rest is ET), and Crystal Lake's documented interflow dominance. The Wolock grid at the Crystal Lake cell gives **BFI = 0.67** (D.1) — higher than a generic till expectation, indicating a strongly groundwater-supported basin and reinforcing a **low DEEPFR**. Confirm from your gauge; if the gauge disagrees, trust the gauge.

---

## (D) Open questions / data needed

1. **BFI from the Wolock grid — now resolved: BFI = 0.67.** The Crystal Lake cell of **Wolock (2003), USGS OFR 03-263, "Base-Flow Index Grid for the Conterminous United States"** (BFI point values via Wahl & Wahl's BFI program, interpolated to 1-km grid) reads **67 → BFI = 0.67** (looked up 2026-06-11). This is *higher* than the ~0.4–0.6 inferred for till, and is the calibration target: tune the percolation rate and DEEPFR so the model's long-term `Σ baseflow / Σ total outflow ≈ 0.67` (slow-interflow tail + active GW together count as baseflow). It also pins two filter/parameter choices: set **Eckhardt BFImax ≈ 0.80** (the ceiling must exceed observed 0.67) and keep **DEEPFR low (≈0.0–0.05)** — a deep sink of 0.11+ cannot coexist with two-thirds baseflow. **Caveat:** the grid is a coarse, interpolated 1-km cell; let a gauge-derived Eckhardt BFI (C.2) override it once enough clean recessions exist.

2. **Dinicola's *original* DEEPFR and AGWRC for Till.** The WWHM Appendix III-B table is the Dinicola-lineage set *as updated by Ecology/AQUA TERRA*, and it omits DEEPFR because WWHM deliberately routes no groundwater (Appendix III-B, §7: "Groundwater flow will not be computed … no groundwater flow from small catchments reaches the surface," per King County 1998). To get Dinicola's as-calibrated DEEPFR you need the original report's PERLND tables: **Dinicola, R.S., 1990, WRIR 89-4052** (and the follow-on **WRIR 02-4059**, 2002). Neither is available as machine-readable text online; the PDFs would need to be pulled and the calibration appendix read directly. **Action:** if DEEPFR matters to your water balance, obtain WRIR 89-4052 and confirm the calibrated Till value.

3. **Percolation formulation.** Your bucket currently sheds only by overflow + ET, so it has no steady percolation-to-GW term. You must add one (B.2 option b) for the GW reservoir to receive inflow between saturation events. The HSPF form needs LZS tracked through time; confirm your bucket exposes lower-zone storage, or approximate.

4. **Stage→discharge accuracy at low flow.** Deriving AGWRC needs reliable *low* outflows. Your spillway FTABLE is built for flood routing; confirm it is accurate near the stoplog/primary-spillway crest where recession flows sit, or the recession constant will be biased.

5. **KVARY confirmation.** Worth confirming empirically (C.1 step 5) before adopting 0.5 /in — if your wet/dry recession slopes are similar, keep KVARY = 0 and avoid the extra nonlinearity.

6. **Outwash sub-areas.** If any part of the 2,131 ac is mapped Everett/outwash (SCS A), split it to an Outwash Forest PERLND (INFILT 2.0, INTFW 0.0, IRC 0.7, KVARY 0.3, AGWRC 0.996) — it contributes almost entirely through groundwater, raising basin BFI.

---

## Citation note (authority ranking)

- **Highest authority for the Crystal Lake values:** Ecology WWHM **Appendix III-B** (Stormwater Management Manual for Western Washington) — it publishes the exact Till Forest numbers and names their lineage. Caveat: WWHM zeroes baseflow, so it is silent on DEEPFR and never exercises AGWRC/KVARY in practice.
- **Highest authority for ranges/definitions:** EPA **BASINS Technical Note 6** (2000) — parameter definitions and typical/possible ranges, with pages cited above.
- **Primary calibration source (lineage):** **Dinicola, R.S., 1990, WRIR 89-4052.** Your brief lists "90-4052"; the correct number is **89-4052** (confirmed in the Ecology reference list and the USGS catalog). The 2002 follow-on **WRIR 02-4059** is correctly numbered.
- **Methods:** Eckhardt (2005); Nathan & McMahon (1990); Vogel & Kroll (1996); Sloto & Crouse (1996, HYSEP); Barlow et al. (2015, USGS GW Toolbox); Wolock (2003, BFI grid).
- **Regional water balance:** Bauer & Mastin (1997, WRIR 96-4219).

### References
- Bauer, H.H., and Mastin, M.C., 1997, *Recharge from precipitation in three small glacial-till-mantled catchments in the Puget Sound Lowland, Washington*: USGS WRIR 96-4219, 119 p. https://doi.org/10.3133/wri964219
- Barlow, P.M., et al., 2015, *USGS Groundwater Toolbox … estimation of base flow, runoff, and groundwater recharge from streamflow data*: USGS Techniques and Methods 3-B10. https://pubs.usgs.gov/tm/03/b10/
- Dinicola, R.S., 1990, *Characterization and simulation of rainfall-runoff relations for headwater basins in western King and Snohomish Counties, Washington*: USGS WRIR **89-4052**.
- Dinicola, R.S., 2002, USGS WRIR 02-4059 (follow-on basins).
- Eckhardt, K., 2005, *How to construct recursive digital filters for baseflow separation*: Hydrological Processes 19:507–515.
- Nathan, R.J., and McMahon, T.A., 1990, *Evaluation of automated techniques for base flow and recession analyses*: Water Resources Research 26(7):1465–1473.
- Sloto, R.A., and Crouse, M.Y., 1996, *HYSEP: A computer program for streamflow hydrograph separation and analysis*: USGS WRIR 96-4040.
- U.S. EPA, 2000, *BASINS Technical Note 6 — Estimating Hydrology and Hydraulic Parameters for HSPF*: EPA-823-R00-012.
- Vogel, R.M., and Kroll, C.N., 1996, *Estimation of baseflow recession constants*: Water Resources Management 10:303–320.
- Wolock, D.M., 2003, *Base-flow index grid for the conterminous United States*: USGS OFR 03-263.
- WA Dept. of Ecology, *Stormwater Management Manual for Western Washington*, Appendix III-B (Western Washington Hydrology Model — Information, Assumptions, and Computation Steps).
