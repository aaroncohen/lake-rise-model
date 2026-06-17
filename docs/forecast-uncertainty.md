# Forecast uncertainty: how the low/median/high rainfall band is built

The model has no ensemble forecast to draw percentiles from (Home Assistant weather
integrations expose a single deterministic forecast, at most a PoP). So the low/median/high
rainfall band is **synthesized**. This note records the research the synthesis is grounded in
and the exact parameter choices, so the band is defensible rather than arbitrary.

> **Status: placeholder, research-grounded.** The values below encode the *shape* of QPF skill
> (multiplicative, asymmetric, decaying with lead, season-dependent). The exact numbers are
> order-of-magnitude until replaced by a site-specific fit from logged forecast-vs-gauge pairs
> (spec §3.5). See "Replacing this with data" below.

## What the literature says

**Skill decays monotonically with lead time.** NWS WPC's QPF verification scores 24-hour totals
as threat scores (fraction of the forecast precip area that verified). For the 1-inch threshold,
WPC typically attains ~0.28 at Day 2 (roughly 45% of the predicted 1-inch area correct, 36–60 h
out). All-time-best monthly scores bound it: ~0.557 Day 1, ~0.497 Day 2, ~0.445 Day 3; typical
months run well below. A 2025 global verification shows the precip field's overlap with reality
dropping from ~59% (1 day) to ~28% (9 days). Past ~7 days, amount forecasts have little skill —
which is why NWS issues week-2 outlooks only as above/near/below-normal terciles.

**The error is multiplicative, not additive.** Spread scales with the forecast amount, so a fixed
"±0.3 in" band is wrong; 24-h precip is commonly modeled log-normal / meta-Gaussian. Treat the
forecast as the **median** and apply a **ratio** (actual ÷ forecast).

**The error is asymmetric — fatter on the high side.** A separate occurrence/timing error layers
on top of amount error (threat scores never reach 1.0 even at Day 1), and **heavy events are
systematically under-forecast at longer leads.** For a dam EAP, the under-forecast upper tail is
the dangerous direction, so the band must be wider above the median than below.

**The PNW cool season is a relatively favorable case.** Wet-season flooding here comes from
large-scale frontal systems and atmospheric rivers, which are far more forecastable than summer
convection — so the national average (dragged down by summer thunderstorms) understates cool-season
skill. Asterisks that still bite: terrain creates large windward/lee gradients (a point sensor can
miss a skillful areal QPF), and **atmospheric-river intensity and exact landfall latitude still
shift substantially at 3–5 days** — which is exactly where the biggest lake-filling events live
(10–15 in over 48–72 h on the southern Cascades vs. 2–5 in in the lowlands is genuinely uncertain
several days out).

## What this model encodes (`artifacts/*.json` → `uncertainty`)

**`lead_ratio_by_day`** — the ~80% interval (10th/90th pct) of actual ÷ forecast, by lead day,
for a cool-season frontal regime:

| Lead | low × | high × | note |
|------|-------|--------|------|
| Day 1 | 0.6 | 1.6 | timing mostly right; amount is the main error |
| Day 2 | 0.5 | 1.8 | AR intensity/position uncertainty entering |
| Day 3 | 0.4 | 2.0 | |
| Day 4 | 0.3 | 2.5 | real chance of a timing miss |
| Day 5 | 0.25 | 3.0 | |
| Day 6 | 0.18 | 4.0 | approaching climatology |
| Day 7 | 0.12 | 5.0 | |
| > 7 d | 0.1 | 6.0 | treat as ~climatology |

`median[i] = forecast[i]`, `low[i] = forecast[i]·low×`, `high[i] = forecast[i]·high×`, where the
lead for hour `i` is `i` hours (`day = i//24 + 1`). High > median > low, and the gap above the
median is intentionally larger than below.

