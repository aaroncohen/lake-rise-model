# S3 — Details: Implementation Plan

Design: docs/design.md (slice S3; delivers F9; depends on D7, D8, D9, D37, D39)
Status: approved (2026-11-15, self-approved under the user's overnight authorisation)
Reviewed: Claude Fable, 2026-11-15; 11 items folded in

## Goal
From the field view, "Details" opens a phone screen of collapsible sections:
- **What's raising the lake:** signed bars, in feet, for surface runoff, rain on the lake, groundwater seepage, evaporation and out the spillways, middle case, up to its peak.
- **Rain by day:** rain per day for each case.
- **The three cases:** dry, middle and wet, with their peak and when each overtops or covers the bridge.
- **Data and model:** gauge age, rain gauge, stop logs, rain forecast, agreement, soil.
- **Tools:** links to the desktop dashboard's what-if and backtest.

Demonstrated by Playwright at 390 px: Details opens from the footer, every section expands, Back returns, and the tool links point at `dashboard?mode=manual` and `dashboard?mode=backtest`.

## Decisions made in planning
| ID | Decision | Why |
|----|----------|-----|
| R1 | Details is a second view inside `field.html`, drawn from the same `/field` response, which gains `details`. No new endpoint and no second fetch. | Architecture; one request, one cache. |
| R2 | Factor bars are cumulative up to the median trajectory's peak hour `i_peak` (argmax of the "median" scenario's elevations): `factors["cumulative_ft"][k][i_peak]`, and `net_ft = factors["net_cumulative_ft"][i_peak]` (= peak − now). If the median never rises above now (`i_peak` is hour 0 or the peak equals now), they are the 3-day totals `factors["totals_ft"][k]` and `factors_window = "3 days"`, otherwise `"to peak"`. Labels: `watershed_runoff` "Surface runoff", `direct_rain` "Rain on the lake", `baseflow` "Groundwater seepage", `evaporation` "Evaporation", `spillway` "Out the spillways". Factor values are deltas in ft: never passed through `stick()`. | Matches the mockup's "2.20 → 2.92 ft, +0.72" row, whose bars sum to it (Fable review). |
| R3 | Rain by day comes in 24 h blocks from the forecast start, for the low/median/high scenarios (`scenario_rain`). The page labels each column with the Pacific weekday the block ends on, and captions the table "in per day ({h}–{h})", with `{h}` the start's Pacific hour ("5 pm–5 pm"). Labels are computed in the page's JS. | The mockup's rule (Fable review). |
| R4 | Cases: the peak comes from the top-level `FieldSummary.cases`; overtop and bridge times from the scenario's `hours_to_crest` / `hours_to_bridge_deck` added to the start. A value of 0 (already over) is encoded as `reached=True` with time None, and the page shows "now". Never reached: "no". | `_hours_to_crest` returns 0.0 when already over (Fable review). |
| R5 | Data rows (short labels): "Lake gauge: {age} min ago", or "{problem}" when `lake.problem` is set or `gauge_age_min` is None; "Rain gauge: {x} in, last 24 h", from `sum(bundle.trailing_rainfall_in[-24:])`; "Stop logs: {n}" + " (default)"; "Rain forecast: ECMWF {Tue 5a} run", or "Apple only" in fallback; "Agreement: {Label}, {pct}% within {tol} ft", with the label from `scenarios.confidence_label` and `tol` from `art.ensemble_rain.agreement_tolerance_ft`; "Soil: {pct}% full" = `min(100, round(100 × state_sm_in / hspf.LZSN_in))`. | D37 and the mockup's rows. |
| R6 | Gauge age: new `LiveHASource.gauge_age_minutes(now)`, the liveness sensor's report age. It is None on `httpx.HTTPError`, or when the state has no usable timestamp (`_state_age_hours` ≥ 1e8). | One state call. |
| R7 | In `/field`'s stale paths: No-forecast merge: `details` is merged field by field. `factors`, `net_ft`, `factors_window`, `rain_days`, `cases`, `rain_source`, `agreement_pct` and `ensemble_run` come from last-good; `gauge_age_min`, `rain_gauge_24h_in`, `stop_log_count`, `stop_log_default` and `soil_pct` come from the new build. HA-down (last-good served whole): `details.gauge_age_min` is set to None, so the page shows the problem/stale text, not an old age. | Fable review. |
| R8 | Times on the page use S1's short form ("Fri 2a", "Tue 5p") everywhere. Accordion panels are independent. The first rendered section starts open (Data and model in `no_forecast`). | One format per page. |

## File structure
```
src/lake_rise/
  field.py               modify: FieldDetails models; summarize(..., gauge_age_min=None) fills details
  sources/live_ha.py     modify: gauge_age_minutes()
  api.py                 modify: /field passes gauge_age_min; R7 merge rules
  static/field.html      modify: Details view
tests/
  test_field.py, test_live_ha.py, test_api.py, test_field_page.py
```

