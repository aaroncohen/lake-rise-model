"""lake-rise CLI: run the whole chain locally — pull (snapshot) -> hindcast ->
forecast -> validate, plus a simulate command for synthetic what-ifs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer

from . import model, sim
from .artifact import DEFAULT_ARTIFACT, load_artifact
from .geometry import control_elev_for_stop_logs, default_stop_log_count
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
    typer.echo(f"  P(cross {art.thresholds_abs_ft.dam_crest:.1f} crest)        : {result.p_cross_crest:.0%}")
    typer.echo("  scenarios:")
    for s in result.scenarios:
        htc = f"{s.hours_to_crest:.1f} h" if s.hours_to_crest is not None else "—"
        typer.echo(f"    {s.name:>6}: peak {s.peak_elevation:.3f} ft   hours_to_crest {htc}")


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
    example: bool = typer.Option(False, "--example", help="Write the synthetic example snapshot."),
):
    """Snapshot HA data into a fixture.

    This milestone uses snapshots written via the MCP tools (see fixtures/). A live
    HA REST client (/api/history, statistics, weather.get_forecasts) is the infra
    followup and will write this same schema. ``--example`` regenerates the bundled
    synthetic snapshot so the other commands run with no HA access."""
    if not example:
        typer.echo("Live HA pull is deferred to the infra followup. For now, use the "
                   "MCP-snapshotted fixture or run with --example.")
        raise typer.Exit(code=0)

    art = load_artifact()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stop_logs = default_stop_log_count(art.stop_logs, now.month, now.day)
    snapshot = {
        "as_of": now.isoformat(),
        "lake_depth_reading_ft": 1.41,
        "stop_log_count": stop_logs,
        "trailing_rainfall_in": [0.0] * 240,
        "rainfall_has_gaps": False,
        "forecast_point_in": [0.05] * 12 + [0.0] * 60,
        "forecast_pop_frac": [0.6] * 12 + [0.1] * 60,
        "noaa_high_total_in": None,
    }
    Path(out).write_text(json.dumps(snapshot, indent=2))
    typer.echo(f"Wrote synthetic snapshot -> {out}")


if __name__ == "__main__":
    app()
