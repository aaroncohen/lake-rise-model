"""Forecast-of-record log: what the live hourly alert run predicted, kept for later scoring."""

from datetime import datetime, timezone

from lake_rise import forecast_log
from lake_rise.alerting import service
from lake_rise.predict import predict


def test_record_round_trips_inputs_and_every_scenario(art, make_bundle, tmp_path):
    bundle = make_bundle(total_in=2.0)
    result = predict(bundle, art)
    rec = forecast_log.forecast_record(bundle, result, artifact_version=art.version, art=art,
                                       qpf_outlook=[{"datetime": "x", "precipitation_in": 0.1,
                                                     "pop_pct": 80}])
    path = forecast_log.append(rec, tmp_path)
    day = datetime.fromisoformat(rec["generated_at"]).astimezone(timezone.utc).strftime("%Y-%m-%d")
    assert path.name == f"{day}.jsonl"

    (got,) = forecast_log.load_day(day, tmp_path)
    assert set(got["scenarios"]) == {"low", "median", "high"}
    med = got["scenarios"]["median"]
    assert len(med["rain_hourly_in"]) == bundle.horizon_hours
    assert sum(med["rain_hourly_in"]) > 0
    assert len(med["elev_hourly_ft"]) == len(result.scenarios[1].trajectory)
    assert set(got["probabilities"]) == {"early_warning", "dam_crest", "bridge_deck"}
    assert got["current_elevation_ft"] == round(result.current_elevation, 3)
    assert got["qpf_outlook"][0]["pop_pct"] == 80
    assert got["spinup"] == result.input_summary["spinup"]
    assert got["spinup_history_days"] == result.input_summary["spinup_history_days"]
    assert got["spinup_baseflow_scale"] == result.input_summary["spinup_baseflow_scale"]
    assert got["nws_outlook"] is None                    # optional; recorded when fetched


def test_live_run_records_the_nws_outlook(art, make_bundle, make_alert_config, tmp_path,
                                          monkeypatch):
    from lake_rise.sources import nws

    cfg = _live(monkeypatch, make_bundle, make_alert_config, tmp_path)
    monkeypatch.setattr(nws, "qpf_outlook", lambda lat, lon: {"grid": "SEW/133,75", "qpf": []})
    service.run_once(cfg, art=art, notifiers=[])
    (path,) = _logged(tmp_path)
    assert '"nws_outlook":{"grid":"SEW/133,75"' in path.read_text()


def test_append_accumulates_lines_per_day(art, make_bundle, tmp_path):
    bundle = make_bundle()
    rec = forecast_log.forecast_record(bundle, predict(bundle, art), artifact_version="v0", art=art)
    forecast_log.append(rec, tmp_path)
    forecast_log.append(rec, tmp_path)
    day = datetime.fromisoformat(rec["generated_at"]).astimezone(timezone.utc).strftime("%Y-%m-%d")
    assert len(forecast_log.load_day(day, tmp_path)) == 2


def _live(monkeypatch, make_bundle, make_alert_config, tmp_path):
    """A 'live' scheduled run: run_once builds the bundle itself (patched, no HA)."""
    monkeypatch.setattr(service, "build_live_bundle", lambda art, config: make_bundle(total_in=1.0))
    return make_alert_config(state_path=tmp_path / "alert_state.json", channels=())


def _logged(tmp_path):
    return sorted((tmp_path / "forecasts").glob("*.jsonl"))


def test_live_run_logs_dry_run_and_supplied_bundle_do_not(art, make_bundle, make_alert_config,
                                                          tmp_path, monkeypatch):
    cfg = _live(monkeypatch, make_bundle, make_alert_config, tmp_path)
    service.run_once(cfg, art=art, notifiers=[], dry_run=True)
    service.run_once(cfg, bundle=make_bundle(), art=art, notifiers=[])
    assert _logged(tmp_path) == []

    service.run_once(cfg, art=art, notifiers=[])
    (path,) = _logged(tmp_path)
    (rec,) = [line for line in path.read_text().splitlines() if line]
    assert '"median"' in rec and '"qpf_outlook":null' in rec   # no HA -> outlook skipped


def test_a_failing_log_never_breaks_the_alert_run(art, make_bundle, make_alert_config,
                                                  tmp_path, monkeypatch):
    cfg = _live(monkeypatch, make_bundle, make_alert_config, tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(forecast_log, "append", boom)
    res = service.run_once(cfg, art=art, notifiers=[])
    assert res.decision is not None


def _at(h, m=0, day=14):
    return datetime(2026, 11, day, h, m, tzinfo=timezone.utc)


def _put(tmp_path, when, **extra):
    rec = {"generated_at": when.isoformat(), **extra}
    forecast_log.append(rec, tmp_path)
    return rec


def test_record_carries_level_percentages_and_peaks(art, make_bundle):
    bundle = make_bundle(total_in=2.0)
    result = predict(bundle, art)
    rec = forecast_log.forecast_record(bundle, result, artifact_version="v0", art=art)
    assert set(rec["levels_pct"]) == {"eap1", "early_warning", "eap2", "overtop", "bridge"}
    assert rec["middle_peak_ft"] == rec["scenarios"]["median"]["peak_elevation_ft"]
    assert rec["ensemble_peaks_ft"] == (
        [round(p, 3) for p in result.ensemble_peaks_ft] if result.ensemble_peaks_ft else None)


def test_previous_picks_the_newest_record_between_50_minutes_and_3_hours_old(tmp_path):
    _put(tmp_path, _at(8, 30), n="too old")
    _put(tmp_path, _at(9, 30), n="old")
    _put(tmp_path, _at(10, 30), n="newest in window")
    _put(tmp_path, _at(11, 30), n="too fresh")
    assert forecast_log.previous(_at(12), directory=tmp_path)["n"] == "newest in window"


def test_previous_picks_the_newest_not_the_last_written(tmp_path):
    _put(tmp_path, _at(11), n="newer")
    _put(tmp_path, _at(10), n="older")
    assert forecast_log.previous(_at(12), directory=tmp_path)["n"] == "newer"


def test_previous_skips_an_unparseable_line(tmp_path):
    _put(tmp_path, _at(11), n=1)
    path = tmp_path / "2026-11-14.jsonl"
    with path.open("a") as fh:
        fh.write("{not json\n{}\n")
    assert forecast_log.previous(_at(12), directory=tmp_path)["n"] == 1


def test_previous_is_none_when_only_fresher_or_older_records_exist(tmp_path):
    _put(tmp_path, _at(11, 30), n="fresher")      # 30 min old
    _put(tmp_path, _at(8), n="older")             # 4 h old
    assert forecast_log.previous(_at(12), directory=tmp_path) is None


def test_previous_looks_back_across_midnight_utc(tmp_path):
    _put(tmp_path, _at(23, day=13), n="yesterday")
    assert forecast_log.previous(_at(1), directory=tmp_path)["n"] == "yesterday"


def test_previous_skips_a_record_with_no_forecast(tmp_path):
    _put(tmp_path, _at(10), n="real", horizon_hours=72)
    _put(tmp_path, _at(11), n="outage", horizon_hours=0)
    assert forecast_log.previous(_at(12), directory=tmp_path)["n"] == "real"


def test_previous_skips_this_forecast_hours_own_record_at_55_past(tmp_path):
    current = _at(12)
    _put(tmp_path, _at(11, 5), n="previous hour", as_of=_at(11).isoformat())
    _put(tmp_path, _at(12, 5), n="this hour", as_of=current.isoformat())
    assert forecast_log.previous(_at(12, 55), before_as_of=current,
                                 directory=tmp_path)["n"] == "previous hour"
