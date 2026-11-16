# S1 — Field view, end to end: Implementation Plan

Design: docs/design.md (slice S1; delivers F1, F2, F3, F4, F8; depends on D5, D6, D7–D13, D16, D19–D32)
Status: implemented (2026-11-15)
Reviewed: codex gpt-5.6-sol, Claude Fable, 2026-11-14; 20 items folded in

## Goal
Operators opening the alert link on a phone land on the field view. It shows the status
(observed EAP level and/or the forecast ladder level), the lake on a drawn stick with the tally
of the 50 forecast peaks, the next 3 days with shading, and the rain strip, all in stick
height. Chances come from member counts everywhere and read as percentages, page and emails alike, built by one shared
live-forecast builder.

Demonstrated by:
- Playwright at 390 px over six states (normal, WATCH, fallback, stale gauge, Home Assistant down with a previous result, Home Assistant down with none);
- the alert-level replay over the archived runs;
- the user checking it on a real phone before the NAS deploy.

## Decisions made in planning
| ID | Decision | Why |
|----|----------|-----|
| P2 | Any alert-ladder rank ≥ 1 (ADVISORY up) shows the status bar. Rank 0 with no observed EAP level shows the "Normal" tag. | The ladder is the emails' rule; ADVISORY already sends an ops email. |
| P3 | The status headline is the percentage for the active level's threshold: early_warning → "Warning 70%", dam_crest → "Overtop 22%", bridge_deck → "Bridge 12%", in both modes. | It names the number that put the ladder at that level. |
| P4 | Trend: the 30-minute median now against the 30-minute median ending one hour ago, from one HA history fetch over the last 90 min. Under 0.03 ft/h in magnitude reads "steady". | D26. One fetch; reuses the live anchor's own smoothing (`backtest.smoothed_anchor_elev`). |
| P5 | The cache and last-good result live on `app.state`, keyed by the artifact version: 120 s TTL, a lock so simultaneous misses build once, errors never cached. | D17, D24. Per app instance, so a calibration version change or a test app never gets another's result. |
| P6 | Playwright tests build responses with `field.summarize` on synthetic inputs, served by route interception. No JSON fixture files. | Fixtures can't drift from the schema. |
| P7 | Page and emails show percentages, never "N of 50" (D39, the user: counts confuse readers who don't know about ensembles). The emails need no template change: their percentages already come from `predict`, which now counts. | D39. |
| P8 | Percentages come from one place. For the model thresholds, `pct = round(100 × p_cross)` from the prediction (absolute frame, the same number the emails use). For Plastic, `pct = round(100 × members with absolute peak ≥ 3.30 + offset / n)`. Stick values are rounded only for display, half-up to 2 places (2.625 → 2.63). | Rounded stick comparisons could make the page and the email disagree at a boundary (Fable review). |
| P9 | `/live/predict` without a what-if storm and with no offset takes its bundle from the shared builder, applying a stop-log override by copy. Its display blocks still come from `fetch_conditions`. What-if and offset requests keep today's path. | D19 with what-if overrides intact; two HA pulls on the desktop dashboard are acceptable. |

## File structure
```
src/lake_rise/
  predict.py               modify: counts as chances with an ensemble; ensemble_peaks_ft
  antecedent.py            modify: archive_spinup() moved here from LiveHASource (shared with replay)
  field.py                 create: pure assembly of the field-view summary (models + summarize)
  api.py                   modify: GET /field; routes /, /dashboard; /live/predict on the shared builder
  cli.py                   modify: `lake-rise alerts-replay`
  sources/live_ha.py       modify: lake_trend_ft_per_h(); archive_spinup delegates to antecedent
  alerting/service.py      modify: live_source() shared by the alert job, /field and /live/predict
  alerting/rules.py        modify: AlertDecision.active_threshold_label
  alerting/render.py       modify: link drops ?mode=live
  static/field.html        create: the phone page
tests/
  test_ensemble_rain.py    modify: counting contract
  test_field.py            create: summarize() contracts
  test_api.py              modify: /field, cache, failures, routes, /live/predict shared builder
  test_live_ha.py          modify: lake trend
  test_alert_render.py     modify: link test
  test_field_page.py       create: Playwright, 390 px, six states
docs/calibration-log.md    modify: entry for counting (T1) and the replay result (T8)
pyproject.toml             modify: playwright in the dev extra (T6)
README.md                  modify: routes paragraph (T5), browser-test line (T6), alerts-replay (T8)
```

## Interfaces

**predict.PredictionResult** (T1) gains `ensemble_peaks_ft: list[float] | None = None`: the members' 3-day peaks, absolute ft, sorted ascending, `None` without an ensemble. With an ensemble, each threshold probability is, in order:
- 1.0 if `current_elevation_abs_ft >= threshold`;
- otherwise `round(sum(p >= threshold for p in ensemble_peaks_ft) / len(ensemble_peaks_ft), 3)`.
This covers every `p_cross_*` and every `threshold_probabilities[*].p_cross_within_horizon`.

**alerting.rules.AlertDecision** (T1) gains `active_threshold_label: str | None = None`: the `threshold_label` of the ladder level `evaluate` picked, None at rank 0.

**antecedent.archive_spinup(art: Artifact, as_of: datetime, recent_rain: list[float]) -> SpinupState | None** (T2): the body of today's `LiveHASource.archive_spinup`, unchanged in behaviour. `LiveHASource.archive_spinup` calls it.

**alerting.service.live_source(art: Artifact, config: AlertConfig) -> LiveHASource** (T2): raises `RuntimeError("No live HA source configured (set HA_URL and HA_TOKEN).")` when unconfigured. `build_live_bundle(art, config)` becomes `live_source(art, config).build_bundle()`.

**LiveHASource.lake_trend_ft_per_h(now: datetime) -> float | None** (T2): ft per hour, or `None` when either window has no samples or the history fetch fails.

**field.py** (T3):
```python
class FieldLevel(BaseModel):
    key: Literal["early_warning", "eap_plastic", "overtop", "bridge"]
    label: str                       # "Warning" | "Plastic" | "Overtop" | "Bridge"
    stick_ft: float                  # display value, half-up 2 dp (2.63, 3.30, 3.83?, see Levels)
    band_top_ft: float | None        # 3.90 / 4.40 for the overtopping bands, else None
    pct: int | None                  # chance of reaching it, 0-100 (None: no forecast, or Plastic in fallback)
    reached: bool                    # the lake is already at or above it (absolute frame)

class FieldStatus(BaseModel):
    level: str | None                # ladder name ("WATCH") or None at rank 0
    rank: int                        # ladder rank, 0 = normal
    headline: str | None             # P3
    observed_eap: dict | None        # {"title": str, "gauge_ft": float, "color": str} (D20)

class FieldLake(BaseModel):
    now_ft: float                    # result.current_elevation - offset (the forecast's anchor)
    trend_ft_per_h: float | None
    trend: Literal["rising", "falling", "steady"] | None
    problem: str | None              # LiveHASource.gauge_problem text when stale/invalid

class FieldTimeline(BaseModel):
    start: datetime                  # bundle.as_of; middle_ft[i] is at start + (i+1) h
    middle_ft: list[float]           # hourly, len == horizon_hours
    bands: dict[str, list[float]]    # ensemble: "10","25","75","90"; fallback: "low","high"

class FieldSummary(BaseModel):
    computed_at: datetime            # request time, UTC, whole seconds
    mode: Literal["ensemble", "fallback", "no_forecast"]
    status: FieldStatus
    lake: FieldLake
    levels: list[FieldLevel]         # always 4, ordered low to high
    peaks_ft: list[float]            # display values, sorted; empty unless ensemble
    peak_levels: list[str | None]    # parallel to peaks_ft: highest level key reached (absolute frame)
    cases: dict[str, float] | None   # {"dry","middle","wet"} = low/median/high peaks; None if no_forecast
    timeline: FieldTimeline | None
    rain_6h: dict[str, list[float]] | None   # {"middle","wet"} = median/high rain, in per 6 h from start
    ensemble_cycle: datetime | None  # bundle.ensemble_cycle
    stale_since: datetime | None = None      # set by /field when the forecast part is a previous result
    unavailable: str | None = None           # banner text in that case

def summarize(result: PredictionResult, bundle: InputBundle, decision: AlertDecision,
              art: Artifact, *, observation: GaugeObservation | None,
              trend_ft_per_h: float | None, gauge_problem: str | None,
              computed_at: datetime) -> FieldSummary
```
Every value whose key ends in `_ft` is in stick height: absolute − `art.datum.sensor_to_absolute_offset_ft` (D32). Display rounding is `Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)`.

**Routes** (T4, T5):
- `GET /field` returns the `FieldSummary` JSON. 503 `{"detail": "No live HA source configured"}` when HA is unconfigured; 502 `{"detail": "Can't reach the gauge"}` when the build fails with no previous result.
- `GET /` serves `static/field.html`. With `?mode=manual` or `?mode=backtest` it answers 307 with `Location: dashboard?mode=<mode>` (relative, so the `/lake-rise` proxy prefix survives). `GET /?mode=live` serves the field view.
- `GET /dashboard` serves `static/index.html`.

**CLI** (T8): `lake-rise alerts-replay [--since YYYY-MM-DD]` prints one line per archived run where the old and new ladder levels differ, then a total line `N runs (M with lake assumed at control), K differ, L lower` (ruling on #14: runs whose hour has no lake reading use the spill crest for the date's default stop logs).

## Hard parts

**Levels and counts (T3, P8).** The levels come from the artifact and `alerting/eap.py`; none is hard-coded.

| key | label | counted at (absolute) | stick_ft shown | band_top_ft |
|---|---|---|---|---|
| early_warning | Warning | `thresholds_abs_ft.early_warning` | 2.63 | None |
| eap_plastic | Plastic | `eap_level(1).gauge_ft + off` | 3.30 | None |
| overtop | Overtop | `thresholds_abs_ft.dam_crest` | 3.83 | `eap_level(2).gauge_ft` (3.90) |
| bridge | Bridge | `thresholds_abs_ft.bridge_deck` | 4.33 | `eap_level(3).gauge_ft` (4.40) |

The shown values are 3.825 and 4.325 rounded half-up, so 3.83 and 4.33. The design's "3.82 / 4.32" were truncations; the page shows 3.83–3.90 and 4.33–4.40. This is recorded in the calibration-log entry in T1.

Counts:
- early_warning / overtop / bridge, both modes: `pct = round(100 × p)`, where `p` is the label's `p_cross_within_horizon` (a member count fraction with an ensemble, D12).
- Plastic, ensemble: `pct = round(100 × sum(peak >= eap_level(1).gauge_ft + off for peak in result.ensemble_peaks_ft) / len(result.ensemble_peaks_ft))`; fallback: None.
- `peak_levels[i]`: the key of the highest level whose absolute threshold `peaks[i]` reaches, else None.

**Status (T3), D20 then P2.**
1. If `observation` is confirmed and `eap.active_eap_rank(observation.gauge_ft) > 0`, then `observed_eap` is `{title, gauge_ft (display-rounded), color}` of that level.
2. `level` and `rank` come from `decision.active_level_name` and `decision.active_rank`; `headline` follows P3 from `decision.active_threshold_label`.
3. Both can be present; the page draws `observed_eap` first.

**Mode (T3).**
- `"ensemble"` when `result.ensemble_peaks_ft`.
- `"fallback"` when any `bundle.forecast_scenarios[*].hourly_in` is non-empty.
- Otherwise `"no_forecast"`: `cases`, `timeline`, `rain_6h` and `ensemble_cycle` are None, `peaks_ft` is empty, and every level's `pct` is None.
- A bundle with ensemble members and no Apple forecast is `"ensemble"`. `scenarios.ensemble_members` already spreads the rain evenly.

**Rain per 6 h (T3).** `predict.scenario_rain(bundle, result)`: middle = "median", wet = "high". Sum the hourly values in consecutive 6-hour blocks from the start; a final short block is summed as is. Round to 2 places.

**Trend (T2).**
```python
hist = self._get_history(self.cfg.lake_sensor, now - timedelta(minutes=90), now)
a = backtest.smoothed_anchor_elev(hist, 0.0, now, window_hours=LIVE_ANCHOR_WINDOW_HOURS)
b = backtest.smoothed_anchor_elev(hist, 0.0, now - timedelta(hours=1), window_hours=LIVE_ANCHOR_WINDOW_HOURS)
return None if a is None or b is None else round(a - b, 3)
```
`field.summarize` maps it: `abs(t) < 0.03` → "steady", `t > 0` → "rising", else "falling".

**`/field` request (T4).**
```text
now = datetime.now(timezone.utc).replace(microsecond=0)
if ha_config_from_env() is None: 503                        # before any try
key = art.version (+ the active calibration version when one is set)
with app.state.field_lock:
    c = app.state.field_cache                               # {"key", "at", "value"} or None
    if c and c["key"] == key and _clock() - c["at"] < 120: return c["value"]
    try:
        src = live_source(art, cfg); bundle = src.build_bundle(); result = predict(bundle, art)
        decision = evaluate(result, bundle, art, cfg)
        summary = summarize(result, bundle, decision, art,
                            observation=src.fetch_gauge_observation(now),
                            trend_ft_per_h=src.lake_trend_ft_per_h(now),
                            gauge_problem=src.gauge_problem(now), computed_at=now)
    except (httpx.HTTPError, RuntimeError, KeyError, ValueError):
        last = app.state.field_last_good (same key) or 502
        return last with stale_since=last.computed_at, unavailable="Can't reach the gauge"   # not cached
    if summary.mode == "no_forecast" and last_good (same key) has a forecast:
        summary = last_good's forecast parts (mode, levels, peaks_ft, peak_levels, cases, timeline,
                  rain_6h, ensemble_cycle) + this summary's status, lake and computed_at,
                  with stale_since=last_good.computed_at, unavailable="No new forecast"
    else if summary.mode != "no_forecast": app.state.field_last_good = summary
    app.state.field_cache = {"key": key, "at": _clock(), "value": summary}; return summary
```
`_clock` is a module attribute (`_clock = time.monotonic`) so tests can advance it. `api.py` imports `live_source` as a name in its own module (`from .alerting.service import live_source`); tests patch `lake_rise.api.live_source`.

**Replay (T8).** For each archived `ecmwf_ens` run at 12Z since `--since` (default 2026-02-21):
1. `as_of = cycle + 12 h`.
2. The lake comes from the archive hour `as_of` (`elev_ft`); skip the run if it is missing.
3. Stop logs are `default_stop_log_count(art.stop_logs, as_of.month, as_of.day)`.
4. Spin-up is `antecedent.archive_spinup(art, as_of, [])`; skip the run if it returns None.
5. `members = scenarios.ensemble_members(art, run, as_of, 72)`.
6. The bundle has those members, `ensemble_cycle`, `spinup_state` and `current_elevation_abs_ft`.
7. `result = predict(bundle, art)` gives the new level via `rules.evaluate` with the default `AlertConfig` levels.
8. The old level: recompute each threshold's probability with `predict._exceedance_probability([(predict._quantile(peaks, q), q) for q in predict._ENSEMBLE_QUANTILES], threshold)` over `result.ensemble_peaks_ft`, then apply the same ladder rule. The "already over → 1.0" rule applies to both.

## Not building
- What changed, actions, stop-log options (S2); the details screen (S3); the watershed scene (S4).
- Hardening the dashboard's public POST endpoints (D31).
- Push notifications, logins, per-phone settings, a dark/light toggle (the page follows `prefers-color-scheme`, D16).
- "N of 50" wording anywhere (P7, D39).

## Global constraints
- Stick height only in anything an operator sees; no absolute elevation in `FieldSummary` or on the page.
- `model.py` stays untouched; parameters only in the artifact (CLAUDE.md).
- Anchors must pass: `.venv/bin/lake-rise validate` (Step 6 peak 343.1 ± 0.5, now 342.72; dry-equilibrium 339.6–339.8, now 339.661). `.venv/bin/pytest -q` stays green.
- Page copy is short labels, no sentences (D7). Anything a picture can carry is drawn (D9). Sections show only when they apply (D8).
- Touch targets ≥ 44 px; text contrast ≥ 4.5:1 in light and dark; colour never the only signal (every zone colour has its label).
- Commit messages end with the session's Co-Authored-By attribution line. Never write "Stage N" in code or commits.
- New dependencies: the newest release at least 7 days old, broadly used, with version and release date recorded in the commit message.

## Tickets

<!-- Issues: T1 #7 · T2 #8 · T3 #9 · T4 #10 · T5 #11 · T6 #12 · T7 #13 · T8 #14 -->

### T1 — Counts are the chances
Depends on: none
**Context.** With an ensemble, the chance of reaching a threshold becomes the share of the members whose 3-day peak reaches it, for every consumer of `predict`: alerts, dashboard, log (F8, D12, D22). The page and emails show it as a percentage (P7). The fallback band keeps its interpolated percentages.
**Files.** Modify `src/lake_rise/predict.py`, `src/lake_rise/alerting/rules.py`, `docs/calibration-log.md`, `tests/test_ensemble_rain.py`, `tests/test_alert_rules.py`.
**Interfaces.** Produces `PredictionResult.ensemble_peaks_ft`, the counting rule and `AlertDecision.active_threshold_label` (see Interfaces).
**Work.**
1. In `predict`'s ensemble branch, keep the sorted member peaks and compute each threshold probability by the counting rule. The non-ensemble branch is unchanged.
2. Set `ensemble_peaks_ft`.
3. `rules.evaluate` sets `active_threshold_label`.
4. Add a 2026-11-14 entry to `docs/calibration-log.md`:
    - counting replaces the smoothed CDF with an ensemble, and why;
    - the trade-off: a level above every member reads 0 %;
    - the shown stick values 3.83 / 4.33.
    Add a parameter-table note too.
**Tests.**
- `test_ensemble_rain.py`:
    - `chance is the share of member peaks that reach a threshold`: 11 of 50 members peak above the crest gives `p_cross_crest == 0.22`;
    - `already over a threshold reads 1.0`;
    - `ensemble_peaks_ft is sorted with one entry per member`;
    - the existing fallback-band tests pass unmodified.
- `test_alert_rules.py`: `active threshold label names the level's threshold` (WATCH → "dam_crest"; normal → None).
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q` all pass
- [ ] `.venv/bin/lake-rise validate` shows both anchors PASS at 342.72 and 339.661

### T2 — Shared live source, archive spin-up, lake trend
Depends on: none
**Context.** `/field`, the alert job and `/live/predict` must build their forecast the same way (D19), and the replay (T8) needs the archive spin-up without a live source. The page needs the lake's one-hour trend (D26, P4).
**Files.** Modify `src/lake_rise/alerting/service.py`, `src/lake_rise/antecedent.py`, `src/lake_rise/sources/live_ha.py`, `tests/test_live_ha.py`.
**Interfaces.** Produces `live_source()`, `antecedent.archive_spinup()` and `LiveHASource.lake_trend_ft_per_h()` (see Interfaces).
**Work.**
1. Add `live_source(art, config)`, the first three lines of today's `build_live_bundle`, returning the source; `build_live_bundle` calls it.
2. Move `LiveHASource.archive_spinup`'s body to `antecedent.archive_spinup(art, as_of, recent_rain)`; the method calls it with `self.art`.
3. Add `lake_trend_ft_per_h` per Hard parts; on `httpx.HTTPError` return None.
**Tests.** `test_live_ha.py`, with mocked HA history serving rows in both windows (the file's handler only covers the last hour today; extend it for this test):
- `lake trend compares the last half hour with an hour earlier`: 1.00 ft in [now−90, now−60] min and 1.10 ft in [now−30, now] gives 0.100 ± 0.001;
- `lake trend is None without older samples`;
- `lake trend is None when history fails` (HTTP 500).
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q` all pass, with `tests/test_alert_*.py` and `tests/test_antecedent.py` unmodified

### T3 — Field summary assembly
Depends on: T1
**Context.** A pure function turns a prediction, its bundle, the alert decision and the gauge facts into the page's JSON. It covers status (D20, P2, P3), the lake and its trend, the four levels with counts or percentages (D5, D6, D21, D23, P8), peaks and their levels, cases, timeline and bands (D27), rain per 6 h (D10), and the mode, all in stick height (D32). F2, F3, F4.
**Files.** Create `src/lake_rise/field.py`, `tests/test_field.py`.
**Interfaces.** Consumes `PredictionResult.ensemble_peaks_ft`, `AlertDecision.active_threshold_label`, `predict.scenario_rain`, and `eap.eap_level` / `active_eap_rank`. Produces the `field.py` models and `summarize` (see Interfaces).
**Work.**
1. Define the models exactly as in Interfaces.
2. Implement `summarize` per Hard parts (levels and counts, status, mode, rain, trend label).
3. Timeline:
    - `middle_ft` is the "median" scenario's trajectory;
    - ensemble bands are `result.elevation_bands_ft` keys 10/25/75/90;
    - fallback bands are the "low" and "high" trajectories.
4. `cases`: dry/middle/wet = the low/median/high scenarios' `peak_elevation`.
5. `levels[*].reached` is `result.current_elevation >= the level's absolute threshold`.
**Tests.** `test_field.py`, using the 50-member bundle helpers from `tests/test_ensemble_rain.py` (import `_bundle`, `_storm`):
- `percentages are cumulative and match the alert probabilities`: each model level's `pct` equals `round(100 × p_cross)`, and Overtop ≥ Bridge;
- `a member peaking exactly at the crest counts as Overtop`;
- `plastic percentage uses 3.30 ft on the stick`;
- `peak_levels names the highest level each peak reaches`;
- `fallback gives percentages and no peaks`;
- `no forecast gives no timeline and no percentages`;
- `ensemble without an Apple forecast is ensemble mode`;
- `observed EAP level leads at 3.95 ft` (D20);
- `normal gives level None and rank 0`;
- `headline follows the active level's threshold` (P3): WATCH gives "Overtop 22%" in both modes;
- `every _ft value is absolute minus the offset`: for a known bundle, `lake.now_ft == round_half_up(current − 338.375)` and every `*_ft` field is ≤ 10;
- `early warning shows 2.63`;
- `rain blocks sum to the scenario total`.
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q tests/test_field.py` pass

### T4 — GET /field
Depends on: T2, T3
**Context.** The endpoint the page calls. It builds live through the shared source, then predicts, evaluates, observes and summarizes. A 120 s per-app cache keyed by artifact version; a last-good result for Home Assistant failures and for an empty forecast; errors never cached (D17, D24, D25, P5). Public, no token (D13).
**Files.** Modify `src/lake_rise/api.py`, `tests/test_api.py`.
**Interfaces.** Consumes `live_source`, `lake_trend_ft_per_h`, `field.summarize`, `rules.evaluate`, `alert_config_from_env`. Produces `GET /field` (see Interfaces).
**Work.**
1. Implement per Hard parts "`/field` request".
2. Initialise `app.state.field_cache = None`, `app.state.field_last_good = None` and `app.state.field_lock = threading.Lock()` in `create_app`.
**Tests.** `test_api.py`. Patch `lake_rise.api.live_source` with a fake whose `build_bundle` returns `test_ensemble_rain._bundle(...)` (or raises), with `fetch_gauge_observation`, `lake_trend_ft_per_h` and `gauge_problem` returning fixed values. Patch `lake_rise.api._clock`.
- `field returns an ensemble summary without a token`;
- `field reuses its result within 120 seconds` (the fake's build count stays 1);
- `field rebuilds after 120 seconds` (advance `_clock` by 121);
- `a failure is not cached`: fail, then succeed, and the second call builds;
- `failure after a success serves the previous result marked stale` (`stale_since` = previous `computed_at`; `unavailable` = "Can't reach the gauge");
- `failure with no previous result is 502`;
- `HA unconfigured is 503`;
- `an empty forecast keeps the previous forecast marked stale with the new lake` (`unavailable` = "No new forecast");
- `a new artifact version doesn't reuse the cache`.
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q tests/test_api.py` pass

### T5 — The field page and routes
Depends on: T4
**Context.** The phone page (F1–F4) drawn from `/field`. It becomes the landing page, the dashboard moves (D31), and the emails' link drops `?mode=live`. The look follows the approved mockup (https://claude.ai/artifact/GdkBWxNAcic8QDPgpKBFxt, boards "Example storm", "Today" and "A: dot tally"); the values below are binding.
**Files.** Create `src/lake_rise/static/field.html`; modify `src/lake_rise/api.py` (routes), `src/lake_rise/alerting/render.py` (link), `README.md` (routes paragraph).
**Interfaces.** Consumes `GET /field` (`FieldSummary`). Produces routes `/`, `/dashboard` and the redirects (see Interfaces).
**Work.**
1. `field.html`: one self-contained page with inline CSS and JS, no libraries, `fetch('field')` relative, `<title>Crystal Lake</title>`. Every drawn part carries a `data-testid` (named in brackets below).
    1. **Header** [`header`]: "Crystal Lake", "Updated h:mm am/pm" from `computed_at` in America/Los_Angeles, and a 44 px refresh button [`refresh`] that refetches.
    2. **Banner** [`banner`] when `unavailable` is set: "As of h:mm — {unavailable}".
    3. **Error card** [`error`] on a non-2xx response: "Can't reach the gauge" (502) or "No live data source" (503), with a 44 px "Try again" button.
    4. **Status bar** [`status`] only when `observed_eap` or `rank ≥ 1`.
        - An observed EAP level comes first: an octagon icon, its title in capitals, " · at {gauge_ft} ft", on its `color`.
        - Then the ladder: a triangle icon, the `level` and the `headline`.
        - Ladder colours: ADVISORY/WARNING `#8A6A00`, WATCH `#B54708`, DANGER `#B42318`, CRITICAL/EVACUATE `#7A1712`, with white text.
    5. **Lake card** [`lake`]:
        - `now_ft` + " ft" in 40 px type;
        - a trend arrow and "{|trend_ft_per_h|} ft/hr", or "steady";
        - when `problem` is set, a flag [`gauge-flag`] "Gauge: {problem}";
        - a "Normal" tag with a check icon [`normal`] at rank 0 with no observed EAP;
        - in normal state (rank 0, no observed EAP, every level's pct 0 or None), "3-day peak {middle} ft ({dry}–{wet})" in place of the stick.
    6. **Stick** [`stick`] (ensemble, not normal):
        - an SVG stick 2.0 ft to `max(4.8, max peak + 0.2)` ft (5.5 until 2026-11-15; now just above the 10,000-year storm's 4.73 ft), with tick labels every 1 ft;
        - the zones: Warning dashed line `#8A6A00`, Plastic line `#C2410C`, overtop band `stick_ft`→`band_top_ft` `#B42318` at 55 %, the region between the bands `#F9DDD9`, above bridge `#F3C9C4`;
        - the water fill to `now_ft` (`#1F6FD1`);
        - one circle [`peak-dot`] per peak (r 4.3), in rows of 0.1 ft (row = floor(peak × 10) / 10), stacked outward from the stick at 10 px spacing;
        - dot colours by `peak_levels`: None `#1F5FB8`, early_warning `#8A6A00`, eap_plastic `#C2410C`, overtop `#B42318`, bridge `#7A1712`;
        - zone labels [`level-<key>`] "{label} {pct}%", or "{label} reached" in bold when `reached`;
        - a solid line at `cases.middle`, labelled "middle {value}";
        - the key "● = one possible outcome, at its highest level".
    7. **Stick, fallback**: the same zones with a range bar [`range-bar`] from dry to wet and a middle mark. Labels read "{label} {pct}%", with no number for Plastic.
    8. **Next 3 days** [`timeline`]:
        - x = 0 is now at `lake.now_ft`; `middle_ft[i]` sits at x = i + 1 h;
        - the shading 25–75 at 30 % and 10–90 at 16 % (fallback: low–high at 20 %);
        - the zones, the middle line in black;
        - labels "Now", then every 24 h as "Wed 5p" in Pacific time.
    9. **Rain strip** [`rain`] below it on the same x scale. One bar [`rain-bar`] per element of `rain_6h.middle` (solid `#1F5FB8`) over `rain_6h.wet` (pale `#DCE8F8` with a `#1F6FD1` outline). The key reads "Rain, in per 6 h · middle · wet"; the y max is the larger of 0.4 and the largest wet value.
    10. **`no_forecast`** without a banner: the lake card and a "No forecast" tag [`no-forecast`] only.
    11. **Footer** link "Details" → `dashboard` [`details`].
2. Dark mode (`prefers-color-scheme: dark`): page `#121212`, cards `#1C1C1A`, text `#F0EFEC`, secondary `#C3C2B7`; zone and dot colours as above; the text contrast ≥ 4.5:1 rule holds.
3. Routes per Interfaces. `render.py`: `ui_url = f"{config.ui_base_url}/"`.
4. README: one paragraph on `/` (field view), `/dashboard` and `/field`.
**Tests.** `test_api.py`:
- `root serves the field page` (`<title>Crystal Lake</title>`);
- `root with mode=backtest redirects to the dashboard` (307, `Location: dashboard?mode=backtest`);
- `root with mode=live serves the field page`;
- `dashboard serves the old page`.
`test_alert_render.py`: `alert link opens the field view` (ends with "/" and has no "?mode=").
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q` pass (the browser checks are in T6)

### T6 — Playwright acceptance at 390 px
Depends on: T5
**Context.** D29/D30: the states operators can meet, rendered in a real browser at phone width.
**Files.** Create `tests/test_field_page.py`; modify `pyproject.toml` (dev extra: `playwright>=1.50,<2`), `README.md` (one line: `.venv/bin/playwright install chromium` for the browser test).
**Interfaces.** Consumes `field.summarize` (P6) and the routes from T5.
**Work.**
1. Skip the module unless `playwright` imports and chromium launches.
2. `monkeypatch.delenv` `ALERT_ENABLED`, `CALIB_ENABLED`, `QPF_ARCHIVE_ENABLED`, `HA_URL` and `HA_TOKEN`. Start `create_app(art)` with uvicorn on a free port in a background thread.
3. Build each state's `FieldSummary` with `summarize` on synthetic inputs, and serve it with `page.route("**/field", …)`. "HA down, none" answers 502 with `{"detail": "Can't reach the gauge"}`.
4. Viewport 390×844, light scheme; WATCH runs again in dark. Save each state's screenshot under pytest's `tmp_path`.
5. Pick the newest `playwright` release at least 7 days old; record its version and release date in the commit message.
**Tests.** One test per state, named for it:
- **normal:** `normal` visible; `status` and `stick` absent; the lake card shows "3-day peak".
- **WATCH:**
    - `status` shows "WATCH" and "Overtop 22%";
    - 50 `peak-dot` elements;
    - `level-overtop` shows "22%", and no visible text contains "of 50";
    - `timeline` contains 2 shading paths and 1 middle line;
    - the `rain-bar` count equals `len(rain_6h["middle"])`.
- **fallback:** `range-bar` present; no `peak-dot`; `level-overtop` shows "%".
- **stale gauge:** `gauge-flag` visible.
- **HA down, previous result:** `banner` shows "Can't reach the gauge".
- **HA down, none:** `error` shows "Can't reach the gauge" and a "Try again" button.
- **Every state:**
    - `document.documentElement.scrollWidth <= 390`;
    - the page's visible text matches no `\b3(39|4[0-3])\.\d` (no absolute elevation);
    - in light and dark, the computed colours of `header` and `lake` text against their backgrounds have a contrast ratio ≥ 4.5 (a small JS luminance helper).
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q tests/test_field_page.py` passes with chromium installed, and skips cleanly without it

### T7 — Dashboard live view on the shared builder
Depends on: T2
**Context.** D19 says the alert job, `/live/predict` and `/field` share one builder. Today `/live/predict` builds its own bundle from `fetch_conditions`. P9 moves its no-what-if path onto `live_source().build_bundle()`, keeping its response shape and the what-if path.
**Files.** Modify `src/lake_rise/api.py`, `tests/test_api.py`.
**Interfaces.** Consumes `live_source` (imported into `api.py` as a name, as in T4).
**Work.**
1. In `live_predict`, when `req.storm is None and req.start_offset_h == 0`, take the bundle from the shared builder:
    - `bundle = live_source(art, alert_config_from_env()).build_bundle()`;
    - if `req.stop_log_count` is set, `bundle = bundle.model_copy(update={"stop_log_count": clamp})`.
    `fetch_conditions` still supplies the `current` and `past` blocks. Otherwise the path is unchanged.
2. Keep `forecast_source` and the `rainfall` block computed as today from this bundle's scenarios.
**Tests.** `test_api.py`:
- `live predict without a storm uses the shared builder`: the patched `live_source` fake's `build_bundle` is called once;
- `live predict with a what-if storm keeps its own path` (the fake's `build_bundle` is not called);
- the existing `/live/predict` tests pass, updated only for the new patch target.
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q tests/test_api.py` pass

### T8 — Alert-level replay command
Depends on: T1, T2
**Context.** D22: before S1 deploys, show the user every archived run where counting changes the alert level compared with the old smoothed chances. The NAS deploy waits on this result, and on the user's decision if any past level drops.
**Files.** Modify `src/lake_rise/cli.py`, `docs/calibration-log.md`, `README.md`; add a test to `tests/test_ensemble_rain.py`.
**Interfaces.** Consumes `antecedent.archive_spinup`, `scenarios.ensemble_members`, `predict`, `rules.evaluate`, `qpf_archive.load("ecmwf_ens")`, `calibration.archive.load`. Produces `lake-rise alerts-replay`.
**Work.**
1. Implement per Hard parts "Replay": one line per differing run, "YYYY-MM-DDTHHZ old=<LEVEL|NORMAL> new=<LEVEL|NORMAL>", then the total line.
2. Run it against the NAS data copies (`LAKE_RISE_QPF_DIR`, `LAKE_RISE_ARCHIVE_DIR` pointed at copies) and record the result in a calibration-log entry.
**Tests.** `test_ensemble_rain.py`: `old and new ladders agree when every member is far below the thresholds`, and `they can differ when one member is far above the rest`. Both run the replay's level function on synthetic peaks, not the archive.
**Acceptance criteria.**
- [ ] `.venv/bin/lake-rise alerts-replay --since 2026-11-01` runs on the copied data and prints the total line
- [ ] The calibration log records the full replay's totals and every run whose level dropped

## Review focus
- **Stale or invalid gauge during a real rise.** The forecast still runs from a held reading (D24). T3 tests that the flag passes through, T6 that it shows.
- **Fewer than 50 members.** Percentages use the actual member count; T3's percentage test takes `n` from the result, not 50.
- **The forecast log after T1.** It records counts as probabilities from here on; S2's "what changed" reads them (D33). No test now.
- **Proxy prefix.** T5 asserts a relative `Location`; the real `/lake-rise/` proxy is checked on the NAS after deploy.
- **Human gates, not tests:** the six T6 screenshots are looked over, and the user checks a real phone (D29), before the deploy.

## Verification
(Added by dev-test when the slice is done.)
