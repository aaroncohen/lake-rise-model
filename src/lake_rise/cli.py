"""lake-rise CLI: run the whole chain locally — pull (snapshot) -> hindcast ->
forecast -> validate, plus a simulate command for synthetic what-ifs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer

from . import model, sim
from .artifact import DEFAULT_ARTIFACT, load_artifact
from .fsutil import atomic_write_text
from .geometry import control_elev_for_stop_logs
from .predict import predict
from .sources.fixture import FixtureSource
from .validate import run_anchors

app = typer.Typer(add_completion=False, help="Crystal Lake lake-rise prediction (local).")

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_SNAPSHOT = REPO_ROOT / "fixtures" / "example_snapshot.json"


def _art(artifact: str | None):
    return load_artifact(artifact or DEFAULT_ARTIFACT)


@app.command()
def validate(artifact: str = typer.Option(None, help="Path to model artifact JSON.")):
    """Run the calibration anchors (Step 6 peak, dry equilibrium) and report."""
    art = _art(artifact)
    results = run_anchors(art)
    typer.echo(f"Model artifact: {art.version}\n")
    all_ok = True
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        all_ok &= r.passed
        typer.echo(f"  [{mark}] {r.name}\n         target {r.target}  |  observed {r.observed}")
    typer.echo("")
    raise typer.Exit(code=0 if all_ok else 1)


@app.command()
def hindcast(
    fixture: str = typer.Option(str(EXAMPLE_SNAPSHOT), help="Snapshot JSON to read."),
    artifact: str = typer.Option(None),
):
    """Replay a snapshot's trailing rainfall to spin up state to 'now'."""
    art = _art(artifact)
    bundle = FixtureSource(art, fixture).build_bundle()
    control = control_elev_for_stop_logs(art.stop_logs, bundle.stop_log_count)
    state = model.initial_state(art, h0=bundle.current_elevation_abs_ft, month=bundle.as_of.month)
    end, records = model.run(art, state, bundle.trailing_rainfall_in,
                             bundle.as_of, control)  # start label only; replay is what matters
    typer.echo(f"Hindcast over {len(records)} h of rainfall "
               f"(total {sum(bundle.trailing_rainfall_in):.2f} in)")
    typer.echo(f"  start  SM={state.sm:.2f} in  S_if={state.s_if:.3f} in")
    typer.echo(f"  end    SM={end.sm:.2f} in  S_if={end.s_if:.3f} in  modeled_h={end.h:.3f} ft")
    typer.echo(f"  gauge  current_elevation={bundle.current_elevation_abs_ft:.3f} ft (trusted)")


@app.command()
def forecast(
    fixture: str = typer.Option(str(EXAMPLE_SNAPSHOT), help="Snapshot JSON to read."),
    artifact: str = typer.Option(None),
    as_json: bool = typer.Option(False, "--json", help="Emit the full PredictionResult as JSON."),
):
    """Run low/median/high scenarios and report freeboard / hours-to-crest / probabilities."""
    art = _art(artifact)
    bundle = FixtureSource(art, fixture).build_bundle()
    result = predict(bundle, art)
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    typer.echo(f"As of {result.generated_at}  (model {result.model_version}, "
               f"horizon {result.horizon_hours} h, data_fresh={result.data_fresh})")
    typer.echo(f"  current elevation : {result.current_elevation:.3f} ft")
    typer.echo(f"  freeboard to crest: {result.freeboard_ft:.3f} ft")
    typer.echo(f"  P(cross {art.thresholds_abs_ft.early_warning:.0f} early-warning): {result.p_cross_341:.0%}")
    typer.echo(f"  P(cross {art.thresholds_abs_ft.dam_crest:.1f} dam overtop)  : {result.p_cross_crest:.0%}")
    if art.thresholds_abs_ft.bridge_deck is not None:
        typer.echo(f"  P(cross {art.thresholds_abs_ft.bridge_deck:.1f} bridge deck) : {result.p_cross_bridge_deck:.0%}")
    typer.echo("  scenarios:")
    for s in result.scenarios:
        htc = f"{s.hours_to_crest:.1f} h" if s.hours_to_crest is not None else "—"
        htb = f"{s.hours_to_bridge_deck:.1f} h" if s.hours_to_bridge_deck is not None else "—"
        typer.echo(f"    {s.name:>6}: peak {s.peak_elevation:.3f} ft   hours_to_crest {htc}   hours_to_bridge {htb}")
    if result.peak_outside_validated_geometry:
        lo, hi = art.geometry.valid_elev_range_ft
        typer.echo(f"  ! peak leaves the validated geometry band ({lo:.1f}–{hi:.1f} ft); "
                   f"elevations above it are extrapolated (directional, not measured-range).")


