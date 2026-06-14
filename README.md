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

### Serving (infrastructure follow-up)

A stateless FastAPI service wraps the same pure predictor:

```bash
# Credentials: export them, OR put them in a .env file in the project root
# (copy .env.example -> .env). Real shell env vars override the file.
export HA_URL=http://homeassistant.local:8123 HA_TOKEN=<long-lived-token>
.venv/bin/lake-rise serve            # or: uvicorn lake_rise.api:app  (run from the project root so .env is found)
.venv/bin/lake-rise pull             # snapshot live HA -> fixtures/ha_snapshot.json
```

- `GET /` — **visualization page**: a **Manual what-if** mode (pick a preset/historical/custom
  storm and enter your own lake/watershed state) and a **Live (Home Assistant)** mode that pulls
  real conditions and runs the actual Apple Weather forecast — showing current rainfall (rate /
  today / event), past rainfall (week / month buckets → ~30 d soil-moisture spin-up), and the
  forecast band from the live lake level. You can also drop a what-if storm onto live conditions.
- `POST /simulate` — project a preset or custom storm from supplied situational parameters.
- `POST /backtest` — **model-accuracy backtest** (page's Backtest tab): slide back N hours, anchor
  to the observed lake level then, drive the model forward with **real observed rain** (no forecast),
  and compare predicted vs. actual gauge with error/peak/timing metrics. Needs `HA_URL` + `HA_TOKEN`.
- `POST /live/predict` — pull live HA data and project: real hindcast + the live Apple forecast
  (default) or a what-if storm override. Returns the `/simulate` shape plus `current` and `past`
  blocks. Needs `HA_URL` + `HA_TOKEN`; 503 if unset.
- `GET /presets` / `GET /historical` — the synthetic storm presets and the curated catalog of
  real Western Washington storms (near Woodinville, severity-sorted).
- `POST /predict` — predict from an inline snapshot body, or pull live HA data if none given.
- `GET /health` — liveness, model version, whether a live source is configured, and alerting status.
- `GET /model/version` — artifact version + cached validation-anchor results.
- `POST /alert/run?dry_run=true` — evaluate the forecast now and dispatch any crossing notices
  (manual / HA-triggered). Defaults to dry-run (renders without sending or mutating state).

`docker build -t lake-rise . && docker run -p 8000:8000 -e HA_URL=... -e HA_TOKEN=... lake-rise`.
Live data flows through `LiveHASource` (Apple WeatherKit forecast), which implements the same
`DataSource` protocol as the fixture/simulator — the predictor never changes.

## Alerting (early warning)

When `ALERT_ENABLED=1`, the server runs the simulation **hourly** (in-process scheduler) and sends
an alert when the forecast crosses up into a higher risk level. Alerts give a forecast summary, the
likelihood of crossing the early-warning (341.0 ft), dam-crest / initial-overtopping (342.2 ft),
and bridge-deck-overtopping (342.7 ft) thresholds, the expected and earliest crossing times, and
the peak level — **all in Pacific time** — plus a link back to the live simulator view. The
bridge-deck level mirrors the EAP: initial overtopping closes the bridge, and bridge-deck
overtopping is the "imminent failure" / evacuate trigger.

- **Adjustable escalation ladder** (`ALERT_LEVELS`): ordered levels, each with its own
  `threshold:probability` cutoff. The first entry is the initial alert; later entries are the
  worsening steps.
- **Fire-on-crossing only:** a level is alerted once when first crossed; no hourly repeats while it
  holds. A silent downgrade re-arms it; an optional one-shot all-clear fires on return to normal.
- **Tiered, cumulative audiences:** each level maps to an audience group; severe levels reach
  broader contacts **in addition to** the small initial list — emergency/road at dam overtopping
  (bridge closure), and the evacuate audience (NORCOM/KCDOT) at bridge-deck overtopping.
- **Toggleable test-level alert** (`ALERT_TEST_ENABLED`): notifies a small test audience whenever
  more than `ALERT_TEST_RAIN_IN` of rain enters the forecast, with the same full detail as a real
  warning — an end-to-end pipeline check.
- **Channels:** email (SMTP) and Twilio SMS, selected via `ALERT_CHANNELS`. Notice content is
  rendered from editable Jinja2 templates (separate email/SMS), overridable via `ALERT_TEMPLATE_DIR`.

```bash
# Preview without sending (no state change); --send goes live via the configured channels.
.venv/bin/lake-rise alert --dry-run                                   # live forecast
.venv/bin/lake-rise alert --dry-run --fixture fixtures/ha_snapshot.json
```

See `.env.example` for the full alerting configuration. Deployment needs only the new env vars in
the NAS `.env` — the scheduler runs inside the existing container (no compose change).

## How it works

The model (`src/lake_rise/model.py`) steps hourly through six modules, in order, each
independently testable (Hydrologic Reference Modules 1–6):

1. **Canopy interception** — CEPSC = 0.20 in per storm.
2. **Soil-moisture bucket** — fills to LZSN = 4.5 in, drained by monthly PET × LZETP.
3. **Interflow generation/routing** — overflow → interflow storage, drained at IRC = 0.5/day.
4. **Watershed lag** — 4.6 h pipeline delay.
5. **Lake-level update** — Δh = (Q_net · Δt · 0.0826) / A(h).
6. **Spillway outflow** — stop-log-controlled weir, `Q = capacity·(H/H_rated)^1.5` + board leakage.

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
- **Spillway** now uses a weir law `Q = capacity·(H/H_rated)^1.5` (was a linear interp that
  over-drained ~5× just above the control elevation — fixed a growing dry-recession error).
  Still scaled to the single known 342-ft capacity; the exact weir coefficient/geometry from
  the 2025 PIR remains a refinement. **Board leakage** still awaits field calibration.
- Rain gauge lacks long-term statistics (~10 d raw); soil-moisture spin-up falls back to the
  seasonal default. Enable recorder statistics for a true 30–60 d replay.

See the plan at `~/.claude/plans/plan-the-initial-stages-delightful-thimble.md` for full context.
