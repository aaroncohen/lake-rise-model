# CLAUDE.md — guidance for agents working in this repo

Crystal Lake lake-rise model: a small, interpretable HSPF-style interflow-bucket model that
estimates the lake's elevation and projects it forward for dam early-warning. See
[`README.md`](README.md) for orientation and commands.

## Calibration is grounded in field & gauge data — reference and maintain the log

**[`docs/calibration-log.md`](docs/calibration-log.md) is the source of truth for why each
calibrated parameter has the value it does.** It records dam field measurements and
gauge-derived analysis with provenance.

- **Before changing any calibrated parameter** (e.g. `PERC_coeff`, `AGWRC_per_day`,
  `seasonal_agw_default_in`, spillway/leakage terms, the datum offset), **read its entry in
  `docs/calibration-log.md` and the inline `*_comment` in
  [`artifacts/crystal_lake_v0.json`](artifacts/crystal_lake_v0.json) first.** Several values
  are anchored to real observations — do not silently re-tune them away from a gauge number.
- **After any model/calibration change, update the log**: add a dated entry, update the
  parameter table, and keep the artifact `*_comment` in sync. New field/gauge observations go
  there too.
- Some parameters are **deliberately not to be changed without strong cause** — notably the
  spillway weir exponent (1.5), which is corroborated and test-enforced. The log flags these.

## Always-true guardrails

- **Anchors must pass.** After any change touching hydrology or the spillway, run
  `.venv/bin/lake-rise validate` (Step 6 peak 343.1 ± 0.5; summer (2-log) dry-equilibrium 339.6–339.8)
  and `.venv/bin/pytest -q`. Step 6 sits at **~342.70 ft** with `PERC_coeff = 0.42`
  and `IFEXP = 3` (2026-11-27): inside the band but only ~0.10 ft above the 342.6 floor, and
  raising `PERC_coeff` or `IFEXP` lowers it. Re-verify both anchors on any subsurface edit.
- **Stop-log crests come from the 2026-11-25 laser survey, per spillway** (`stop_logs`, in stick
  ft): counted the EAP's way, 1 log = main 0.98 / aux 0.99; winter normal 1, summer normal 2;
  ~5 in per log; 0 logs = the aux log out with the main's base boards in. The two legs differ
  below 1 log. The survey changes the model only: the EAP's levels and pool numbers (0.65 / 0.97 /
  1.30) stay as written. Step 6 runs on the design study's rated crests (`Artifact.rated_control`).
- **Open-water evaporation carries the dry-season lake loss** (`lake_evaporation_in_per_day`,
  derived from on-site data), and stop-log seam leakage is 0. The summer dry-equilibrium anchor
  is held by evaporation, not leakage — change one and re-check the other.
- **The live forecast spins up from the archive** (`antecedent.replay_subsurface`, via
  `LiveHASource.with_archive_spinup`), and `PERC_coeff` is fitted on that same replay. Change one
  and re-check the other. The live forecast, backtest and archive storm records then scale the
  groundwater store to the lake's measured water balance over the prior week
  (`antecedent.replay_with_assimilated_baseflow`, `baseflow_assimilation` in the artifact); the
  `PERC_coeff` fit uses the *pure* replay. It was a stopgap for the single groundwater store's fall
  over-delivery; since the deep zone (`deep_zone`, 2026-11-14) gates recharge it is mostly idle.
  Retire it once a wet-season recession confirms the deep zone.
- **The deep zone is what separates June from the fall** (`deep_zone`, `model.m8_deep_zone`):
  percolation refills it before recharging groundwater, so a wet spring (June 2026: 3.1 cfs)
  and a dry summer (fall 2026 storms: ~nothing reached the lake) behave differently with the
  soil about as dry in both. Its knobs and `IFEXP` are hand-fitted to those two. Change any of
  them and re-check the June 13 06Z delivery (model ~3.35 cfs with `gate_exp` 4, 2026-11-27;
  target 3.1 since the 2026-11-25 survey), the eight fall below-crest storms (2026-11-14 log
  entry) and Step 6.
- **The live forecast's rain is the calibrated ECMWF ensemble** (`ensemble_rain`,
  `scenarios.ensemble_members`): all 50 members are run; low/median/high and the crossing
  probabilities come from their peaks. `calibration_by_day` is fitted to the gauge (2026-11-14
  log entry). Refit it, don't hand-tune it. The `uncertainty` band is only the fallback (no run
  under `max_run_age_h`) and the page simulator's.
- The live lake reading is filtered (`lake_filter`; docs/plans/lake-filter.md): rain-adaptive and
  model-assisted, falling back to the 30-min median. In rain, or with the rain gauge offline, the
  live reading is today's 30-min median; the filter runs in dry weather only. That median counts
  only minutes with a valid water-surface reading (`lake_filter.median_anchor`, P17), so a
  far-echo gap holds the last valid half hour, not the smoothed sensor's held sample. Its parameters are set by
  `lake-rise lake-filter-study`. Re-run it after the first wet-season storm, after the laser
  remount, and on any change to the replay's inflows or the spillway. Known limit: a rise while
  the rain gauge reads zero (snowmelt, a frozen piezo) keeps the filter stiff, so it lags (+0.05 ft
  at 60 min on a blind 0.1 ft/h rise); it stays until snowmelt is modelled (2026-11-16 dry-only log entry).
- **Flood-peak vs post-rain-recession trade-off (largely relieved, 2026-07-03 #3).** It used to
  be that a single saturation-triggered interflow store couldn't make the Step 6 peak *and*
  sustain the recession from one knob. The #3 wetness-driven interflow generation (interflow
  engages below saturation) added the fast path, so `PERC_coeff` could rise to strengthen the
  slow-store recession without hurting the peak. Interflow *release* is still one timescale
  (IRC); a fuller distributed routing is a future refinement (see the log's "Structural findings").
- The model is pure/framework-free in `model.py`; parameters live only in the JSON artifact
  (no magic numbers in code). Keep it that way.

## Development process

- Major features follow the dev-process skills: design → plan → implement → test.
- Design: [`docs/design.md`](docs/design.md) (the field view, approved 2026-11-14); slice plans go in `docs/plans/`.
- Tests: `.venv/bin/pytest -q`, plus `.venv/bin/lake-rise validate` for the anchors.