@app.command()
def simulate(
    storm: str = typer.Option("heavy", help="Built-in scenario: step6 | heavy | dry"),
    stop_logs: int = typer.Option(3, help="Stop-log count 0-3."),
    start_elev: float = typer.Option(None, help="Starting absolute elevation (ft)."),
    artifact: str = typer.Option(None),
):
    """Drive the model with a synthetic storm on synthetic current conditions."""
    art = _art(artifact)
    now = datetime(2026, 1, 15) if storm in ("step6", "heavy") else datetime(2026, 7, 15)
    control = control_elev_for_stop_logs(art.stop_logs, stop_logs)
    h0 = start_elev if start_elev is not None else control

    if storm == "step6":
        series, sm0 = sim.step6_hyetograph(art), art.hspf.LZSN_in
    elif storm == "heavy":
        series, sm0 = sim.constant_storm(0.3, 48), art.hspf.LZSN_in  # saturated, 0.3 in/hr for 48 h
    elif storm == "dry":
        series, sm0 = sim.dry(72), art.seasonal_sm_default(now.month)
    else:
        raise typer.BadParameter("storm must be step6 | heavy | dry")

    src = sim.SimulatedSource.single_storm(now, h0, stop_logs, series, initial_sm_in=sm0)
    result = predict(src.build_bundle(), art)
    typer.echo(f"Simulated '{storm}'  start={h0:.3f} ft  stop_logs={stop_logs} "
               f"(control {control:.3f} ft)")
    typer.echo(f"  freeboard to crest: {result.freeboard_ft:.3f} ft")
    for s in result.scenarios[:1]:  # all three identical for a single-storm sim
        htc = f"{s.hours_to_crest:.1f} h" if s.hours_to_crest is not None else "—"
        typer.echo(f"  peak elevation    : {s.peak_elevation:.3f} ft   hours_to_crest {htc}")


@app.command()
def pull(
    out: str = typer.Option(str(REPO_ROOT / "fixtures" / "ha_snapshot.json")),
    artifact: str = typer.Option(None),
):
    """Snapshot live HA data into a fixture (requires HA_URL + HA_TOKEN env vars).

    Uses the same wire format every other command and the API consume. Falls back with
    a clear message if credentials are absent."""
    from .settings import ha_config_from_env
    from .sources.live_ha import HAConfig, LiveHASource

    art = _art(artifact)
    ha = ha_config_from_env()
    if ha is None:
        default_fc = HAConfig(base_url="", token="").forecast_entity
        typer.echo("Set HA_URL and HA_TOKEN to pull live. "
                   f"Default forecast source is Apple WeatherKit ({default_fc}).")
        raise typer.Exit(code=1)
    snap = LiveHASource(art, ha).fetch_snapshot()
    atomic_write_text(out, snap.model_dump_json(indent=2))
    typer.echo(f"Pulled live HA snapshot -> {out}  "
               f"(reading {snap.lake_depth_reading_ft} ft, "
               f"{len(snap.forecast_point_in)} h forecast, "
               f"stop_logs {snap.stop_log_count})")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
):
    """Run the stateless prediction API (uvicorn). Config via env (HA_URL, HA_TOKEN)."""
    import uvicorn
    uvicorn.run("lake_rise.api:app", host=host, port=port)


