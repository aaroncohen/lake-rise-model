# Lake filter — a rain-adaptive, model-assisted lake reading: Implementation Plan

Design: docs/design.md (not a slice: a user request of 2026-11-15, recorded as D47; supersedes the
trend definition in D26)
Status: draft (2026-11-15)
Reviewed: codex gpt-5.6-sol, Claude Fable, 2026-11-15; 21 items folded in, 1 put to the user (P12)

## Goal
The lake reading that starts the forecast and appears on the page ("Lake now" and its trend) comes
from a Kalman filter instead of a 30-minute median. The filter models three things:
- the gauge's error as noise plus an offset that persists ~15–20 min;
- the lake's rate of rise as something that changes only gradually;
- the model's water balance as the expected rate, with measured rain loosening how fast the rate
  may change.

It smooths hard in dry weather and lets the gauge steer quickly while rain is measured (or the rain
gauge is out). Done when:
- the study command shows less hour-to-hour jitter than today's anchor on real gauge noise, and
  synthetic storm tracking within P12's bounds;
- the live forecast, page and alerts use the filtered level, falling back to today's median when the
  filter can't run.

## Evidence so far (2026-11-15, 9 days of 1-minute gauge readings)
- The raw depth's error is ~0.057 ft per reading; a 30-min mean still leaves 0.026 ft (white noise
  would leave 0.010). The offsets persist ~15–20 min and show no wind dependence at the speeds seen
  (≤ 7 mph).
- Today's anchor (30-min median of `sensor.crystal_lake_depth_smoothed`) jitters 0.040 ft hour to
  hour. The forecast peak follows the anchor at 0.5–1×; nothing else moves it minute to minute.
- A prototype of this filter, on that data with synthetic 0.1 ft/h storms added (scripts in the
  session scratchpad; T3 reproduces them):
  - jitter 0.021 ft dry and 0.041 ft wet, against 0.032 and 0.047 today;
  - within ~0.015 ft of a storm the model sees, even at 2× or ½× the true rate;
  - if the model misses the storm, 0.051 ft behind at 1 h and 0.010 ft at 2 h (today: 0.029, 0.021).
- **What the archive holds.** Hourly lake levels only from 2026-07-01 (rain is backfilled to
  2026-02-21). Every storm since is small: the fall's eight below-crest storms delivered ~0.09 ft
  together. The largest recorded steps (+0.17 ft, no rain) are the laser re-locking. So no real storm
  can set the tuning yet: the bar is synthetic ramps on real noise, and the first wet-season storm
  re-checks it (T2 keeps its minute readings).
- **Known gauge behaviours the filter does not model** (calibration log):
  - an hour-of-day cycle of 0.05–0.11 ft (2026-11-05); the filter folds it into the level and rate,
    an apparent rate up to ~0.015 ft/h, under the 0.03 "steady" line;
  - reading high during rain and settling within hours (2026-11-14). With `wet = 1` the filter
    trusts the gauge most, so it biases the anchor high in storms: the safe side for alerts.
  - A remount (planned ~3 ft raise) changes the noise; re-run the study after it.

