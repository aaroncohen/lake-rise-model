"""The field view's example storm: a fixed ensemble storm (``presets.example_ensemble``) run
through the same prediction, alert evaluation and summary as /field, for showing the page to
people. It reads nothing live and writes nothing: no Home Assistant, alert state or forecast log."""

from __future__ import annotations

from datetime import datetime, timedelta

from . import forecast_log
from .alerting.rules import evaluate
from .artifact import Artifact
from .bundle import InputBundle, ScenarioRain
from .field import FieldSummary, summarize
from .predict import predict
from .presets import (EXAMPLE_PREVIOUS_SCALE, EXAMPLE_START_ELEVATION_ABS_FT,
                      EXAMPLE_STOP_LOGS, example_ensemble)


def _bundle(as_of: datetime, scale: float) -> InputBundle:
    members = [ScenarioRain(name=f"member {i}", hourly_in=r)
               for i, r in enumerate(example_ensemble(scale))]
    return InputBundle(as_of=as_of, current_elevation_abs_ft=EXAMPLE_START_ELEVATION_ABS_FT,
                       stop_log_count=EXAMPLE_STOP_LOGS, ensemble_members=members,
                       ensemble_cycle=as_of - timedelta(hours=6))


def example_summary(art: Artifact, cfg, as_of: datetime) -> FieldSummary:
    """The example storm as the field view's summary, as if forecast at ``as_of``. It carries
    ``example=True``; an hour-old smaller forecast gives it a "what changed"."""
    bundle = _bundle(as_of, 1.0)
    result = predict(bundle, art)
    before = _bundle(as_of - timedelta(hours=1), EXAMPLE_PREVIOUS_SCALE)
    previous = forecast_log.forecast_record(before, predict(before, art),
                                            artifact_version=art.version, art=art)
    options = [(k, predict(bundle.model_copy(update={"stop_log_count": k}), art))
               for k in range(bundle.stop_log_count - 1, -1, -1)]
    summary = summarize(result, bundle, evaluate(result, bundle, art, cfg), art,
                        observation=None, trend_ft_per_h=0.0, gauge_problem=None,
                        computed_at=as_of, previous=previous, options=options, gauge_age_min=1)
    return summary.model_copy(update={"example": True})