@app.command()
def alert(
    fixture: str = typer.Option(None, help="Evaluate a snapshot JSON instead of pulling live HA."),
    send: bool = typer.Option(False, "--send/--dry-run",
                              help="--send dispatches via the configured channels and persists "
                                   "state; --dry-run (default) prints to the console only."),
    force_test: bool = typer.Option(False, "--force-test",
                                    help="Fire a TEST notice immediately regardless of rain "
                                         "threshold or prior state. Useful for validating the "
                                         "end-to-end channel config."),
    artifact: str = typer.Option(None),
):
    """Evaluate the forecast and fire alerts that cross into a higher level.

    Dry-run by default: renders any notice to the console without sending or mutating
    the alert state, so you can test the pipeline safely. Use --send to go live."""
    from .alerting import alert_config_from_env, run_once

    art = _art(artifact)
    config = alert_config_from_env()
    bundle = FixtureSource(art, fixture).build_bundle() if fixture else None
    run = run_once(config, bundle=bundle, art=art, dry_run=not send, force_test=force_test)

    d = run.decision
    typer.echo(
        f"level={d.active_rank} ({d.active_level_name})  "
        f"P(early-warning)={d.probabilities.get('early_warning', 0):.0%}  "
        f"P(crest)={d.probabilities.get('dam_crest', 0):.0%}  "
        f"P(bridge)={d.probabilities.get('bridge_deck', 0):.0%}  test={d.test_active}"
    )
    if not run.actions:
        typer.echo("No threshold crossing since the last run — nothing to send.")
    else:
        kinds = ", ".join(a.kind for a in run.actions)
        typer.echo(f"{'Sent' if send else 'Would send'}: {kinds}")


@app.command()
def alert_config(artifact: str = typer.Option(None)):
    """Show the alert escalation ladder, per-tier recipients, and config warnings.

    Pure inspection (no prediction): validates that every tier maps to a real threshold,
    routes to a non-empty audience, and that the enabled channels + templates are usable."""
    from .alerting import alert_config_from_env
    from .alerting.preview import summarize_config

    s = summarize_config(_art(artifact), alert_config_from_env())
    typer.echo("Alert tiers (escalation ladder; recipients are cumulative up the ladder):")
    for r in s.rows:
        elev = (f"{r.threshold_abs_ft:.2f}abs/{r.gauge_ft:.2f}gauge"
                if r.threshold_abs_ft is not None else "UNKNOWN")
        em = ", ".join(r.recipients.emails) or "(none)"
        sms = ", ".join(r.recipients.sms) or "(none)"
        typer.echo(f"  {r.rank}. {r.name:<9} {r.threshold_label:<13} {elev:<22} "
                   f"fire@P>={r.min_prob:.0%}  -> {r.audience}")
        typer.echo(f"        email[{em}]  sms[{sms}]")
    typer.echo(f"\nChannels: {', '.join(s.channels) or '(none)'}  "
               f"(SMTP {'configured' if s.smtp_configured else 'NOT configured'}, "
               f"Twilio {'configured' if s.twilio_configured else 'NOT configured'})")
    typer.echo(f"Test audience '{s.test_audience}': "
               f"email[{', '.join(s.test_recipients.emails) or '(none)'}]  "
               f"sms[{', '.join(s.test_recipients.sms) or '(none)'}]")
    if s.warnings:
        typer.echo("\nWarnings:")
        for w in s.warnings:
            typer.echo(f"  ! {w}")
    else:
        typer.echo("\nNo configuration warnings.")


