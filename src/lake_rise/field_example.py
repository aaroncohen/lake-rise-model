"""The field view's example storm: a fixed ensemble storm already underway
(``presets.example_storm``), run through the same prediction, alert evaluation and summary as
/field, for showing the page to people. It reads nothing live and writes nothing: no Home
Assistant, alert state or forecast log."""

from __future__ import annotations

from datetime import datetime, timedelta

from . import forecast_log, model
from .alerting.rules import evaluate
from .artifact import Artifact
from .bundle import InputBundle, ScenarioRain
from .field import FieldSummary, summarize
from .geometry import control_elev_for_stop_logs
from .predict import predict
from .presets import (EXAMPLE_ELAPSED_H, EXAMPLE_PREVIOUS_SCALE, EXAMPLE_START_ELEVATION_ABS_FT,
                      EXAMPLE_STOP_LOGS, example_storm)


def _bundle(art: Artifact, as_of: datetime, scale: float, elapsed_h: int
            ) -> tuple[InputBundle, list[float]]:
    """The example ``elapsed_h`` hours into the storm, and the lake's hourly levels (abs ft) over
    the rain so far: the model's hindcast of it from the pre-storm level."""
    fallen, members = example_storm(scale, elapsed_h)
    _, records = model.hindcast(
        art, fallen, h0=EXAMPLE_START_ELEVATION_ABS_FT, start=as_of - timedelta(hours=len(fallen)),
        control_elev=control_elev_for_stop_logs(art, EXAMPLE_STOP_LOGS))
    levels = [r.h for r in records]
    bundle = InputBundle(
        as_of=as_of, current_elevation_abs_ft=levels[-1], stop_log_count=EXAMPLE_STOP_LOGS,
        trailing_rainfall_in=fallen, ensemble_cycle=as_of - timedelta(hours=6),
        ensemble_members=[ScenarioRain(name=f"member {i}", hourly_in=r)
                          for i, r in enumerate(members)])
    return bundle, levels


def example_summary(art: Artifact, cfg, as_of: datetime) -> FieldSummary:
    """The example storm as the field view's summary, as if forecast at ``as_of``: raining now,
    the lake rising. It carries ``example=True``; an hour-old smaller forecast gives it a "what
    changed"."""
    bundle, levels = _bundle(art, as_of, 1.0, EXAMPLE_ELAPSED_H)
    result = predict(bundle, art)
    before, _ = _bundle(art, as_of - timedelta(hours=1), EXAMPLE_PREVIOUS_SCALE,
                        EXAMPLE_ELAPSED_H - 1)
    previous = forecast_log.forecast_record(before, predict(before, art),
                                            artifact_version=art.version, art=art)
    options = [(k, predict(bundle.model_copy(update={"stop_log_count": k}), art))
               for k in range(bundle.stop_log_count - 1, -1, -1)]
    summary = summarize(
        result, bundle, evaluate(result, bundle, art, cfg), art, observation=None,
        trend_ft_per_h=levels[-1] - levels[-2], trend_was_ft_per_h=levels[-2] - levels[-3],
        rain_rate_in_per_h=bundle.trailing_rainfall_in[-1], gauge_problem=None,
        computed_at=as_of, previous=previous, options=options, gauge_age_min=1)
    return summary.model_copy(update={"example": True})
