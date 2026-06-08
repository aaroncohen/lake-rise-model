# Crystal Lake lake-rise model

A small, interpretable **HSPF-style interflow bucket model** that estimates Crystal
Lake's elevation and projects it 6–72 h forward under rainfall scenarios, for
dam-management early warning (freeboard to crest, hours-to-crest, threshold-crossing
probabilities). Home Assistant owns notifications; this system only produces predictions.

This is the **testable-model-first** milestone: pull HA data into a snapshot, build a
model artifact, and run hindcast → forecast → prediction entirely on a local machine,
including against *simulated* conditions. The long-running services, REST API, Docker,
and scheduled jobs are a deliberate follow-up (see `Non-goals` in the plan).

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"

.venv/bin/pytest -q                 # 19 tests: per-module + calibration anchors
.venv/bin/lake-rise validate        # Step 6 peak (~343.1 ft) + dry equilibrium anchors
.venv/bin/lake-rise forecast --fixture fixtures/ha_snapshot.json   # real HA snapshot
.venv/bin/lake-rise simulate --storm step6 --stop-logs 0 --start-elev 338.8  # synthetic
```

## How it works

The model (`src/lake_rise/model.py`) steps hourly through six modules, in order, each
independently testable (Hydrologic Reference Modules 1–6):

1. **Canopy interception** — CEPSC = 0.20 in per storm.
2. **Soil-moisture bucket** — fills to LZSN = 4.5 in, drained by monthly PET × LZETP.
3. **Interflow generation/routing** — overflow → interflow storage, drained at IRC = 0.5/day.
4. **Watershed lag** — 4.6 h pipeline delay.
5. **Lake-level update** — Δh = (Q_net · Δt · 0.0826) / A(h).
6. **Spillway outflow** — stop-log-controlled weir (linear-interp stopgap) + board leakage.

**Hindcast** replays trailing rainfall to spin up soil-moisture / interflow state (trusting
the live gauge for elevation); **forecast** projects each scenario (low / median / high)
forward from that end-state. The predictor is a pure function `predict(bundle, artifact)`.

All parameters live in a single versioned JSON artifact (`artifacts/crystal_lake_v0.json`),
and every raw→model transform lives in the shared library imported by both paths
(training/serving-skew guard).

## Data sources (Home Assistant, read-only)

| Input | Entity | Notes |
|---|---|---|
| Lake level | `sensor.crystal_lake_depth_smoothed` | depth frame; see datum note |
| Rainfall | `sensor.gw3000b_hourly_rain_piezo` | inches; ~10 d raw history |
| Forecast (QPF + PoP) | `weather.47_77849_122_10882` (**Apple WeatherKit**) | preferred source; carries both precipitation and PoP, 168 h |
| Forecast fallbacks | `weather.home` (met.no, precip only), `weather.nws_…` (PoP only) | |
| Stop-logs | *(helper not yet created)* | parameterized; date-driven default |

`fixtures/ha_snapshot.json` is a real snapshot pulled 2026-06-08. The `DataSource` protocol
(`sources/base.py`) lets a live HA REST client drop in later without touching the model.

## Open items / known caveats

- **Datum offset (provisional).** The depth sensor's zero differs from the doc's staff
  frame; `sensor_to_absolute_offset_ft = 338.375` is cross-checked from the HA
  `summer_normal` threshold and a hindcast, but **needs a field tape-down**.
- **`deep_loss_fraction = 0.11`** tuned to the Step 6 elevation anchor (the slow IRC routing
  attenuates the instantaneous peak inflow vs. HEC-HMS; revisit with a real logged storm).
- **Board leakage** and **spillway stage-discharge below 342 ft** are stopgaps awaiting
  field calibration / weir geometry.
- Rain gauge lacks long-term statistics (~10 d raw); soil-moisture spin-up falls back to the
  seasonal default. Enable recorder statistics for a true 30–60 d replay.

See the plan at `~/.claude/plans/plan-the-initial-stages-delightful-thimble.md` for full context.