@app.command()
def alert_preview(
    current_elev: float = typer.Option(..., help="Current lake elevation (absolute ft)."),
    preset: str = typer.Option(None, help="Storm preset key (see the `presets` command)."),
    historical: str = typer.Option(None, help="Historical storm id, e.g. h0 (see `historical`)."),
    rate: float = typer.Option(None, help="Constant rain rate in/hr (use with --duration)."),
    duration: int = typer.Option(None, help="Hours of rain for --rate."),
    stop_logs: int = typer.Option(3, min=0, max=3),
    month: int = typer.Option(1, min=1, max=12, help="Season (drives PET + band spread)."),
    start_offset_h: int = typer.Option(0, help="Dry-lead hours before the storm starts."),
    horizon_h: int = typer.Option(72),
    tier: str = typer.Option(None, help="Force-render this tier for the storm even if it doesn't fire."),
    out_dir: str = typer.Option(None, help="Write subject.txt/body.txt/body.html/sms.txt here."),
    email_self: bool = typer.Option(False, help="Also email the rendered notice to SMTP_USER (you). SMS is never sent."),
    artifact: str = typer.Option(None),
):
    """Generate the alert a what-if / historical storm would fire. Renders to the console
    (and --out-dir); never sends SMS, never mutates alert state."""
    from .alerting import alert_config_from_env
    from .alerting.channels import ConsoleNotifier
    from .alerting.preview import preview_storm, write_rendered, email_self as send_self

    art = _art(artifact)
    config = alert_config_from_env()
    pv = preview_storm(
        art, config, current_elevation_abs_ft=current_elev, stop_log_count=stop_logs,
        month=month, preset=preset, historical_id=historical, rate_in_per_hr=rate,
        duration_h=duration, start_offset_h=start_offset_h, horizon_h=horizon_h, force_tier=tier)
    d = pv.decision
    off = art.datum.sensor_to_absolute_offset_ft
    typer.echo(f"Forecast: total {d.forecast_total_in:.2f} in, peak {d.peak_elevation:.2f} ft "
               f"({d.peak_elevation - off:.2f} gauge), confidence {d.confidence_label}")
    typer.echo(f"P(early-warning)={d.probabilities.get('early_warning', 0):.0%}  "
               f"P(crest)={d.probabilities.get('dam_crest', 0):.0%}  "
               f"P(bridge)={d.probabilities.get('bridge_deck', 0):.0%}")
    typer.echo(f"Fires: level={d.active_rank} ({d.active_level_name or 'none'})\n")
    if pv.fired is None:
        typer.echo("No tier fires for this storm. Use --tier NAME to force-render a tier, "
                   "or use a stronger storm / higher --current-elev.")
        return
    ConsoleNotifier().send(pv.fired.alert, pv.fired.recipients)
    if out_dir:
        path = write_rendered(Path(out_dir), pv.fired.label, pv.fired.alert)
        typer.echo(f"\nWrote {path}/ (open body.html in a browser).")
    if email_self:
        to = send_self(config, pv.fired.alert)
        typer.echo(f"Emailed the rendered notice to {to} (SMS not sent).")


@app.command()
def alert_tiers(
    out_dir: str = typer.Option(None, help="Write each tier's files under out_dir/<TIER>/."),
    tier: str = typer.Option(None, help="Limit to a single tier by name."),
    email_self: bool = typer.Option(False, help="Email each rendered tier to SMTP_USER (you). SMS is never sent."),
    artifact: str = typer.Option(None),
):
    """Render every configured tier's email + SMS (plus TEST and ALL_CLEAR) from one
    synthetic high-risk decision, so you can eyeball all templates and routing at once."""
    from .alerting import alert_config_from_env
    from .alerting.channels import ConsoleNotifier
    from .alerting.preview import render_all_tiers, write_rendered, email_self as send_self

    art = _art(artifact)
    config = alert_config_from_env()
    tiers = render_all_tiers(art, config)
    if tier:
        tiers = [t for t in tiers if t.label.lower() == tier.lower()]
        if not tiers:
            typer.echo(f"No tier named '{tier}'.")
            raise typer.Exit(code=1)
    for t in tiers:
        typer.echo(f"\n##### TIER {t.label} (rank {t.rank}, kind {t.kind}) #####")
        ConsoleNotifier().send(t.alert, t.recipients)
        if out_dir:
            write_rendered(Path(out_dir), t.label, t.alert)
        if email_self:
            to = send_self(config, t.alert)
            typer.echo(f"  emailed to {to}")
    if out_dir:
        typer.echo(f"\nWrote {len(tiers)} tier(s) under {out_dir}/ (open each body.html).")


