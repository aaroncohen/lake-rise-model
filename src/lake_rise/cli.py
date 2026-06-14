"""lake-rise CLI: run the whole chain locally — pull (snapshot) -> hindcast ->
forecast -> validate, plus a simulate command for synthetic what-ifs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer

from . import model, sim
from .artifact import DEFAULT_ARTIFACT, load_artifact
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
    Path(out).write_text(snap.model_dump_json(indent=2))
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


if __name__ == "__main__":
    app()
