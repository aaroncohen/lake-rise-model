# Storm-truth dataset (offline backtest records)

Each `*.json` here is a **`StormRecord`** (`src/lake_rise/storm_record.py`): a frozen snapshot
of the inputs `backtest.run_backtest` consumes for one real storm window — the trailing +
forward **observed rainfall**, the **observed hourly lake gauge** (absolute ft), the T0 anchor,
and the spillway control elevation — plus capture provenance (`label`, `captured_at`,
`data_fresh`, `notes`).

A record freezes the **observations, never the model output**. Scoring re-runs the model, so a
record stays valid as parameters change — that's the point: it's the objective ground truth a
parameter **sweep** (Stage 3) and **auto-calibration** (Stage 4) optimise against.

## Capture (needs a live Home Assistant connection)

```bash
export HA_URL=... HA_TOKEN=...
.venv/bin/lake-rise capture-storm --hours 120 --label 2026-03-atmos-river \
    --out data/backtest_storms/2026-03-atmos-river.json
```

Capture right after a storm, while the ≤10-day raw HA history still covers the window.

## Score an artifact against the dataset (offline, no HA)

```bash
.venv/bin/lake-rise backtest-offline data/backtest_storms            # score the whole set
.venv/bin/lake-rise backtest-offline data/backtest_storms/<one>.json # score one storm
.venv/bin/lake-rise backtest-offline data/backtest_storms --artifact artifacts/crystal_lake_v1.json
```

Reports per-storm `rmse_ft` / `peak_err_ft` / `peak_timing_err_h` and a dataset aggregate.

## Data-quality caveats (travel with every record)

- **Gauge noise:** the lake sensor is per-hour-median denoised, but ~0.2 in residual noise
  remains — do not chase RMSE below that floor.
- **Rain is approximate:** derived from a within-hour accumulator max, not a true hourly total.
- **~10-day retention, no recorder statistics:** capture promptly; a recovered mid-window gap
  can't be reconstructed later.
- **One storm is weak evidence.** Calibration discipline here is the same as everywhere in this
  repo: accumulate many storms/seasons before trusting a fit, and regularise toward the
  research `prior`s in `artifacts/parameter_registry.json`.

Records with `"data_fresh": false` were captured over a degraded window — keep them for context
but weight them lightly (or exclude them) when tuning.