@app.command()
def alert_drill(
    send: bool = typer.Option(False, "--send/--dry-run",
                              help="--send dispatches via the configured channels and persists "
                                   "the drill date; --dry-run (default) prints to console only."),
    artifact: str = typer.Option(None),
):
    """Send the monthly drill sequence: Advisory → Danger → Critical → Evac Notice → All Clear.

    All five messages are clearly labelled MONTHLY DRILL and go only to the drill audience
    (default: ops).  Dry-run by default — safe to call any time to preview the output.
    With --send the drill is recorded in alert_state.json; a second --send in the same
    calendar month is a no-op."""
    from .alerting import alert_config_from_env
    from .alerting.drill import run_drill, should_run_drill
    from .alerting.state import load_state

    art = _art(artifact)
    config = alert_config_from_env()

    if send:
        from datetime import timezone as _tz
        state = load_state(config.state_path)
        current_ym = datetime.now(_tz.utc).strftime("%Y-%m")
        if state.last_drill_ym == current_ym:
            typer.echo("Drill already sent this month (last_drill_ym matches). "
                       "Delete or edit alert_state.json to force a re-run.")
            raise typer.Exit(code=0)

    dispatched = run_drill(config, art=art, dry_run=not send)
    if not dispatched:
        typer.echo("No drill steps dispatched — check ALERT_DRILL_AUDIENCE and its recipient list.")
        raise typer.Exit(code=1)

    verb = "Sent" if send else "Would send"
    typer.echo(f"{verb} {len(dispatched)} drill steps: {', '.join(dispatched)}")


@app.command()
def params(
    param_class: str = typer.Option(None, "--class", help="Filter by provenance class "
                                     "(research|gauge-calibrated|reasoned|provisional|test-locked)."),
    tunable: bool = typer.Option(None, "--tunable/--not-tunable",
                                 help="Show only tunable (or only non-tunable) parameters."),
    set_: str = typer.Option(None, "--set", metavar="PATH=VALUE",
                             help="Write a new value for one tunable parameter (needs --out)."),
    out: str = typer.Option(None, "--out", help="Write the tuned artifact here (with --set)."),
    as_json: bool = typer.Option(False, "--json", help="Emit the listing as JSON."),
    artifact: str = typer.Option(None, help="Path to model artifact JSON."),
):
    """Inspect parameters by provenance, or write a tuned artifact.

    `lake-rise params --tunable` lists the parameters eligible for local fitting;
    `lake-rise params --set hspf.PERC_coeff=0.28 --out artifacts/crystal_lake_v1.json`
    writes a new artifact with that value (range/tunability-checked; never touches the
    canonical file). Then `lake-rise validate --artifact <out>` shows the anchor impact.
    """
    import json as _json

    from . import registry as R
    from .artifact import Artifact

    art_path = artifact or DEFAULT_ARTIFACT
    art = load_artifact(art_path)
    reg = R.load_registry()

    if set_ is not None:
        if not out:
            typer.echo("--set requires --out (writes a NEW artifact; never mutates the canonical one).")
            raise typer.Exit(code=1)
        if "=" not in set_:
            typer.echo("--set expects PATH=VALUE, e.g. hspf.PERC_coeff=0.28")
            raise typer.Exit(code=1)
        path, _, raw_val = set_.partition("=")
        path = path.strip()
        try:
            value: object = float(raw_val)
        except ValueError:
            value = raw_val.strip()
        try:
            R.check_write(reg, path, value)          # range / tunability / code / table gate
            old = R.get(art, path)
            R.set(art, path, value)                  # type-coerce/validate on the model
        except typer.Exit:
            raise
        except Exception as e:  # noqa: BLE001 -- surface any set/validate error as a clean CLI failure
            typer.echo(f"Refusing to write: {e}")
            raise typer.Exit(code=1)
        coerced = R.get(art, path)                    # properly-typed value
        # Write by editing the RAW JSON (preserves inline provenance comments), then re-load
        # as the final validation gate.
        raw = _json.loads(Path(art_path).read_text())
        node = raw
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = coerced
        raw["version"] = f"{raw.get('version', 'v0')}+tuned:{path}={coerced}"
        atomic_write_text(out, _json.dumps(raw, indent=2) + "\n")
        try:
            load_artifact(out)                        # final gate: must validate + load
        except Exception as e:  # noqa: BLE001
            Path(out).unlink(missing_ok=True)
            typer.echo(f"Wrote an invalid artifact; removed it: {e}")
            raise typer.Exit(code=1)
        typer.echo(f"{path}: {old} -> {coerced}   written to {out} (version {raw['version']})")
        typer.echo(f"Next: .venv/bin/lake-rise validate --artifact {out}")
        return

    rows = R.list_parameters(reg, art, cls=param_class, tunable=tunable)
    if as_json:
        typer.echo(_json.dumps(rows, indent=2))
        return
    if not rows:
        typer.echo("No parameters match the filter.")
        return
    typer.echo(f"{len(rows)} parameter(s):")
    for r in rows:
        flags = []
        if r["tunable"]:
            rng = f"[{r['min']}, {r['max']}]" if r.get("min") is not None else "(whole table)" if r.get("table") else ""
            flags.append(f"TUNABLE {rng}".rstrip())
        if r.get("location") == "code":
            flags.append("code-const")
        tag = f"  {'  '.join(flags)}" if flags else ""
        typer.echo(f"  [{r['class']:>16}] {r['path']} = {r['value']}{tag}")
        if r["tunable"] and r.get("couples_with"):
            typer.echo(f"                     couples_with: {', '.join(r['couples_with'])}")


