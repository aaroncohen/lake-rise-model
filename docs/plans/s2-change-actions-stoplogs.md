# S2 — What changed, actions, stop logs: Implementation Plan

Design: docs/design.md (slice S2; delivers F5, F6, F7; depends on D2, D3, D4, D14, D18, D26, D33–D36)
Status: implemented (2026-11-15; self-approved plan under the user's overnight authorisation)
Reviewed: Claude Fable, 2026-11-14; 11 items folded in (codex Sol skipped: its session budget was reserved for PR reviews)

## Goal
The field view shows what changed since the previous forecast, what to do now, and what
pulling stop logs would do.
- **What changed:** the previous middle peak as a dashed line on the stick, the previous percentages beside any that moved, and whether the lake is rising faster or slower.
- **Do now:** short actions with icons, and the next EAP level's actions as tags.
- **Stop logs:** one range bar per option.

Every section hides when it doesn't apply. Demonstrated by Playwright at 390 px on a WATCH state built from a deterministic storm (D36), and on a normal state where all three sections are absent.

## Decisions made in planning
| ID | Decision | Why |
|----|----------|-----|
| Q1 | The previous forecast is the newest forecast-log record whose `generated_at` (the forecast's floored `as_of` hour) is between 50 min and 3 h before the request time. It is used only if it has the S2 fields (`levels_pct`), the same `stop_log_count` and the same `rain_source`. Otherwise there is no "what changed". | D14, D33. |
| Q2 | "What changed" shows when the middle peak moved by ≥ 0.05 ft or any level's percentage differs, comparing display values (`cases["middle"]` vs `middle_was_ft`). It is "worse" when the middle rose ≥ 0.05 ft or any percentage rose. In the page's normal state (no status bar, every pct 0 or None) `change` is None: there is no stick to draw it on. | D8: nothing when nothing moved. |
| Q3 | "Faster / slower" compares the lake's trend now with the trend an hour earlier (`lake_trend_ft_per_h(now − 1 h)`). None unless both are non-None and `trend × was > 0` (same direction). Then `abs(trend) − abs(was) ≥ 0.02` → "faster", `≤ −0.02` → "slower", else None. | D2, D26; one more HA history fetch per cache miss. |
| Q4 | Page actions reuse the emails' rule for when preventative measures show (`show_preventative`: an active or reachable EAP level, or a dam-overtop chance ≥ 10 %). The short labels sit beside the long text in code, keyed by id. | D18, D35: the page can't advise anything the email doesn't. |
| Q5 | Next level is the lowest EAP level not yet reached whose gauge reading the wet-case peak reaches (stick ft). Shown as tags under "At {gauge} ft". | D35. |
| Q6 | The stop-log options are every count from `stop_log_count − 1` down to 0, each a `predict` on the same bundle with that count. The section shows when any option changes any level's percentage, or the middle peak by ≥ 0.05 ft. "(default)" marks a count taken from the seasonal default. | D3, D4, D34. |
| Q8 | Under each stop-log bar the page shows the overtopping percentage only (F7). The design's D34 also says "counts for levels in play"; overtop-only is the operators' decision number, and the others would crowd a 100-px column. | F7 vs D34 inconsistency; D7/D8. |
| Q7 | The proposed short wording (table in T1) ships tonight and is flagged for the user's morning review (D35). Changing a label later is a one-line edit. | The user asked for the work to proceed overnight and to review tough calls in the morning. |

## File structure
```
src/lake_rise/
  alerting/eap.py          modify: EAPLevel.short_actions (parallel to actions)
  alerting/render.py       modify: preventative_actions() returns (id, long, short); emails unchanged
  field.py                 modify: level_pcts(); FieldChange, FieldAction, FieldNextLevel, FieldStopLogs; summarize kwargs
  forecast_log.py          modify: levels_pct, ensemble_peaks_ft, middle_peak_ft in records; previous()
  bundle.py, sources/snapshot.py, sources/live_ha.py   modify: stop_log_count_is_default
  api.py                   modify: /field passes previous record, trend an hour ago, stop-log options
  static/field.html        modify: what changed, Do now, stop-log bars
tests/
  test_alert_render.py, test_forecast_log.py, test_live_ha.py, test_field.py, test_api.py, test_field_page.py
```

## Interfaces

**alerting.eap.EAPLevel** (T1) gains `short_actions: tuple[str, ...]`, the same length as `actions`. Action ids are `f"eap{rank}-{i}"`, with `i` the index.

**alerting.render.preventative_actions(*, stop_logs: int | None, eap_active: bool, eap_likely: bool, p_crest_pct: int) -> list[tuple[str, str, str]]** (T1) returns `(id, long, short)` in today's order, with ids:
- `pull_logs` / `logs_out` (the two stop-log variants);
- `inspect`;
- `monitor`.
`_preventative_actions` becomes `[long for _, long, _ in preventative_actions(...)]`, so the email text is byte-identical.

**field.level_pcts(result: PredictionResult, bundle: InputBundle, art: Artifact) -> dict[str, int | None]** (T2): `{"early_warning", "eap_plastic", "overtop", "bridge"} → pct`, by exactly the rules `summarize` uses today, including all-None in `no_forecast` mode (which needs the bundle). Moved out of `summarize`, which then calls it.

**forecast_log.forecast_record** (T2) adds:
- `levels_pct`: `level_pcts(result, bundle, art)`;
- `ensemble_peaks_ft`: absolute ft, or None;
- `middle_peak_ft`: the absolute peak of the "median" scenario (a flat copy of `scenarios["median"]["peak_elevation_ft"]`, kept flat on purpose so `previous()` readers don't depend on the scenario layout).
It needs `art: Artifact` as a new keyword argument; its callers pass it.

**forecast_log.previous(at: datetime, *, before_as_of: datetime | None = None, directory=None) -> dict | None** (T2): the newest record from `at`'s UTC day and the day before, with `at − 3 h ≤ generated_at ≤ at − 50 min`, else None. Unparseable lines are skipped, never raised. Also skipped (#47): records with `horizon_hours == 0` (an outage hour), and records whose `as_of` ≥ `before_as_of` (`/field` passes `bundle.as_of`).

**InputBundle.stop_log_count_is_default: bool = False** (T3). Set from `Snapshot.stop_log_count_is_default`, which `LiveHASource.fetch_snapshot` fills: True when the helper was missing, unavailable or out of range.

**field.py** (T4):
```python
class FieldChange(BaseModel):
    since: datetime                  # the previous record's generated_at
    middle_was_ft: float             # stick, display-rounded
    pct_was: dict[str, int | None]   # only the level keys whose pct differs
    worse: bool                      # Q2

class FieldAction(BaseModel):
    id: str                          # "pull_logs" | "logs_out" | "inspect" | "monitor" | "eap<rank>-<i>"
    short: str

class FieldNextLevel(BaseModel):
    title: str                       # EAPLevel.title
    gauge_ft: float
    actions: list[FieldAction]

class FieldStopLogOption(BaseModel):
    logs_in: int
    dry_ft: float
    middle_ft: float
    wet_ft: float
    pct: dict[str, int | None]       # level_pcts for this option

class FieldStopLogs(BaseModel):
    logs_in: int
    is_default: bool
    options: list[FieldStopLogOption]   # [0] is "leave them" (current count), then fewer

# FieldSummary gains (all optional, default None / []):
#   change: FieldChange | None
#   trend_change: Literal["faster", "slower"] | None
#   actions: list[FieldAction]
#   next_level: FieldNextLevel | None
#   stop_logs: FieldStopLogs | None

def summarize(..., previous: dict | None = None, trend_was_ft_per_h: float | None = None,
              options: list[tuple[int, PredictionResult]] = (),
              ) -> FieldSummary    # stop_log_is_default is read from bundle.stop_log_count_is_default
```

**GET /field** (T5): same response type, now carrying the S2 fields.

## Hard parts

**Actions (T4).** In stick ft, with `now = lake.now_ft`, `wet = cases.wet` and `middle = cases.middle`:
- `eap_active = observed_eap is not None or any(now >= lv.gauge_ft for lv in EAP_LEVELS)`;
- `reachable = [lv for lv in EAP_LEVELS if now < lv.gauge_ft <= wet]`;
- `eap_likely = any(lv.gauge_ft <= middle for lv in reachable)`;
- `show = eap_active or reachable or (pct["overtop"] or 0) >= 10`.

When `show` is true, `actions` = `preventative_actions(stop_logs=bundle.stop_log_count, eap_active=…, eap_likely=…, p_crest_pct=pct["overtop"] or 0)`, as `FieldAction(id, short)`. `next_level` = `reachable[0]` (the lowest) when any, with its `short_actions`. In `no_forecast` mode, actions use `eap_active` only and there is no next level.

**Stop-log options (T5).**
```text
options = [(k, predict(bundle.model_copy(update={"stop_log_count": k}), art))
           for k in range(bundle.stop_log_count - 1, -1, -1)]
```
Only when `bundle.stop_log_count > 0` and the mode is not `no_forecast`. `summarize` builds `FieldStopLogs` with option 0 = the current result. It sets it to None when no option differs from the current in any pct or by ≥ 0.05 ft in middle.

**Change (T4).** `previous` is valid per Q1; summarize receives a record already filtered by `forecast_log.previous`.
- It checks `previous["stop_log_count"] == bundle.stop_log_count` and `previous["rain_source"] == result.input_summary.get("rain_source")`, and that `levels_pct` is present.
- `since = datetime.fromisoformat(previous["generated_at"])`.
- `middle_was_ft = stick(previous["middle_peak_ft"])`, via `field.stick()` (the Decimal subtraction, not float minus offset).

## Not building
- The details screen (S3), the watershed scene (S4).
- A per-phone "since you last looked" (D14).
- Recommending whether pulling logs is safe (D4).
- New wording beyond shortening existing text (D18).

## Global constraints
The S1 plan's Global constraints apply unchanged (stick height only; anchors; short labels; ≥ 44 px targets; contrast; attribution line; dependency rules). Additionally:
- The email text must stay byte-identical (T1 refactor).
- `/field` keeps its 120 s cache and last-good behaviour; the S2 work runs inside the same cached build.

## Tickets

<!-- Issues: T1 #21 · T2 #22 · T3 #23 · T4 #24 · T5 #25 · T6 #26 -->

### T1 — Short action labels from the alert code
Depends on: none
**Context.** The page shows short versions of the emails' actions (F6, D18, D35, Q4). Short labels live beside the long text, so they can't drift.
**Files.** Modify `src/lake_rise/alerting/eap.py`, `src/lake_rise/alerting/render.py`, `tests/test_alert_render.py`.
**Work.**
1. Add `short_actions` to each `EAPLevel`, exactly:

| Level | short_actions |
|---|---|
| 1 Mandatory Alert | "Follow EAP flowchart · notify all"; "East-side cars to west side"; "Sandbag · plastic on downstream slope" |
| 2 Bridge Closure | "Overtopping east of bridge"; "Close the bridge" |
| 3 Evacuate Downstream | "Bridge deck overtopped"; "Call NORCOM + KCDOT: imminent failure"; "Evacuate downstream" |

2. Add `preventative_actions` per Interfaces, with these short labels:
    - `pull_logs`: "Pull {n} stop log{s}" ("Pull stop logs" when the count is unknown);
    - `logs_out`: "Stop logs already out";
    - `inspect`: "Inspect spillways · clear debris";
    - `monitor`: "Monitor more often".
3. `_preventative_actions` uses it.
**Tests.**
- `every EAP action has a short label` (equal lengths for every level);
- `preventative short labels follow the same conditions` (pull_logs at p_crest 10 with 2 logs; logs_out with 0 logs; neither below 10 % with no EAP level);
- `email text is unchanged` (the existing render tests pass unmodified).
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q` pass with `tests/test_alert_render.py`'s existing tests unmodified

### T2 — Level percentages in the forecast log, and the previous record
Depends on: none
**Context.** "What changed" compares against the previous forecast of record (D14, D33, Q1). Records gain the field levels' percentages, the member peaks and the middle peak.
**Files.** Modify `src/lake_rise/field.py` (extract `level_pcts`), `src/lake_rise/forecast_log.py`, `src/lake_rise/alerting/service.py` (pass `art`), `tests/test_forecast_log.py`, `tests/test_field.py`.
**Work.**
1. Extract `level_pcts` from `summarize` with no behaviour change; `summarize` calls it.
2. `forecast_record(..., art=art)` adds the three fields; update every caller (`grep -rn forecast_record src tests`).
3. Add `previous()` per Interfaces.
**Tests.**
- `record carries level percentages and peaks`;
- `previous picks the newest record between 50 minutes and 3 hours old`;
- `previous skips an unparseable line`;
- `previous is None when only fresher or older records exist`;
- `previous looks back across midnight UTC`;
- the existing `test_field.py` tests pass unmodified.
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q` pass

### T3 — Mark a default stop-log count
Depends on: none
**Context.** The stop-log comparison labels a count taken from the seasonal default as "(default)" (D34).
**Files.** Modify `src/lake_rise/bundle.py`, `src/lake_rise/sources/snapshot.py`, `src/lake_rise/sources/live_ha.py`, `tests/test_live_ha.py`.
**Work.**
1. `LiveHASource._stop_log_count` also reports whether it fell back. Keep the method's return value for its existing callers, and add `_stop_log_count_source(now) -> tuple[int, bool]` that it delegates to.
2. `fetch_snapshot` sets `Snapshot.stop_log_count_is_default`, and `bundle_from_snapshot` copies it to the bundle.
**Tests.**
- `stop-log count from the helper is not a default`;
- `missing helper gives the seasonal default, marked default`.
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q` pass

### T4 — Field summary: change, trend change, actions, stop logs
Depends on: T1, T2, T3
**Context.** The pure assembly of F5, F6 and F7 (Q1–Q6).
**Files.** Modify `src/lake_rise/field.py`, `tests/test_field.py`.
**Work.**
1. Add the models and `summarize` arguments per Interfaces.
2. Implement Hard parts "Actions", "Change", the stop-log options assembly, and Q3's trend change.
**Tests.**
- `change shows when the middle moved 0.05 ft`;
- `no change when nothing moved`;
- `change ignored when the stop-log count differs`;
- `change ignored for a record without levels_pct`;
- `change ignored when the rain source differs`;
- `no change in the normal state`;
- `worse when a percentage rose`;
- `trend faster and slower by 0.02 ft per hour`;
- `no trend change when either trend is missing or the directions differ`;
- `actions follow the email rule at 10 percent overtop`;
- `no actions when normal`;
- `next level is the lowest EAP level the wet case reaches`;
- `stop-log options hidden when pulling changes nothing`;
- `stop-log options list every count down to zero`;
- `default count is marked`.
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q tests/test_field.py` pass

### T5 — /field wiring
Depends on: T4; S1 T4 (#10) merged
**Context.** `/field` passes the previous record, the trend an hour ago, and the stop-log option predictions into `summarize`, inside the same cached build.
**Files.** Modify `src/lake_rise/api.py`, `tests/test_api.py`.
**Work.**
1. In the build, also gather:
    - `previous = forecast_log.previous(now)`;
    - `trend_was = src.lake_trend_ft_per_h(now - timedelta(hours=1))`;
    - `options` per Hard parts.
    Pass them to `summarize`.
2. Call `previous()` in its own `try/except Exception: previous = None`, outside the build's failure handling, so a log problem never turns into a 502 or a stale result.
**Tests.**
- `field includes stop-log options when pulling changes a percentage` (fake bundle with 2 logs);
- `field includes a change against a logged record`: write a record with `forecast_log.append` into the hermetic `LAKE_RISE_FORECAST_LOG_DIR`, with `generated_at` overwritten to now − 1 h, `stop_log_count` and `rain_source` matching the fake bundle, and `levels_pct` present;
- `an unreadable forecast log does not fail the field view`.
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q tests/test_api.py` pass

### T6 — The page: what changed, do now, stop logs
Depends on: T5; S1 T5 (#11) and T6 (#12) merged
**Context.** Draws F5–F7 on the field page, matching the approved mockup's "Example storm" board (dashed "was" line, Do now card with icons and tags, stop-log range bars).
**Files.** Modify `src/lake_rise/static/field.html`, `tests/test_field_page.py`.
**Work.**
1. **Stick** (when `change`):
    - a dashed line [`was-line`] at `middle_was_ft`, labelled "was {value}";
    - every level label whose key is in `pct_was` gets "(was {n}%)" [`level-was-<key>`].
2. **Lake card:** when `trend_change` is set, the trend text gains " · faster" or " · slower" [`trend-change`].
3. **Do now card** [`actions`] when `actions` is non-empty:
    - one row per action, with an icon (`pull_logs`/`logs_out`: boards; `inspect`: waves; `monitor`: clock; `eap*`: alert triangle) and its `short`;
    - when `next_level` is set, an "At {gauge_ft} ft" heading [`next-level`] (gauge with `toFixed(2)`, so 3.3 reads "3.30") with its actions as tags.
4. **Stop-log card** [`stop-logs`] when `stop_logs`:
    - the header "Stop logs: {logs_in} in", plus " (default)" when `is_default`;
    - one range bar per option [`option-bar`] on the same zone background as the stick (dry→wet bar, middle mark labelled), captioned "Leave {n}" / "Pull {k}" / "Pull all";
    - the overtop percentage under each (Q8).
5. All touch targets ≥ 44 px; sections absent when their data is absent.
**Tests.** `tests/test_field_page.py`, new states built with `summarize` on synthetic inputs (a deterministic 50-member storm, D36):
- `WATCH with change shows the was line, faster, actions, next level and stop logs`: `option-bar` count = number of options; `next-level` reads "At 3.30 ft";
- `normal shows no change, no actions and no stop logs`;
- `stop logs hidden when pulling changes nothing`;
- every state keeps S1's no-horizontal-scroll, no-absolute-elevation and contrast checks.
**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q tests/test_field_page.py` passes with chromium installed

## Review focus
- **Email text unchanged by the T1 refactor:** pinned by the existing render tests, run unmodified.
- **A forecast log written before S2:** it has no `levels_pct`, so there's no "what changed" (T4 test `change ignored…` plus Q1).
- **Morning review (user):** the T1 short labels (Q7).

## Verification
(Added by dev-test when the slice is done.)
