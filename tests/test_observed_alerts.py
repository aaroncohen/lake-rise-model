"""Observed EAP crossing orchestration, routing, and delivery isolation."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from lake_rise.alerting import service
from lake_rise.alerting.config import Recipients
from lake_rise.alerting.state import AlertState, load_state, save_state
from lake_rise.observed import GaugeObservation


class FakeObservedSource:
    def __init__(self, observation, bundle_factory, art):
        self.observation = observation
        self.bundle_factory = bundle_factory
        self.art = art
        self.build_calls = 0

    def fetch_gauge_observation(self, now=None):
        return self.observation

    def build_bundle(self, lake_reading_ft=None):
        self.build_calls += 1
        bundle = self.bundle_factory(start=self.observation.detected_at)
        bundle.current_elevation_abs_ft = (
            lake_reading_ft + self.art.datum.sensor_to_absolute_offset_ft)
        return bundle


class RecordingNotifier:
    name = "recording"

    def __init__(self, fail_observed=False):
        self.fail_observed = fail_observed
        self.sent = []

    def send(self, alert, recipients):
        if self.fail_observed and "EAP THRESHOLD" in alert.subject:
            raise RuntimeError("observed channel failure")
        self.sent.append((alert, recipients))


def _config(make_alert_config, tmp_path):
    return make_alert_config(
        audiences={
            "ops": Recipients(emails=("ops@example.org",)),
            "emergency": Recipients(emails=("emergency@example.org",)),
            "road": Recipients(emails=("road@example.org",)),
            "evacuate": Recipients(emails=("evacuate@example.org",)),
        },
        state_path=tmp_path / "alert_state.json",
        channels=("email",),
    )


def test_observed_crossing_and_predictive_escalation_send_separately(
    art, make_bundle, make_alert_config, tmp_path,
):
    cfg = _config(make_alert_config, tmp_path)
    at = datetime(2026, 1, 15, 16, 5, tzinfo=timezone.utc)
    source = FakeObservedSource(GaugeObservation(at, 3.30, True, 3), make_bundle, art)
    notifier = RecordingNotifier()

    result = service.run_observed_once(cfg, art=art, source=source, notifiers=[notifier])

    assert {action.kind for action in result.actions} == {"LEVEL", "EAP_CROSSING"}
    assert len(notifier.sent) == 2
    observed = next(item for item in notifier.sent if "EAP THRESHOLD" in item[0].subject)
    assert set(observed[1].emails) == {"ops@example.org", "emergency@example.org"}
    state = load_state(cfg.state_path)
    assert state.level_rank > 0 and state.observed_eap_rank == 1

    # Same observed level is silent and does not fetch the expensive forecast again.
    again = service.run_observed_once(cfg, art=art, source=source, notifiers=[notifier])
    assert again.actions == [] and source.build_calls == 1 and len(notifier.sent) == 2


def test_observed_jump_is_one_notice_with_cumulative_routing(
    art, make_bundle, make_alert_config, tmp_path,
):
    cfg = _config(make_alert_config, tmp_path)
    at = datetime(2026, 1, 15, 16, 5, tzinfo=timezone.utc)
    source = FakeObservedSource(GaugeObservation(at, 4.45, True, 4), make_bundle, art)
    notifier = RecordingNotifier()

    result = service.run_observed_once(cfg, art=art, source=source, notifiers=[notifier])
    observed_actions = [action for action in result.actions if action.kind == "EAP_CROSSING"]
    assert len(observed_actions) == 1 and observed_actions[0].rank == 3
    message, recipients = next(item for item in notifier.sent if "EAP THRESHOLD" in item[0].subject)
    assert set(recipients.emails) == {
        "ops@example.org", "emergency@example.org", "road@example.org",
        "evacuate@example.org",
    }
    assert all(title in message.text_body for title in (
        "Mandatory Alert", "Bridge Closure", "Evacuate Downstream"))


def test_failed_observed_delivery_retries_without_repeating_predictive(
    art, make_bundle, make_alert_config, tmp_path,
):
    cfg = _config(make_alert_config, tmp_path)
    at = datetime(2026, 1, 15, 16, 5, tzinfo=timezone.utc)
    source = FakeObservedSource(GaugeObservation(at, 3.30, True, 3), make_bundle, art)
    first = RecordingNotifier(fail_observed=True)

    result = service.run_observed_once(cfg, art=art, source=source, notifiers=[first])
    assert {action.kind for action in result.actions} == {"LEVEL", "EAP_CROSSING"}
    state = load_state(cfg.state_path)
    assert state.level_rank > 0                 # predictive delivery committed
    assert state.observed_eap_rank == 0         # observed delivery remains armed

    retry = RecordingNotifier()
    result2 = service.run_observed_once(cfg, art=art, source=source, notifiers=[retry])
    assert [action.kind for action in result2.actions] == ["EAP_CROSSING"]
    assert len(retry.sent) == 1
    assert load_state(cfg.state_path).observed_eap_rank == 1


def test_non_crossing_poll_only_persists_debounce_state(
    art, make_bundle, make_alert_config, tmp_path,
):
    cfg = _config(make_alert_config, tmp_path)
    save_state(cfg.state_path, AlertState(observed_eap_rank=1))
    at = datetime(2026, 1, 15, 16, 5, tzinfo=timezone.utc)
    source = FakeObservedSource(GaugeObservation(at, 3.24, True, 3), make_bundle, art)

    result = service.run_observed_once(cfg, art=art, source=source, notifiers=[])
    assert result.actions == [] and source.build_calls == 0
    state = load_state(cfg.state_path)
    assert state.observed_eap_rank == 1 and state.observed_clear_since == at.isoformat()


def test_stale_observation_neither_fetches_forecast_nor_changes_state(
    art, make_bundle, make_alert_config, tmp_path,
):
    cfg = _config(make_alert_config, tmp_path)
    prior = AlertState(observed_eap_rank=1)
    save_state(cfg.state_path, prior)
    at = datetime(2026, 1, 15, 16, 5, tzinfo=timezone.utc)
    source = FakeObservedSource(
        GaugeObservation(at, None, False, 0, "gauge liveness is stale"), make_bundle, art)

    result = service.run_observed_once(cfg, art=art, source=source, notifiers=[])
    assert result.actions == [] and source.build_calls == 0
    assert load_state(cfg.state_path).observed_eap_rank == 1


def test_alert_entry_points_serialize_state_transactions(make_alert_config, monkeypatch):
    cfg = make_alert_config()
    guard = threading.Lock()
    active = 0
    most_active = 0

    def fake_run(*args, **kwargs):
        nonlocal active, most_active
        with guard:
            active += 1
            most_active = max(most_active, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return "ok"

    monkeypatch.setattr(service, "_run_once_unlocked", fake_run)
    # The live bundle is pulled before the lock (network I/O stays outside it).
    monkeypatch.setattr(service, "build_live_bundle", lambda art, config: object())
    monkeypatch.setattr(service, "_log_forecast", lambda *a: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.run_once(cfg, art=object()), range(2)))
    assert results == ["ok", "ok"] and most_active == 1


def test_a_forecast_outage_holds_the_ladder_instead_of_sending_all_clear(
    art, make_bundle, make_alert_config, tmp_path,
):
    """Review finding: an empty forecast collapsed every probability and sent ALL_CLEAR to the
    emergency audience mid-storm. With no forecast hours the run must hold every track."""
    cfg = _config(make_alert_config, tmp_path)
    save_state(cfg.state_path, AlertState(level_rank=4, level_name="DANGER", max_rank_reached=4))
    from lake_rise.bundle import ScenarioRain

    no_forecast = make_bundle()       # what a failed forecast fetch produces: zero-hour scenarios
    no_forecast.forecast_scenarios = [ScenarioRain(name=n, hourly_in=[]) for n in ("low", "median", "high")]
    notifier = RecordingNotifier()

    res = service.run_once(cfg, bundle=no_forecast, art=art, notifiers=[notifier])

    assert res.actions == [] and notifier.sent == []
    assert load_state(cfg.state_path).level_rank == 4               # held, not cleared


def test_observed_eap_page_still_goes_out_when_the_forecast_pipeline_fails(
    art, make_bundle, make_alert_config, tmp_path,
):
    """Review finding: an exception building the forecast bundle aborted the observed EAP page.
    The page now renders from a forecast-less fallback; the forecast ladder is left alone."""
    cfg = _config(make_alert_config, tmp_path)
    at = datetime(2026, 1, 15, 16, 5, tzinfo=timezone.utc)

    class BrokenForecastSource(FakeObservedSource):
        def build_bundle(self, lake_reading_ft=None):
            raise ValueError("forecast precipitation 'n/a' is not a number")

    source = BrokenForecastSource(GaugeObservation(at, 3.30, True, 3), make_bundle, art)
    notifier = RecordingNotifier()
    result = service.run_observed_once(cfg, art=art, source=source, notifiers=[notifier])

    assert [a.kind for a in result.actions] == ["EAP_CROSSING"]
    assert any("EAP THRESHOLD" in alert.subject for alert, _ in notifier.sent)
    state = load_state(cfg.state_path)
    assert state.observed_eap_rank == 1 and state.level_rank == 0    # ladder untouched