@app.command()
def sweep(
    path: str = typer.Argument(..., help="Tunable parameter path, e.g. hspf.PERC_coeff "
                               "(see `lake-rise params --tunable`)."),
    steps: int = typer.Option(9, "--steps", help="Number of values across the range."),
    dir: str = typer.Option("data/backtest_storms", "--dir", help="Storm-record dataset directory."),
    artifact: str = typer.Option(None, help="Path to model artifact JSON."),
    as_json: bool = typer.Option(False, "--json", help="Emit the sweep as JSON."),
):
    """Sensitivity sweep: vary one tunable parameter across its range and, at each value,
    score the model against the stored storms and re-check the anchors.

    Shows how a parameter affects accuracy (RMSE / peak / timing over the dataset) and where
    it starts breaking the Step 6 / dry-equilibrium anchors. A 1-D sweep holds other
    parameters fixed -- watch the `couples_with` note for parameters that aren't independent.
    """
    import json as _json

    from . import storm_record as SR, sweep as SW
    from .registry import load_registry

    art = _art(artifact)
    reg = load_registry()
    records = SR.load_dataset(dir)
    try:
        result = SW.sweep_parameter(art, reg, records, path, steps=steps)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(code=1)

    if as_json:
        typer.echo(_json.dumps(result, indent=2))
        return

    typer.echo(f"Sweep {result['path']} over {result['range']} "
               f"({len(result['rows'])} values) against "
               f"{result['n_scored']}/{result['n_records']} scored storm(s):")
    if result["couples_with"]:
        typer.echo(f"  note: couples_with {', '.join(result['couples_with'])} "
                   f"(held fixed in this 1-D sweep)")
    if result["n_records"] == 0:
        typer.echo("  (no storm records found -- accuracy columns are blank; showing anchors only. "
                   "Capture storms with `lake-rise capture-storm`.)")
    elif result["n_scored"] == 0:
        typer.echo("  (storms loaded but NONE share predicted/actual hours -- accuracy columns are "
                   "blank; showing anchors only. Check the storm windows.)")
    typer.echo(f"  {'value':>9}  {'mRMSE':>7}  {'m|peak|':>7}  {'m|time|':>7}  anchors")
    for r in result["rows"]:
        mark = "*" if r["is_current"] else ("†" if r["is_prior"] else " ")
        rmse = f"{r['mean_rmse_ft']:.3f}" if r["mean_rmse_ft"] is not None else "  -  "
        pk = f"{r['mean_abs_peak_err_ft']:.3f}" if r["mean_abs_peak_err_ft"] is not None else "  -  "
        tm = f"{r['mean_abs_peak_timing_err_h']:.2f}" if r["mean_abs_peak_timing_err_h"] is not None else "  -  "
        anc = "PASS" if r.get("anchors_pass") else "FAIL"
        typer.echo(f"  {mark}{r['value']:>8.4g}  {rmse:>7}  {pk:>7}  {tm:>7}  {anc}")
    typer.echo("  (* = current value, † = research prior)")