## Interfaces
```python
class FieldFactor(BaseModel):
    key: str        # watershed_runoff | direct_rain | baseflow | evaporation | spillway
    label: str      # R2
    ft: float       # signed delta, 2 dp (not stick height)

class FieldRainDay(BaseModel):
    start: datetime
    dry_in: float
    middle_in: float
    wet_in: float

class FieldCaseTimes(BaseModel):
    name: Literal["dry", "middle", "wet"]
    overtop_at: datetime | None
    overtop_reached: bool          # already over at the start
    bridge_at: datetime | None
    bridge_reached: bool

class FieldDetails(BaseModel):
    factors: list[FieldFactor]       # [] when mode == "no_forecast"
    net_ft: float | None             # None when no_forecast
    factors_window: Literal["to peak", "3 days"] | None
    rain_days: list[FieldRainDay]    # [] when no_forecast
    cases: list[FieldCaseTimes]      # [] when no_forecast
    gauge_age_min: int | None
    rain_gauge_24h_in: float
    stop_log_count: int
    stop_log_default: bool
    rain_source: Literal["ensemble", "point_forecast"] | None   # None iff no_forecast
    agreement_pct: int | None
    agreement_label: str | None      # "High" | "Medium" | "Low"
    agreement_tol_ft: float | None
    soil_pct: int                    # 0-100, clamped

# FieldSummary gains: details: FieldDetails | None = None — summarize always sets it.
# summarize(..., gauge_age_min: int | None = None)
```
`LiveHASource.gauge_age_minutes(now: datetime) -> int | None` (T2).

## Not building
- The watershed scene (S4).
- Hour-by-hour flux charts, soil and groundwater store charts on the phone; the desktop dashboard keeps them.
- Changes to the desktop dashboard.

## Global constraints
The S1 plan's Global constraints apply unchanged, except that `FieldFactor.ft` values are deltas (R2), not stick heights. Copy is short labels (D7); ≥ 44 px targets; contrast in both schemes.

## Tickets

<!-- Issues: T1 #29 · T2 #30 · T3 #31 -->

### T1 — Details in the field summary
Depends on: S2 T4 (#24) merged (both change `summarize` and `FieldSummary`)
**Context.** The pure assembly of F9's data (R2–R5), added to `FieldSummary.details`.
**Files.** Modify `src/lake_rise/field.py`, `tests/test_field.py`.
**Work.**
1. Add the models per Interfaces.
2. In `summarize`, build `details` per R2–R5.
3. Gate the forecast parts on `mode == "no_forecast"`, not on `result.factors`, which can be a zero-filled dict.
**Tests.**
- `details factors map to the five labels`;
- `factors to the peak sum to the peak rise` (sum within `abs=0.03` of `net_ft`; `net_ft == round(cases.middle − lake.now_ft, 2)` within 0.01);
- `spillway and evaporation are negative`;
- `factors fall back to 3-day totals when the lake doesn't rise`;
- `rain days sum to the scenario totals`;
- `cases carry overtop and bridge times when reached`;
- `already over the crest is reached, not a time`;
- `no forecast keeps the data rows`;
- `soil percent uses LZSN and is capped at 100`.
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q tests/test_field.py` pass

### T2 — Gauge age and stale-path wiring
Depends on: T1; S2 T5 (#25) merged (both edit `/field` and its test fake)
**Context.** The data section's gauge age (R6), passing it through `/field`, and keeping `details` honest in the stale paths (R7).
**Files.** Modify `src/lake_rise/sources/live_ha.py`, `src/lake_rise/api.py`, `tests/test_live_ha.py`, `tests/test_api.py`.
**Work.**
1. `gauge_age_minutes(now)` per R6.
2. `/field` passes `gauge_age_min=src.gauge_age_minutes(now)`; the test fakes gain the method (returning 2).
3. Apply R7 in the no-forecast merge and the HA-down path.
**Tests.**
- `gauge age in minutes from the liveness sensor`;
- `gauge age None when HA fails or there is no timestamp`;
- `field details carry the gauge age`;
- `HA down clears the gauge age in details`;
- `no-forecast merge keeps live data rows current`.
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q` pass

### T3 — The details view
Depends on: T2; S2 T6 (#26) merged (both edit `field.html` and `tests/test_field_page.py`)
**Context.** Draws F9 in `field.html` as a second view, matching the approved mockup's "All details" board.
**Files.** Modify `src/lake_rise/static/field.html`, `tests/test_field_page.py`.
**Work.**
1. The footer "Details" becomes a button [`details`] that shows the details view [`details-view`] and hides the summary. The view has a 44 px "Back" [`back`] and the title "All details · {Updated h:mm}".
2. Sections, each with a 56 px `<button aria-expanded>` header (R8):
    1. **"What's raising the lake"** [`sec-factors`]: one signed bar per factor, right for +, left for −, centre axis, with its label and value. Below them a net row [`net`]: "Net {now}→{middle} ft {+x}" when `factors_window == "to peak"`, else "Net over 3 days {+x}".
    2. **"Rain by day"** [`sec-rain`]: a table per R3.
    3. **"Dry, middle and wet"** [`sec-cases`]: a table with Peak (from the top-level `cases`), Overtops and Bridge, showing "now", a time like "Fri 2a", or "no".
    4. **"Data and model"** [`sec-data`]: the R5 rows.
3. Tools: two 52 px links, "What-if storm" → `dashboard?mode=manual` and "Backtest: model vs gauge" → `dashboard?mode=backtest`.
4. Sections with no data (no forecast) are omitted, except Data and model.
**Tests.** `tests/test_field_page.py`:
- `details opens and closes`;
- `every section expands`;
- `factor bars show five rows and a net`;
- `rain columns are labelled by the day each block ends` (start Tue 5p Pacific → first column "Wed", caption "5 pm–5 pm");
- `an overtop past midnight Pacific shows the right weekday` (09:00Z → "1a" on the Pacific date);
- `tools link to the dashboard modes`;
- `no forecast shows only data and model`;
- on the details view: no horizontal scroll, no absolute elevations, and contrast ≥ 4.5 for `details-view` title text and the `sec-data` rows in light and dark.
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q tests/test_field_page.py` passes with chromium installed

## Review focus
- **Factor signs and the peak window:** T1 tests both. The page relies on the signs for bar direction.
- **Stale data rows:** R7 is tested in T2.

## Verification
(Added by dev-test when the slice is done.)