> **Known bias — the band is comonotonic.** The same lead-dependent ratio is applied to *every*
> hour at once, so the low/high branches move in perfect lockstep. Summing per-hour q10/q90 over a
> storm therefore yields the q10/q90 of the *total* **only if hourly errors are perfectly
> correlated**; with any independence the total's true 80% interval is narrower. So the synthesized
> low/high are an **upper bound on the dispersion** of the storm total/peak, and the q=0.10/0.90
> labels attached to the three peaks downstream (`predict._SCENARIO_QUANTILE`) are
> **conservative-wide**, with a bias that is not constant across storms. We can't estimate the real
> hourly-error correlation without data, so this is left as a documented bias to resolve with the
> logged forecast-vs-gauge fit below — which would replace the whole synthetic band anyway.

**`season_spread_factor`** — a per-month exponent on the ratios (log-space widening): ~1.0 in the
cool season (Nov–Mar frontal/AR baseline), rising to ~1.4 in Jul–Aug (convective, least skill).
`low^sf` / `high^sf` widens both sides while preserving the asymmetry.

**`skill_confidence_by_day`** — an approximate forecast-confidence % by lead day
(90/75/60/45/35/25/18, ~10 beyond a week), echoing the threat-score / overlap decay above. It is
reduced in summer by the season factor and surfaced in the UI/alerts as High/Medium/Low with the %
labelled as *QPF skill at this lead* — an **ordinal communication score, not a calibrated event
probability** (`confidence_pct = skill_by_day / season_factor` has no probabilistic meaning).
The lead it is evaluated at is the **risk-relevant** one — the earliest hour the median trajectory
reaches a threshold, else the heaviest-rain hour — so a storm whose danger lands days out reads
lower confidence (it is *not* pinned to day 1). A true probability also waits on the §3.5 fit.

**PoP** (when present) scales the **low** branch toward zero — the occurrence/timing part of the
error. **NOAA-alert QPF** (when present) can lift the **high** branch to a parsed heavy-tail total.

## Consequences carried elsewhere

- Warning logic leans to the **high** scenario, not the median, near a threshold (e.g. the
  3.30 ft mandatory-alert stick level) — the upper tail is the dangerous one. The primary warning
  trajectory already assumes no operator board changes (spec §4.6).
- Threshold-crossing risk ("Risk of early warning / overtopping") is a smooth
  `P(peak >= threshold)`: the low/median/high peak elevations are treated as the 10th/50th/90th
  percentiles of the peak (peak is monotonic in rainfall), and a CDF is fit through them with a
  **linear interior and log-linear (exponential-survival) tails** (`predict._exceedance_probability`).
  The dam-crest and bridge-deck thresholds almost always sit *above* the high-scenario peak, so they
  land in the upper tail: the exponential decay there is set by the q50→q90 spacing, so a wider band
  pushes the high peak out **and** fattens the tail, raising the risk of crossing a high threshold —
  and that risk **decays smoothly to zero, never clamping to a hard 0**, so the dangerous
  under-forecast tail is surfaced rather than hidden. (The earlier clamped *linear* extrapolation
  asserted `P=0` above a finite cutoff just past the high peak — the bug this replaced.) The quantile
  mapping is still only as good as the synthetic band (see the comonotonic-bias note above), so it
  tightens once the band is fit to data.

## Replacing this with data (highest-value next step)

1. **Use ensemble spread if the feed exposes it.** If a forecast source backed by GEFS/ECMWF-ENS
   becomes available, its day-to-day percentile spread is a *calibrated, weather-dependent*
   uncertainty — strictly better than this static table, because spread genuinely grows before
   uncertain events (e.g. an AR whose landfall is in doubt).
2. **Log forecast-vs-gauge pairs and fit your own.** A wet season of (forecast amount at each lead,
   actual measured rainfall) pairs lets you fit the multiplicative spread for *this* site and *this*
   feed (Apple WeatherKit), capturing local terrain bias and feed quirks. This is the same
   regime-ordered calibration philosophy already used for soil saturation, and it's what spec §3.5
   ("forecast-vs-gauge pairs → scenario-band widths") calls for.