## Decisions made in planning
| ID | Decision | Why |
|----|----------|-----|
| P1 | State `x = [L, a, c, b]`: level (stick ft), inflow scale, residual rate (ft/min), gauge offset (ft). One-minute steps. Predict: `L ← L + a·u_in + u_rest(L) + c`, `a ← a`, `c ← c`, `b ← φ·b` with `φ = exp(−1/offset_minutes)`. Observe `z = L + b + v`, `v ~ N(0, noise_sd_ft²)`. | `b` is the persistent offset the data shows. `a` absorbs the model's watershed-inflow error (it over-delivered 4.6× this fall from a generic state). `c` covers what the model has no term for. |
| P2 | Process noise `Q = diag(level_sd², scale_sd², rate_sd(wet)², offset_sd_ft²·(1−φ²))`. Every `*_sd` is a standard deviation per one-minute step. `F` is the Jacobian with `∂u_rest/∂L` ignored: `F = [[1, u_in, 1, 0], [0,1,0,0], [0,0,1,0], [0,0,0,φ]]`. | Names say what they are (reviews: the units were ambiguous by orders of magnitude). The spillway's slope over a minute is negligible. |
| P3 | Only the watershed inflow is scaled. `u_in = units.cfs_to_dh(inflow_cfs, 1/60, surface_area_acres(art.geometry, L + off))`. `u_rest(L) = m5_lake_update(art, L+off, 0.0, rain_min_in, m6_spillway(art, L+off, control_elev), 1/60, month)[0] − (L+off)`: rain on the lake − spillway − evaporation, unscaled. | The spillway rating and evaporation are calibrated and test-enforced (weir 1.5); the soil-driven inflow is the uncertain part. Since P16 the run's rain only matters in its history: it shapes the transition back to dry. |
| P4 | Rain adaptivity: `rate_sd(wet) = rate_sd_dry + (rate_sd_wet − rate_sd_dry)·wet`. `wet[i] = min(1, rain over the trailing wet_window_hours / wet_full_in)`. **A minute whose rain is unknown counts as fully wet**, and so does every minute after it within the window. A rain gauge that hasn't reported within `lake_stale_minutes` makes the current hour's rain unknown (#74). **Since P16** any wetness at the newest minute means no estimate, so the wet parameters only shape the transition back to dry (the run still filters its wet minutes). | The user's request. An outage must not put the filter in its stiffest mode while a storm may be raising the lake unobserved (both reviews' first finding). |
| P5 | Initialisation: `L₀` = median of the first 30 valid minutes; `a₀ = 1`, `c₀ = 0`, `b₀ = 0`; `P₀ = diag(noise_sd² + offset_sd², init_scale_sd², (init_rate_sd_ft_per_h/60)², offset_sd²)`. | "Wide covariance" decided nothing. |
| P6 | Robust update: innovation `y = z − (L + b)`, `S = HPHᵀ + R`. If `|y| > gate_sigmas·√S`, the update uses `R' = R·(|y| / (gate_sigmas·√S))²` (the measurement variance only). After each predict and update, `a` is clamped to `[0, scale_max]`; the covariance is left as computed. | Huber: a single 1 ft spike moves the level by < 0.005 ft. The clamp keeps a wild inflow-scale estimate from driving the level. |
| P7 | Stateless: every call re-runs the filter over the trailing `history_hours`. The history is cut back to start at the latest stop-log change in that window (the stop-log helper's history), so the spillway control is constant over the run. The cut applies to the readings and the inflow, not the rain: rain before the change still counts toward the wetness (#74). | Deterministic for tests and the /field cache; no persisted state. A stop-log pull mid-storm changes outflow at once, and a scalar control applied to earlier hours would invent a residual rate. |
| P8 | Minute grid: minutes `t_k = floor_minute(now) − (N−1−k)` for `k = 0..N−1`. Each takes the last state at or before `t_k`; `unknown`/`unavailable` → `None`. The last element is the minute containing `now`. Source: `sensor.crystal_lake_depth` (`HAConfig.lake_raw_sensor`). | One definition for all callers (reviews: off-by-one). The raw depth goes `unknown` honestly; the smoothed sensor holds its last value through a dropout. |
| P9 | No estimate (→ today's 30-min median) when fewer than `min_valid_minutes_last_30` of the grid's last 30 minutes are non-`None`, or when the history after P7's cut is under 30 minutes. | Degrade, don't crash. A dead gauge is still flagged by `gauge_problem`. |
| P10 | The page trend is the filter's total rate `a·u_in + u_rest(L) + c` in ft/h (3 dp), at `now`. "Faster/slower" uses the same quantity 60 minutes earlier in the same run (`LakeEstimate.rate_hour_ago_ft_per_h`). The "steady" threshold (0.03 ft/h) is unchanged; the page copy is unchanged. | One source for level and trend. Supersedes D26's "30-min median now against one hour ago". The trend is now a current rate, not an hour's change. |
| P11 | Not filtered: the observed-EAP crossing state machine (`fetch_gauge_observation`), the hourly archive's medians and the backtest's T0 anchor. **Amended (#74):** `/live/predict` now starts every option (the live forecast, a what-if storm, another horizon) from the filtered level when available, else the 30-min median, so the dashboard's options agree with each other and with /field. | Calibrated or safety-critical paths with their own rules. Before #74 `/live/predict` was excluded, and its options started from different levels. |
| P12 | **Storm-lag bound (user, 2026-11-15).** With the model blind to a storm (`u_in = 0`) in rain, the filtered anchor may trail today's anchor by at most **0.03 ft more** at 30 and 60 min after the rise starts, and by **no more** at 120 min. With the model right or at half the rate, it is never further behind than today's. | Offered "never below today's reading in rain" (alerts never later, but no smoothing in storms); the user chose a small, bounded lag for smoother storm readings. At 0.1 ft/h, 0.03 ft is ~18 min of threshold delay for an unmodelled storm. Since P16 the live anchor in rain is today's median, so the storm checks compare the median with itself while wet and test the filter only once its window is dry. |
| P13 | Parameters live in the artifact as `lake_filter`, and in `parameter_registry.json` (class `provisional`, not tunable). The initial values are the prototype's; T3 retunes them and records the evidence. | Repo rule: parameters live only in the artifact; the registry test covers every leaf. |
| P14 | 1-minute readings are kept in a gitignored day-file archive (`data/gauge_minute/`), appended hourly by the archive scheduler. Entities: `lake_raw_sensor`, `lake_sensor`, `rain_sensor`, `lake_fresh_sensor`, `stop_log_helper`. | HA keeps minute data ~10 days. The first wet-season storm must be re-checkable without anyone remembering to capture it. `lake_sensor` makes today's anchor reproducible as the study's comparator. |
| P15 | The NAS deploy waits for T3's tuning and the user's OK. | The alert path must not run on untuned prototype values. |
| P16 | **Dry only (user, 2026-11-16).** `estimate_lake` returns `None` whenever the wetness at the newest minute is above 0: any rain, or unknown or stale rain (P4), within `wet_window_hours`. Callers keep today's 30-min median (`build_bundle`) and its trend (`/field`). | The user's decision after the #74 review: no passing setting beat today's median in rain. The settings that held P12 there (margins up to +0.002 ft) had wet jitter no better than the median's (0.047–0.053 ft vs 0.048) and a worse p99 (0.079–0.082 vs 0.072). In dry weather the filter cuts the jitter by two thirds. |

## File structure
```
src/lake_rise/
  lake_filter.py            new, pure (numpy): estimate_lake, minute_grid, wetness
  artifact.py               modify: LakeFilter; Artifact.lake_filter
  antecedent.py             modify: archive_replay() returns the spin-up and the replay's hourly inflows
  gauge_minute.py           new: day-file archive of minute readings
  sources/live_ha.py        modify: minute_history() (T2); filtered anchor in build_bundle (T4)
  calibration/scheduler.py  modify: module-level archive_tick(); appends minute readings
  api.py                    modify: /field trend from the filter's estimate
  cli.py                    modify: `gauge-capture` (T2), `lake-filter-study` (T3)
artifacts/crystal_lake_v0.json, artifacts/parameter_registry.json   modify: lake_filter
docs/calibration-log.md, docs/design.md, CLAUDE.md, .gitignore       modify
tests/test_lake_filter.py, tests/test_gauge_minute.py, tests/test_lake_filter_study.py,
tests/test_live_ha.py, tests/test_api.py, tests/test_antecedent*.py
```

## Interfaces
```python
# artifact.py
class LakeFilter(_ArtifactModel):
    history_hours: int                  # 12
    noise_sd_ft: float                  # 0.045  white part of the reading error
    offset_sd_ft: float                 # 0.03   persistent offset (stationary sd)
    offset_minutes: float               # 20     its AR(1) time constant
    level_sd_ft: float                  # 5e-5   per-minute level process sd
    scale_sd: float                     # 2e-3   per-minute random walk of the inflow scale a
    rate_sd_dry_ft_per_min: float       # 3e-6   per-minute random walk of c, dry
    rate_sd_wet_ft_per_min: float       # 1e-4   ... fully wet
    init_scale_sd: float                # 0.5
    init_rate_sd_ft_per_h: float        # 0.1
    scale_max: float                    # 5.0
    gate_sigmas: float                  # 4.0
    wet_window_hours: float             # 6
    wet_full_in: float                  # 0.2
    min_valid_minutes_last_30: int      # 20
    comment: str | None = None
# Artifact gains: lake_filter: LakeFilter | None = None   (None -> today's 30-min median everywhere)

# lake_filter.py
@dataclass(frozen=True)
class LakeEstimate:
    level_ft: float                     # stick height at the last grid minute
    rate_ft_per_h: float                # P10, at the last grid minute
    rate_hour_ago_ft_per_h: float | None   # P10, 60 grid minutes earlier (None if the run is shorter)

def minute_grid(rows: list[tuple[datetime, str]], now: datetime, minutes: int) -> list[float | None]:
    """P8. `rows` are HA (time, state) changes, any order; states that don't parse as floats -> None."""

def rain_by_minute(hourly_in: list[float | None], partial_hour_in: float | None,
                   now: datetime, minutes: int) -> list[float | None]:
    """Each hour's total spread evenly over its minutes; the current hour's partial total over
    its minutes so far. Aligned with minute_grid. None = unknown (P4)."""

def estimate_lake(art: Artifact, readings: list[float | None], rain_in_by_minute: list[float | None],
                  *, inflow_cfs_by_minute: list[float] | None, control_elev_abs: float, month: int
                  ) -> LakeEstimate | None:
    """readings in stick ft, control_elev_abs absolute (stick + art.datum.sensor_to_absolute_offset_ft).
    All series aligned per P8. inflow None -> u_in = 0. Returns None per P9."""

# antecedent.py
def archive_replay(art, as_of, recent_rain) -> tuple[SpinupState, list[float]] | None:
    """archive_spinup's state, plus the PURE replay's hourly watershed inflow (q_in_cfs + q_agw_cfs,
    before the baseflow-assimilation scale) for every replayed hour, oldest first, the last one the
    hour ending at as_of. archive_spinup(...) = archive_replay(...)[0], or None."""

# sources/live_ha.py
class LiveHASource:
    def minute_history(self, entity_ids: list[str], start: datetime, end: datetime
                       ) -> dict[str, list[tuple[datetime, str]]]   # raises httpx.HTTPError
    lake_estimate: LakeEstimate | None   # set by build_bundle (T4); None when the filter didn't run

# gauge_minute.py
DEFAULT_DIR = <repo>/data/gauge_minute     # env LAKE_RISE_GAUGE_MINUTE_DIR overrides
def append(rows: dict[str, list[tuple[datetime, str]]], directory=None) -> None
    # merged per UTC day into YYYY-MM-DD.json: {"<entity>": [[iso, state], ...]}, sorted, one row per time
def load(start: datetime, end: datetime, directory=None) -> dict[str, list[tuple[datetime, str]]]

# calibration/scheduler.py
def archive_tick(config, art, source_factory) -> None   # the hourly job's body, now testable
```

## Hard parts
**Inflows by minute (T4).** The replay's last inflow is the hour ending at `as_of =
floor_hour(now)`. Grid minute `t` takes the inflow of the replay hour containing `t`; minutes after
`as_of` take the last replayed hour's value (persistence). No replay (degraded rain, short archive,
`archive_spinup` would return None): `inflow_cfs_by_minute = None`.

**Partial-hour rain (T4).** The accumulator's rise since the current hour began, from the same
history fetch as the trailing rain; `None` if that fetch failed or the accumulator is unavailable.

## Not building
- Filtering the paths in P11.
- Reading the 1 mm air gap instead of the 0.01 ft depth.
- Wind, daily-cycle or rain-bias terms in the gauge model (known limits above; revisit with data).
- A hard cap on the lake's rate.
- Any page copy or layout change.

## Global constraints
- `model.py` stays untouched; the filter calls its functions (real signatures:
  `surface_area_acres(geom, h)`, `m5_lake_update(...)` returns a 4-tuple, `m6_spillway(art, h, control)`).
- No magic numbers in code: every tuning value is in `lake_filter`.
- Anchors must pass: `.venv/bin/lake-rise validate`; `.venv/bin/pytest -q` passes.
- Stick height only in user-facing output. Maintain `docs/calibration-log.md`.
- Degrade, don't crash: an HA or archive failure falls back to today's behaviour.
- Commits end with the Co-Authored-By line; no "Stage N" in code or commits.

## Tickets

<!-- Issues: T1 #66 · T2 #67 · T3 #68 · T4 #69 -->

### T1 — The filter
Depends on: none
**Context.** The pure core (P1–P6, P8–P10) with its parameters in the artifact and registry, at
the prototype's values. Nothing calls it yet.
**Files.** Create `src/lake_rise/lake_filter.py`, `tests/test_lake_filter.py`. Modify
`src/lake_rise/artifact.py`, `artifacts/crystal_lake_v0.json`, `artifacts/parameter_registry.json`.
**Interfaces.** Produces `LakeFilter`, `Artifact.lake_filter`, `LakeEstimate`, `minute_grid`,
`rain_by_minute`, `estimate_lake`.
**Work.**
1. `LakeFilter`, `Artifact.lake_filter`, the JSON block with the Interfaces values. Its `comment`:
   "PROVISIONAL (2026-11-15): prototype values from 9 days of 1-minute readings; every *_sd is a
   standard deviation per one-minute step. Retuned by `lake-rise lake-filter-study`." Registry
   entries for every leaf, class `provisional`.
2. `minute_grid`, `rain_by_minute`, `estimate_lake` per P1–P6, P8–P10.

**Tests.** Synthetic minute series, seeded `numpy.random.default_rng(0)`, 12 h, the 3-log
control, month 11.
- The noise is not only the filter's own model: white noise sd 0.05 plus offset steps that hold a
  uniform ±0.05 ft for 10–40 min.
- Where a test says "today's anchor", it means the 30-min trailing median of the same readings.

Tests:
- `a flat lake stays within 0.02 ft`, dry, no inflow.
- `dry jitter beats a 30 min median`: the hour-to-hour change of the estimate, sampled every 15 min
  over the last 6 h, has a smaller sd.
- `a modelled storm is tracked`: truth ramps 0.1 ft/h over the last 2 h, with that inflow and
  0.25 in/h of rain. Within 0.02 ft.
- `a blind storm in rain stays within P12`: the same with inflow `None`. Behind by at most today's
  anchor's lag + 0.03 ft at 30 and 60 min, and no more than today's at 120 min.
- `missing rain counts as wet`: the blind storm with the rain series all `None` passes the same
  bound.
- `a 20 min dropout mid-storm is re-acquired`: the blind storm with readings `None` for minutes
  60–80 of the ramp. Within 0.03 ft 30 min after readings return.
- `a 1 ft spike moves the level by less than 0.005 ft`.
- `no estimate without enough recent readings`: the last 30 min have 19 valid minutes → `None`;
  20 → an estimate.
- `minute_grid holds the last value, maps unknown to None, and ends at the minute containing now`.
- `rain_by_minute spreads hours and the partial hour, and marks unknown hours None`.

**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q` passes (including `tests/test_registry.py`)
- [ ] `.venv/bin/lake-rise validate` passes

### T2 — Data seams: minute history, minute archive, replay inflows
Depends on: none
**Context.** The data the study (T3) and the live wiring (T4) need:
- one public HA minute-history fetch;
- a day-file archive of minute readings, appended hourly (P14);
- the archive replay's hourly inflows.

**Files.** Create `src/lake_rise/gauge_minute.py`, `tests/test_gauge_minute.py`. Modify
`src/lake_rise/sources/live_ha.py`, `src/lake_rise/antecedent.py`,
`src/lake_rise/calibration/scheduler.py`, `src/lake_rise/cli.py`, `.gitignore`, the antecedent tests.
**Interfaces.** Produces `LiveHASource.minute_history`, `gauge_minute.append/load/DEFAULT_DIR`,
`antecedent.archive_replay`, `scheduler.archive_tick`, `lake-rise gauge-capture`.
**Work.**
1. `minute_history`: one `_get_history` call per entity, returning `(time, state)` rows.
2. `gauge_minute.append/load` per Interfaces.
3. `archive_replay`: restructure `replay_with_assimilated_baseflow` to also return the pure replay's
   records. `archive_spinup` delegates to it.
4. Move the scheduler's `_tick` body into module-level `archive_tick`. It also fetches the last 2 h
   of the P14 entities and appends them. A failure there is logged and never stops the hourly archive.
5. `lake-rise gauge-capture --days N` (default 9) appends the last N days of the P14 entities.
6. `.gitignore`: `data/gauge_minute/` (comment on its own line).

**Tests.**
- `append then load round-trips across a UTC midnight`.
- `overlapping appends keep one row per timestamp`.
- `archive_tick still archives the hour when the minute append fails` (fake source raising on
  `minute_history`).
- `archive_replay's inflows end at as_of and match the replay records`. A test archive with a known
  rain burst: the hour count, and `inflow[-1] == q_in + q_agw` of the last record; unaffected by
  a non-1.0 baseflow scale.
- Existing `archive_spinup` tests stay green.

**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q` passes
- [ ] `lake-rise gauge-capture --days 1` against HA writes today's file (reported in the PR)

### T3 — The tuning study, and the tuned parameters
Depends on: T1, T2
**Context.** Sets `lake_filter` from evidence and records it (P12, P13). The archive has no real
storm big enough to tune on (Evidence), so the gating bar is synthetic ramps on real minute noise.
Real storms are reported, not gating.
**Files.** Modify `src/lake_rise/cli.py`, `artifacts/crystal_lake_v0.json`,
`artifacts/parameter_registry.json` (only if a value's class changes), `docs/calibration-log.md`,
`docs/design.md`. Create `tests/test_lake_filter_study.py`. Modify `src/lake_rise/lake_filter.py`
only to fix a defect, with a T1-style test.
**Interfaces.** Produces `lake-rise lake-filter-study [--minute-dir] [--archive-dir] [--write]`.
**Work.**
1. Inputs:
   - ≥ 7 days of the minute archive (`lake_raw_sensor`, `lake_sensor`, rain);
   - the continuous archive (the NAS copy) for model inflows (`archive_replay` at each hour).

   Exit with an error if under 7 days.
2. **Real noise.** "Truth" is the centred 4 h median of the raw depth: not independent of the
   gauge, a stated limitation. Reported for today's anchor (30-min median of `lake_sensor`) and the
   filter:
   - hour-to-hour jitter sd, split dry (`wet == 0`) and wet;
   - the 99th-percentile error.
3. **Synthetic storms.** Ramps of 0.05 and 0.1 ft/h for 6 h, once per 24 h, added to the raw
   readings. Each ramp starts either abruptly or rising linearly to full rate over 2 h. The ramp's
   rain is 0.25 in/h from 2 h before it until it ends. Three model cases:
   - right: the ramp's inflow added to the replayed inflow;
   - half: half of it;
   - blind: no ramp inflow, the replay only.

   "Behind" is truth minus estimate (positive = behind), averaged over the ramps at 30/60/120 min
   after each onset.
4. **Real storms.** List every archived rise of ≥ 0.10 ft in 24 h (settled 24 h medians) with
   ≥ 0.5 in of rain, with the filter's and today's behind-ness. Print the count; zero is expected
   until the wet season.
5. **Grid:**
   - `rate_sd_wet_ft_per_min` {3e-5, 5e-5, 1e-4, 2e-4};
   - `wet_window_hours` {3, 6, 12};
   - `offset_minutes` {15, 20, 30}.

   Keep the combinations that meet P12 in every synthetic case. Pick the lowest dry jitter; ties go
   to the lower wet jitter. **Amended (#74, with P16):** among those, a p99 no worse than today's
   anchor's, then the most hourly calls on the filter, then the lowest p99, then the lowest dry
   jitter; with none meeting the p99, the lowest p99. `--write` writes the winner into the artifact. With no passing
   combination the command says so and writes nothing.
6. Calibration log: a dated entry with the study's tables, the choice, the real-storm count, and
   the known limits (Evidence); parameter rows. `lake_filter.comment` names the entry.
7. Design: D47 (this filter replaces the 30-min median for the live anchor and page trend; P10
   supersedes D26's trend; P12 is the user's bound), and a Status row "Lake filter |
   docs/plans/lake-filter.md | implemented".

**Tests.**
- `the storm finder ignores a rise without rain` (a +0.17 ft re-lock).
- `the bar rejects a combination that lags a blind ramp more than P12 allows` (a stub estimator
  that is 0.05 ft late).
- `--write leaves the artifact untouched when nothing passes`.

**Acceptance criteria.**
- [ ] The study's tables, the chosen values and the real-storm count are in the calibration-log
      entry, and the chosen filter's dry jitter is below today's anchor's
- [ ] `.venv/bin/lake-rise validate` and `.venv/bin/pytest -q` pass

### T4 — Live wiring
Depends on: T1, T2, T3
**Context.** The live forecast, the alerts (through `build_bundle`) and the page use the filtered
level and rate (P7, P9, P10), with today's 30-min median as the fallback. Activated only with
T3's tuned values (P15).
**Files.** Modify `src/lake_rise/sources/live_ha.py`, `src/lake_rise/api.py`, `CLAUDE.md`,
`tests/test_live_ha.py`, `tests/test_api.py`.
**Interfaces.** Consumes `estimate_lake`, `minute_grid`, `rain_by_minute`, `archive_replay`,
`minute_history`. Produces `LiveHASource.lake_estimate`.
**Work.**
1. `with_archive_spinup` uses `archive_replay` and keeps its inflows as `self._replay_inflows`
   (`None` when there is no replay).
2. `build_bundle`, when `art.lake_filter` is set:
   - after the spin-up, fetch `history_hours` of `lake_raw_sensor`, `rain_sensor` and
     `stop_log_helper` with `minute_history`;
   - cut the history at the latest stop-log change (P7);
   - build the series (Hard parts) and call `estimate_lake` with the bundle's stop-log control
     (absolute) and `as_of.month`.

   On an estimate:
   - `current_elevation_abs_ft = level + offset`;
   - `self.lake_estimate = estimate`.

   Otherwise, and on any `httpx.HTTPError` or `ValueError` in this step (logged):
   - keep the snapshot's 30-min median;
   - `self.lake_estimate = None`.
3. `/field`: `trend_ft_per_h` and `trend_was_ft_per_h` come from `getattr(src, "lake_estimate",
   None)` when set; else from `lake_trend_ft_per_h` as today.
4. CLAUDE.md, "Always-true guardrails": "The live lake reading is filtered (`lake_filter`;
   docs/plans/lake-filter.md): rain-adaptive and model-assisted, falling back to the 30-min median.
   Its parameters are set by `lake-rise lake-filter-study`. Re-run it after the first wet-season
   storm, after the laser remount, and on any change to the replay's inflows or the spillway."

**Tests.** `tests/test_live_ha.py`'s `_handler` serves a flat minute history for
`sensor.crystal_lake_depth` and the stop-log helper, so the filter runs in the existing tests.
- `build_bundle starts the forecast at the filtered level`: flat 1.00 ft for 11.5 h, then 1.10 ft
  for the last 30 min, dry. The anchor is below 1.05 (today's median reads 1.10).
- `a stop-log change cuts the filter's history`: the helper changes 3 → 2 at −2 h. The estimate
  matches a run on the last 2 h only.
- `build_bundle falls back to the 30-min median when the minute history fails`.
- `field's trend comes from the filter's rate`, and `falls back to lake_trend_ft_per_h without an
  estimate`.
- `no lake_filter in the artifact keeps today's anchor`.

**Acceptance criteria.**
- [ ] `.venv/bin/pytest -q` and `.venv/bin/lake-rise validate` pass
- [ ] The PR reports two `build_bundle()` calls against HA 3 minutes apart, with both anchors and
      today's medians

## Review focus
- **A storm the model misses, in rain:** T1 pins P12 on synthetic data, and T3 gates the tuning on it.
- **The rain gauge out:** T1 `missing rain counts as wet`.
- **A dropout mid-storm:** T1 `a 20 min dropout mid-storm is re-acquired`.
- **A stop-log pull mid-storm:** T4.
- **The first wet-season storm** isn't in the data yet. T2 keeps its minute readings, and
  CLAUDE.md tells the next maintainer to re-run the study.

## Verification
(Added by dev-test when the slice is done.)
