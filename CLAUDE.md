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
  `.venv/bin/lake-rise validate` (Step 6 peak 343.1 ± 0.5; 3-log dry-equilibrium 339.6–339.8)
  and `.venv/bin/pytest -q`. Step 6 sits at **~342.91 ft** (comfortably above the 342.6 floor
  since the #3 change); still re-verify both anchors on any subsurface edit.
- **Flood-peak vs post-rain-recession trade-off (largely relieved, 2026-07-03 #3).** It used to
  be that a single saturation-triggered interflow store couldn't make the Step 6 peak *and*
  sustain the recession from one knob. The #3 wetness-driven interflow generation (interflow
  engages below saturation) added the fast path, so `PERC_coeff` could rise to strengthen the
  slow-store recession without hurting the peak. Interflow *release* is still one timescale
  (IRC); a fuller distributed routing is a future refinement (see the log's "Structural findings").
- The model is pure/framework-free in `model.py`; parameters live only in the JSON artifact
  (no magic numbers in code). Keep it that way.
