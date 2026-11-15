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
.venv/bin/lake-rise params --tunable   # parameters by provenance; --set PATH=VAL --out to tune
```

Parameter **provenance & tunability** (which values are research-grounded vs. gauge-calibrated
vs. weakly-grounded guesses) is catalogued in
[`artifacts/parameter_registry.json`](artifacts/parameter_registry.json), the machine-readable
companion to the calibration log. `lake-rise params` lists/filters them; `params --set
hspf.PERC_coeff=0.28 --out artifacts/crystal_lake_v1.json` writes a range-checked tuned artifact
without touching the canonical one. `lake-rise sweep <param>` shows how a parameter affects
accuracy against stored storms; `lake-rise capture-storm --from-archive --t0 … --end …` freezes a
storm from the continuous archive (no retention deadline); `lake-rise backtest-offline <storm|dir>` scores an artifact
against captured storms offline.

Rain forecasts are archived and scored against the gauge so the model's rain scenarios can be
fitted to evidence. `lake-rise qpf fetch` stores the missing NBM percentile and ECMWF ensemble
runs (00Z/12Z, newest first, back to the gauge record's 2026-02-21 start; resumable). The NAS
does the same every hour (`QPF_ARCHIVE_ENABLED`, default on with `CALIB_ENABLED`). Stored runs
are skipped and a per-source lock stops two updates overlapping on one machine; don't backfill
the same history on two machines, copy `data/qpf/` instead.
`lake-rise qpf score [--common]` compares NBM, ECMWF, Apple and NWS against the gauge by day of
lead and for the 3-day total.

The newest ECMWF run drives the live forecast. The model runs all 50 members, each corrected
against the gauge for the ensemble's bias and too-narrow spread (`ensemble_rain`). Low / median
/ high are the members at the 10th/50th/90th percentile of lake peak, and the crossing
probabilities come from the spread of all their peaks. Apple's hourly forecast only times the
rain within each day. With no run under 30 h old, the forecast falls back to Apple with a fixed
band by lead day (`uncertainty`).

### Automated calibration (human-approved, versioned)

`lake-rise calibration` re-fits the three subsurface parameters (`PERC_coeff`, `AGWRC_per_day`,
`leakage`) from **hydrological signatures** — recession→AGWRC, BFI≈0.67→percolation,
dry-equilibrium→leakage — grades each by data sufficiency, and never auto-applies. `calibration
train` proposes a graded change (with a multi-criteria accuracy table and a safety veto), `--email`
sends it to `CALIB_RECIPIENT` for approval; `calibration approve <id> --token …` promotes it to a
new versioned artifact and `calibration revert <version>` rolls back. `calibration archive` (and,
when `CALIB_ENABLED=1`, an hourly server job) grows the continuous record the signatures need.

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
an alert when the forecast crosses up into a higher risk level. Independently, a lightweight job
checks a rolling 15-minute gauge average every 5 minutes and sends an observed EAP notice at
3.30 ft (Mandatory Alert), 3.90 ft (Bridge Closure), and 4.40 ft (Evacuate Downstream). Alerts give a forecast summary, the
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
- **Observed EAP crossings:** each physical EAP level pages once per continuous event, even if a
  predictive notice was already sent. Higher observed levels escalate separately; a jump across
  multiple levels produces one consolidated notice with every active EAP action. The observed track
  re-arms only after the 15-minute average stays below 3.25 ft for 30 minutes, preventing flapping.
- **Tiered, cumulative audiences:** each level maps to an audience group; severe levels reach
  broader contacts **in addition to** the small initial list — emergency/road at dam overtopping
  (bridge closure), and the evacuate audience (NORCOM/KCDOT) at bridge-deck overtopping.
- **Toggleable test-level alert** (`ALERT_TEST_ENABLED`): notifies a small test audience whenever
  more than `ALERT_TEST_RAIN_IN` of rain enters the forecast, with the same full detail as a real
  warning — an end-to-end Alert System Check.
- **Channels:** email (SMTP) and Twilio SMS, selected via `ALERT_CHANNELS`. Notice content is
  rendered from editable Jinja2 templates (separate email/SMS), overridable via `ALERT_TEMPLATE_DIR`.
- **Notice structure:** every notice is three sections — (1) a summary carrying an explicit action
  verdict, (2) current lake/dam status, (3) the forecast and the likelihood that action is required.
  Recipients are the dam management team and EAP contacts only; all recommended action refers to the
  preventative measures or the EAP, and nothing is sent to lake residents automatically.

```bash
# Preview without sending (no state change); --send goes live via the configured channels.
.venv/bin/lake-rise alert --dry-run                                   # live forecast
.venv/bin/lake-rise alert --dry-run --fixture fixtures/ha_snapshot.json
```

Each live hourly forecast run also appends its **forecast of record** — inputs, every scenario's
hourly rain and trajectory, the threshold probabilities, and the forecast source's full 168 h
QPF — to `data/forecasts/YYYY-MM-DD.jsonl` (override with `LAKE_RISE_FORECAST_LOG_DIR`), so
forecasts can be scored against the gauge afterwards. It runs after dispatch and can never block
or fail an alert.

See `.env.example` for the full alerting configuration. Deployment needs only the new env vars in
the NAS `.env` — the scheduler runs inside the existing container (no compose change).

## How it works

The model (`src/lake_rise/model.py`) steps hourly through six modules, in order, each
independently testable (Hydrologic Reference Modules 1–6):

1. **Canopy interception** — CEPSC = 0.20 in per storm.
2. **Soil-moisture bucket** — fills to LZSN = 4.5 in, drained by monthly PET × LZETP.
3. **Interflow generation/routing** — overflow → interflow storage, drained at IRC = 0.5/day.
4. **Watershed lag** — 4.6 h pipeline delay.
5. **Lake-level update** — Δh = (Q_net · Δt · 0.0826) / A(h), with open-water evaporation off the lake
   surface (monthly `lake_evaporation_in_per_day`, from on-site temperature/humidity/wind and the lake probe).
6. **Spillway outflow** — stop-log-controlled weir, `Q = capacity·(H/H_rated)^1.5` (+ board-seam leakage,
   currently 0: evaporation carries the dry-season loss).

**Hindcast** replays trailing rainfall to spin up soil-moisture / interflow state (trusting
the live gauge for elevation); **forecast** projects each scenario (low / median / high)
forward from that end-state (live: each of the 50 calibrated ECMWF members, from which
low / median / high are picked). The predictor is a pure function `predict(bundle, artifact)`.

All parameters live in a single versioned JSON artifact (`artifacts/crystal_lake_v0.json`),
and every raw→model transform lives in the shared library imported by both paths
(training/serving-skew guard).

## Data sources (Home Assistant, read-only)

| Input | Entity | Notes |
|---|---|---|
| Lake level | `sensor.crystal_lake_depth_smoothed` (live anchor) / `sensor.crystal_lake_depth` (archive) | depth frame; see datum note. The gauge counts as degraded when `source_value_valid` is false (the laser lost the water surface, e.g. it read the lake bottom through clear water), not only when it stops reporting |
| Weather (evaporation) | `sensor.carport_temp_corrected`, `sensor.carport_temp`, `sensor.carport_humidity`, `sensor.gw3000b_wind_speed`, `sensor.lake_temperature` | hourly means archived from HA long-term statistics; drive the recorded lake-evaporation estimate in backtests and storm records |
| Rainfall | `sensor.gw3000b_daily_rain_piezo` | inches; hourly rain = per-hour rise of the since-midnight accumulator (raw history, ~10 d) for the live hindcast, archive, backtest and storm capture; the archive is backfilled from HA long-term statistics (kept permanently). Not `hourly_rain_piezo`: a rolling 60-min total that double-counts bursts |
| Forecast (QPF + PoP) | `weather.47_77849_122_10882` (**Apple WeatherKit**) | carries both precipitation and PoP, 168 h; times the ECMWF members' rain within each day, and is the whole forecast (with a fixed band) when no recent ECMWF run is archived |
| Forecast fallbacks | `weather.home` (met.no, precip only), `weather.nws_…` (PoP only) | |
| NWS QPF (logged, not modeled) | api.weather.gov gridpoint SEW 133,75 | 6 h QPF blocks + PoP, ~7.5 days; stored in each forecast-log record (`nws_outlook`) beside Apple's, to score both against the gauge |
| NBM rain percentiles (archived and scored, not modeled) | NWS National Blend of Models QMD GRIB2, AWS `noaa-nbm-grib2-pds` (back to 2020-05) | 5/10/25/50/75/90/95th percentiles for days 1-3 and the 3-day total, 00Z/12Z, nearest 2.5 km grid point; `data/qpf/nbm/` (`qpf_archive`) |
| ECMWF ensemble rain (**drives the live forecast**) | ECMWF open data IFS ENS 0.25 deg, Google Cloud mirror (AWS fallback; back to 2023), CC-BY-4.0 | all 50 members for days 1-4 (days 1-3 before 2026-11-14) and the 3-day total, gauge-calibrated (`ensemble_rain`), 00Z/12Z, bilinear at the site; `data/qpf/ecmwf_ens/` |
| Stop-logs | `input_number.crystal_lake_stop_logs` | boards in (0–3), set by the operator when boards change; archived hourly. Missing/unavailable → the date-driven default (summer 3, winter 2). Per-board heights: `stop_logs.board_heights_ft` |

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
- The live forecast spins up soil and groundwater by replaying the archived true rain (up to
  ~182 d, falling back to a 10-day seasonal-seed hindcast when the archive is short or the rain is
  degraded), then scales the groundwater store so the model's baseflow matches the lake's measured
  water balance over the prior week (`baseflow_assimilation`). The single groundwater store
  over-delivered in the fall (1.2–1.4 cfs vs ~0.1–0.2 cfs measured, 2026). Since 2026-11-14 a
  **deep zone** between the soil and groundwater must refill before percolation recharges
  groundwater, which keeps a dry fall's storms from reaching the lake while a wet spring still
  does; the assimilation is now mostly idle. The fall's below-crest storms go from 1.0 ft of
  modeled watershed water to 0.24 ft (~0.09 ft measured). See the calibration log.

See the plan at `~/.claude/plans/plan-the-initial-stages-delightful-thimble.md` for full context.