@app.command()
def capture_storm(
    hours: int = typer.Option(..., "--hours", help="Window length to capture (T0 = now - hours)."),
    label: str = typer.Option(..., "--label", help="Short name for this storm record."),
    out: str = typer.Option(..., "--out", help="Where to write the StormRecord JSON."),
    notes: str = typer.Option("", "--notes", help="Free-text context for the record."),
    stop_logs: int = typer.Option(None, "--stop-logs", help="Stop-log count override (else seasonal)."),
    artifact: str = typer.Option(None, help="Path to model artifact JSON."),
):
    """Freeze a real storm window (live HA) into a StormRecord for offline backtesting.

    Requires HA_URL + HA_TOKEN. Capture promptly after a storm, while the ~10-day raw HA
    history still covers the window. Score it later with ``lake-rise backtest-offline``.
    """
    from . import storm_record as SR
    from .settings import ha_config_from_env
    from .sources.live_ha import LiveHASource

    art = _art(artifact)
    ha = ha_config_from_env()
    if ha is None:
        typer.echo("Set HA_URL and HA_TOKEN to capture a live storm window.")
        raise typer.Exit(code=1)
    record = LiveHASource(art, ha).capture_storm(hours_back=hours, label=label, notes=notes,
                                                 stop_log_count=stop_logs)
    SR.save(record, out)
    fresh = "" if record.data_fresh else "  WARNING: data not fresh over this window"
    typer.echo(f"Captured '{label}' ({hours} h, {len(record.rain_hourly)} rain h, "
               f"{len(record.level_by_hour)} gauge h) -> {out}{fresh}")
    typer.echo(f"Score it: .venv/bin/lake-rise backtest-offline {out}")


