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
  and `.venv/bin/pytest -q`. The **Step 6 margin is currently tight (~342.78 ft)** — baseflow/
  percolation increases trade against the flood peak, so watch it.
- **Known structural limitation:** a single lumped interflow reservoir can't both make the
  Step 6 flood peak *and* sustain the post-rain recession. The intended fix is a two-timescale
  subsurface (see the log's "Structural findings"). Don't try to force both from one knob.
- The model is pure/framework-free in `model.py`; parameters live only in the JSON artifact
  (no magic numbers in code). Keep it that way.
