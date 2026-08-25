from datetime import datetime, timedelta, timezone

import pytest

import lake_rise.settings as settings
from lake_rise.artifact import load_artifact


@pytest.fixture
def art():
    return load_artifact()


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Keep tests isolated from a developer's .env or exported HA creds."""
    monkeypatch.setattr(settings, "_DOTENV_LOADED", True)  # _load_dotenv_once -> no-op
    for k in ("HA_URL", "HA_TOKEN", "LAKE_RISE_LAKE_SENSOR", "LAKE_RISE_RAIN_SENSOR",
              "LAKE_RISE_FORECAST_ENTITY", "LAKE_RISE_STOPLOG_HELPER", "LAKE_RISE_ARTIFACT"):
        monkeypatch.delenv(k, raising=False)
    # Also clear any ALERT_* so alerting tests see a clean, default config.
    import os
    for k in [k for k in os.environ if k.startswith(("ALERT_", "SMTP_", "TWILIO_"))]:
        monkeypatch.delenv(k, raising=False)


# --- alerting test factories ---------------------------------------------------

@pytest.fixture
def make_prediction():
    """Build a synthetic PredictionResult with controllable probabilities, crossing
    times, and a median trajectory whose peak lands at a chosen hour."""
    from lake_rise.predict import (
        PredictionResult, ScenarioResult, ThresholdProbability, TrajectoryPoint,
    )

    def _scn(name, start, horizon, peak, peak_hour, htew, htc, htb):
        traj = []
        for i in range(horizon + 1):
            # Linear rise to the peak at peak_hour, then hold.
            frac = min(i, peak_hour) / peak_hour if peak_hour else 1.0
            elev = peak if i >= peak_hour else peak * frac
            traj.append(TrajectoryPoint(valid_at=start + timedelta(hours=i), elevation=elev))
        return ScenarioResult(name=name, trajectory=traj, peak_elevation=peak,
                              hours_to_crest=htc, hours_to_early_warning=htew,
                              hours_to_bridge_deck=htb)

    def build(*, p_ew=0.0, p_crest=0.0, p_bridge=0.0, start=None, horizon=72,
              current=339.0, freeboard=3.2,
              median_peak=340.0, high_peak=341.0, peak_hour=5,
              median_htew=None, median_htc=None, median_htb=None,
              high_htew=None, high_htc=None, high_htb=None,
              data_fresh=True):
        start = start or datetime(2026, 1, 15, tzinfo=timezone.utc)
        scenarios = [
            _scn("low", start, horizon, median_peak - 0.5, peak_hour, None, None, None),
            _scn("median", start, horizon, median_peak, peak_hour, median_htew, median_htc, median_htb),
            _scn("high", start, horizon, high_peak, peak_hour, high_htew, high_htc, high_htb),
        ]
        return PredictionResult(
            generated_at=start, model_version="test", horizon_hours=horizon,
            current_elevation=current, freeboard_ft=freeboard,
            hours_to_crest_high_scenario=high_htc, p_cross_341=p_ew, p_cross_crest=p_crest,
            p_cross_bridge_deck=p_bridge,
            data_fresh=data_fresh, scenarios=scenarios,
            threshold_probabilities=[
                ThresholdProbability(threshold_abs_ft=341.0, label="early_warning",
                                     p_cross_within_horizon=p_ew),
                ThresholdProbability(threshold_abs_ft=342.2, label="dam_crest",
                                     p_cross_within_horizon=p_crest),
                ThresholdProbability(threshold_abs_ft=342.7, label="bridge_deck",
                                     p_cross_within_horizon=p_bridge),
            ],
            input_summary={}, factors=None,
        )

    return build


@pytest.fixture
def make_bundle():
    """Build an InputBundle whose median forecast totals a chosen amount of rain."""
    from lake_rise.bundle import InputBundle, ScenarioRain

    def build(*, total_in=0.0, horizon=72, start=None):
        start = start or datetime(2026, 1, 15, tzinfo=timezone.utc)
        series = [0.0] * horizon
        if total_in:
            series[3] = total_in  # one wet hour
        scn = [ScenarioRain(name=n, hourly_in=series) for n in ("low", "median", "high")]
        return InputBundle(as_of=start, current_elevation_abs_ft=339.0,
                           stop_log_count=3, forecast_scenarios=scn)

    return build


@pytest.fixture
def make_alert_config():
    """Build an AlertConfig directly (no env), with overridable fields."""
    from pathlib import Path

    from lake_rise.alerting.config import (
        AlertConfig, Recipients, SMTPConfig, TwilioConfig, _parse_levels, _DEFAULT_LEVELS,
    )

    def build(*, audiences=None, test_enabled=False, test_rain_in=0.10,
              monthly_test_enabled=False, monthly_test_dom=1, monthly_test_hour=7,
              monthly_test_audience="ops",
              send_all_clear=True, state_path=None, channels=("email", "sms"),
              ui_base_url="http://nas.local:8077", smtp=None, twilio=None, levels=None):
        return AlertConfig(
            enabled=True, interval_minutes=60, observed_interval_minutes=5, horizon_hours=72,
            timezone="America/Los_Angeles",
            levels=levels or _parse_levels(_DEFAULT_LEVELS),
            audiences=audiences or {},
            test_enabled=test_enabled, test_rain_in=test_rain_in, test_audience="test",
            monthly_test_enabled=monthly_test_enabled, monthly_test_dom=monthly_test_dom,
            monthly_test_hour=monthly_test_hour, monthly_test_audience=monthly_test_audience,
            drill_enabled=False, drill_dom=1, drill_audience="ops",
            template_dir=None, send_all_clear=send_all_clear,
            state_path=state_path or Path("/tmp/lake_rise_test_state.json"),
            channels=tuple(channels), ui_base_url=ui_base_url,
            smtp=smtp or SMTPConfig("", 587, None, None, "", True),
            twilio=twilio or TwilioConfig(None, None, None),
        )

    return build