@app.command()
def backtest_offline(
    path: str = typer.Argument(..., help="A StormRecord JSON, or a directory of them "
                               "(e.g. data/backtest_storms)."),
    artifact: str = typer.Option(None, help="Path to model artifact JSON."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full result as JSON."),
):
    """Score an artifact against stored real-storm records, OFFLINE (no live HA).

    Replays each frozen storm through the pure backtest and reports predicted-vs-observed
    error (RMSE / peak / timing) per storm plus a dataset aggregate -- the objective a
    parameter sweep or auto-calibration minimises. Compare artifacts by re-running with
    ``--artifact`` (e.g. a tuned ``crystal_lake_v1.json`` from ``lake-rise params --set``).
    """
    import json as _json

    from . import storm_record as SR

    art = _art(artifact)
    p = Path(path)
    if not p.exists():
        typer.echo(f"No such path: {path}")
        raise typer.Exit(code=1)
    records = [SR.load(p)] if p.is_file() else SR.load_dataset(p)
    if not records:
        typer.echo(f"No StormRecord(s) found at {path}.")
        raise typer.Exit(code=1)

    result = SR.score_dataset(art, records)
    if as_json:
        typer.echo(_json.dumps(result, indent=2))
        return

    typer.echo(f"Scored {result['aggregate']['n_scored']}/{result['aggregate']['n_records']} "
               f"record(s) against model {art.version}:")
    for s in result["per_storm"]:
        stale = "" if s["data_fresh"] else "  (data not fresh)"
        if s.get("rmse_ft") is None:
            typer.echo(f"  {s['label']}: no overlapping hours{stale}")
            continue
        pk = "OK" if s["peak_within_target"] else "OUT"
        tm = "OK" if s["timing_within_target"] else "OUT"
        typer.echo(f"  {s['label']}: RMSE {s['rmse_ft']:.3f} ft   peak_err {s['peak_err_ft']:+.3f} ft "
                   f"[{pk}]   timing {s['peak_timing_err_h']:+.1f} h [{tm}]{stale}")
    a = result["aggregate"]
    typer.echo(f"  --- aggregate: mean RMSE {a['mean_rmse_ft']} ft   "
               f"mean |peak_err| {a['mean_abs_peak_err_ft']} ft   "
               f"mean |timing| {a['mean_abs_peak_timing_err_h']} h   "
               f"(peak within target {a['peak_within_target']}/{a['n_scored']}, "
               f"timing {a['timing_within_target']}/{a['n_scored']})")


calib_app = typer.Typer(add_completion=False, help="Automated signature-based calibration.")
app.add_typer(calib_app, name="calibration")


@calib_app.command("archive")
def calib_archive(
    artifact: str = typer.Option(None),
    days: int = typer.Option(None, "--days", help="Window to pull (default trailing_days); raise "
                             "for a one-shot reconcile that backfills more of HA's retention."),
):
    """Pull the live HA window and reconcile it into the continuous record (needs HA). Recoverable
    gaps are filled; genuinely-missing hours are preserved as missing."""
    from .calibration import archive
    from .settings import ha_config_from_env
    from .sources.live_ha import LiveHASource

    ha = ha_config_from_env()
    if ha is None:
        typer.echo("Set HA_URL and HA_TOKEN to archive live observations.")
        raise typer.Exit(code=1)
    samples = LiveHASource(_art(artifact), ha).continuous_samples(days=days)
    archive.append_samples(samples)
    rec = archive.load()                                  # full record, for the span report
    typer.echo(f"Archived {len(samples)} hours -> continuous record now spans "
               f"{rec.span_hours()} h ({len(rec.samples)} samples).")


@calib_app.command("train")
def calib_train(
    continuous: str = typer.Option(None, help="Continuous-record JSON path (default the archive)."),
    storms: str = typer.Option(None, help="Storm-record directory path for the safety veto."),
    email: bool = typer.Option(False, "--email", help="Email the proposal to CALIB_RECIPIENT."),
):
    """Extract signatures, propose a graded re-tuning, and print the report (nothing applies)."""
    from .calibration import report, service
    from .calibration.config import calibration_config_from_env

    cfg = calibration_config_from_env()
    candidate = service.run_training(cfg, continuous_path=continuous, storms_path=storms)
    typer.echo(report.render(candidate, cfg).text_body)
    if email:
        sent = report.email(candidate, cfg)
        typer.echo(f"Emailed to {sent}." if sent else "Email not configured (SMTP + CALIB_RECIPIENT).")


@calib_app.command("show")
def calib_show():
    """Show the pending proposal (if any)."""
    from .calibration import report
    from .calibration.config import calibration_config_from_env
    from .calibration.state import load_state

    cfg = calibration_config_from_env()
    state = load_state(cfg.state_path)
    if state.pending is None:
        typer.echo(f"No pending proposal. Active model: {state.active_version}.")
        return
    typer.echo(report.render(state.pending, cfg).text_body)


@calib_app.command("approve")
def calib_approve(candidate_id: str = typer.Argument(...),
                  token: str = typer.Option(..., "--token")):
    """Promote the pending proposal to a new active version (single-use token)."""
    from .calibration import service
    from .calibration.config import calibration_config_from_env

    cfg = calibration_config_from_env()
    try:
        version = service.approve(cfg, candidate_id, token)
    except ValueError as e:
        typer.echo(f"Refused: {e}")
        raise typer.Exit(code=1)
    typer.echo(f"Approved — active model is now {version}. Revert with "
               f"`lake-rise calibration revert v0`.")


@calib_app.command("reject")
def calib_reject(candidate_id: str = typer.Argument(...)):
    """Discard the pending proposal."""
    from .calibration import service
    from .calibration.config import calibration_config_from_env
    try:
        service.reject(calibration_config_from_env(), candidate_id)
    except ValueError as e:
        typer.echo(f"Refused: {e}")
        raise typer.Exit(code=1)
    typer.echo("Rejected.")


@calib_app.command("revert")
def calib_revert(version: str = typer.Argument(..., help="e.g. v0 or v2")):
    """Flip the active-version pointer to a prior version."""
    from .calibration import service
    from .calibration.config import calibration_config_from_env
    try:
        service.revert(calibration_config_from_env(), version)
    except Exception as e:  # noqa: BLE001
        typer.echo(f"Refused: {e}")
        raise typer.Exit(code=1)
    typer.echo(f"Active model reverted to {version}.")


@calib_app.command("status")
def calib_status(as_json: bool = typer.Option(False, "--json")):
    """Show the active version, any pending proposal, and the recent audit trail."""
    import json as _json

    from .calibration import service
    from .calibration.config import calibration_config_from_env

    st = service.status(calibration_config_from_env())
    if as_json:
        typer.echo(_json.dumps(st, indent=2))
        return
    typer.echo(f"Active model: {st['active_version']}")
    if st["pending"]:
        typer.echo(f"Pending: {st['pending']['id']} — {st['pending']['banner']}")
    for e in st["audit_tail"]:
        typer.echo(f"  {e['at']}  {e['action']:>8}  {e['version']}  {e['detail']}")


if __name__ == "__main__":
    app()
