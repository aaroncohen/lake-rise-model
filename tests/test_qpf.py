"""Ensemble rain forecasts: NBM / ECMWF point fetches (sources/nbm.py, ecmwf_ens.py over
grib_point.py), the per-cycle archive (qpf_archive.py) and scoring against the gauge
(qpf_verify.py). No network: an httpx MockTransport serves the files, and decoding is replaced
by reading a number out of the "message" bytes."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from lake_rise import qpf_archive, qpf_verify
from lake_rise.sources import ecmwf_ens, grib_point, nbm

C0 = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fast_and_fake_decode(monkeypatch):
    monkeypatch.setattr(grib_point, "MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(grib_point, "BACKOFF_S", 0.0)
    # A fake "GRIB message" is just its value as text: decode returns it.
    monkeypatch.setattr(grib_point, "decode_point", lambda b, lat, lon: float(b.decode()))


def _served(files: dict[str, bytes], fail_first: set[str] = frozenset()):
    """A client whose server returns slices of ``files`` for Range requests; 404 otherwise.
    Paths in ``fail_first`` answer 503 once before working."""
    failed = set()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in fail_first and url not in failed:
            failed.add(url)
            return httpx.Response(503)
        body = files.get(url)
        if body is None:
            return httpx.Response(404)
        rng = request.headers.get("Range")
        if rng:
            a, _, b = rng.removeprefix("bytes=").partition("-")
            body = body[int(a): int(b) + 1 if b else None]
        return httpx.Response(206 if rng else 200, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _nbm_file(values_mm: dict[tuple[str, int], float]) -> tuple[bytes, str]:
    """A GRIB "file" of one message per (accumulation, percentile) and its wgrib2-style idx."""
    body, idx = b"", []
    for n, ((acc, p), v) in enumerate(values_mm.items(), start=1):
        idx.append(f"{n}:{len(body)}:d=2026111012:APCP:surface:{acc}:{p}% level:prob fcst 255/255")
        body += f"{v:010.3f}".encode()
    idx.append(f"{len(values_mm) + 1}:{len(body)}:d=2026111012:TMP:2 m above ground:24 hour fcst:")
    body += b"9999999999"
    return body, "\n".join(idx) + "\n"


def _nbm_server(scale: float = 1.0, fail_first=frozenset()):
    files = {}
    for lead0, lead1, fh, acc in nbm.PERIODS:
        if nbm.file_url(C0, fh) in files:
            continue
        accs = [a for _, _, f, a in nbm.PERIODS if f == fh]
        vals = {(a, p): scale * 25.4 * p / 100 * (2 if "0-3" in a else 1)
                for a in accs for p in nbm.PERCENTILES}
        body, idx = _nbm_file(vals)
        files[nbm.file_url(C0, fh)] = body
        files[nbm.file_url(C0, fh) + ".idx"] = idx.encode()
    return _served(files, fail_first)


def test_nbm_idx_gives_each_message_its_byte_span():
    body, idx = _nbm_file({("0-1 day acc fcst", 50): 1.0, ("0-1 day acc fcst", 90): 2.0})
    msgs = nbm.parse_idx(idx)
    assert [(m.offset, m.length) for m in msgs] == [(0, 10), (10, 10), (20, None)]
    assert set(nbm.select(msgs, "0-1 day acc fcst")) == {50, 90}
    assert nbm.select(msgs, "1-2 day acc fcst") == {}


def test_nbm_cycle_is_each_days_and_the_3_day_percentiles_in_inches():
    rec = nbm.fetch_cycle(C0, 47.78, -122.11, c=_nbm_server())
    assert rec["source"] == "nbm" and rec["cycle"] == C0.isoformat()
    assert [(p["lead_h"], p["hours"]) for p in rec["periods"]] == [(0, 24), (24, 24), (48, 24),
                                                                    (0, 72)]
    day1, total = rec["periods"][0], rec["periods"][3]
    assert day1["percentiles_in"]["50"] == pytest.approx(0.5)      # 12.7 mm
    assert total["percentiles_in"]["90"] == pytest.approx(1.8)


def test_an_unpublished_nbm_cycle_is_none():
    assert nbm.fetch_cycle(C0, 47.78, -122.11, c=_served({})) is None


def test_throttled_requests_are_retried():
    url = nbm.file_url(C0, 24) + ".idx"
    rec = nbm.fetch_cycle(C0, 47.78, -122.11, c=_nbm_server(fail_first={url}))
    assert rec is not None


def _ecmwf_server(cum_mm_by_step: dict[int, list[float]], base: str):
    files = {}
    for step, members in cum_mm_by_step.items():
        body, idx = b"", []
        for n, v in enumerate(members, start=1):
            idx.append({"param": "2t", "type": "pf", "number": str(n), "_offset": len(body),
                        "_length": 10})
            body += b"0000000000"
            idx.append({"param": "tp", "type": "pf", "number": str(n), "_offset": len(body),
                        "_length": 10})
            body += f"{v / 1000:010.6f}".encode()                 # metres
        url = ecmwf_ens.file_url(C0, step, base)
        files[url] = body
        files[url.replace(".grib2", ".index")] = "\n".join(json.dumps(x) for x in idx).encode()
    return files


def test_ecmwf_members_cumulative_rain_becomes_daily_and_3_day_totals():
    files = _ecmwf_server({24: [25.4, 0.0], 48: [50.8, 25.4], 72: [50.8, 76.2],
                           96: [76.2, 76.2]}, ecmwf_ens.MIRRORS[0])
    rec = ecmwf_ens.fetch_cycle(C0, 47.78, -122.11, c=_served(files))
    assert rec["members"] == [1, 2]
    by = {(p["lead_h"], p["hours"]): p["members_in"] for p in rec["periods"]}
    assert by[(0, 24)] == [1.0, 0.0]
    assert by[(24, 24)] == [1.0, 1.0]
    assert by[(48, 24)] == [0.0, 2.0]
    assert by[(72, 24)] == [1.0, 0.0]
    assert by[(0, 72)] == [2.0, 3.0]


def test_ecmwf_falls_back_to_the_next_mirror():
    files = _ecmwf_server({24: [1.0], 48: [2.0], 72: [3.0], 96: [4.0]}, ecmwf_ens.MIRRORS[1])
    rec = ecmwf_ens.fetch_cycle(C0, 47.78, -122.11, c=_served(files))
    assert rec is not None and rec["periods"][-1]["members_in"] == [pytest.approx(3 / 25.4,
                                                                                 abs=1e-3)]


def test_bilinear_interpolates_between_grid_points():
    import numpy as np

    values = np.array([[0.0, 1.0], [2.0, 3.0]])
    lats, lons = np.array([48.0, 47.75]), np.array([237.75, 238.0])       # north-to-south rows
    v = grib_point._bilinear(values, lats, lons, 47.875, 237.875)
    assert v == pytest.approx(1.5)


# --- archive ---------------------------------------------------------------------------------

def _rec(source, cycle):
    return {"source": source, "cycle": cycle.isoformat(), "periods": []}


def test_update_fills_missing_cycles_newest_first_and_skips_stored(tmp_path):
    now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    since = datetime(2026, 11, 10, tzinfo=timezone.utc)
    seen = []

    def fetch(c):
        seen.append(c)
        return _rec("nbm", c)

    stats = qpf_archive.update("nbm", fetch, since=since, max_cycles=2, now=now,
                               directory=tmp_path)
    # 8 h publish lag: the newest due cycle is Nov 12 00Z
    assert seen == [datetime(2026, 11, 12, 0, tzinfo=timezone.utc),
                    datetime(2026, 11, 11, 12, tzinfo=timezone.utc)]
    assert stats["stored"] == 2 and stats["remaining"] == 3
    seen.clear()
    qpf_archive.update("nbm", fetch, since=since, now=now, directory=tmp_path)
    assert len(seen) == 3                                      # only the ones still missing
    assert len(qpf_archive.load("nbm", tmp_path)) == 5


def test_a_cycle_never_published_is_given_up_after_a_week(tmp_path):
    now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    since = datetime(2026, 11, 1, tzinfo=timezone.utc)
    calls = []

    def fetch(c):
        calls.append(c)
        return None

    qpf_archive.update("nbm", fetch, since=since, now=now, directory=tmp_path)
    first = len(calls)
    calls.clear()
    qpf_archive.update("nbm", fetch, since=since, now=now, directory=tmp_path)
    # only the cycles younger than GIVE_UP_AFTER are tried again
    assert calls and all(now - c <= qpf_archive.GIVE_UP_AFTER for c in calls)
    assert len(calls) < first


def test_a_failing_cycle_does_not_stop_the_rest(tmp_path):
    now = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)

    def fetch(c):
        if c.hour == 0:
            raise httpx.ConnectError("boom")
        return _rec("ecmwf_ens", c)

    stats = qpf_archive.update("ecmwf_ens", fetch, since=datetime(2026, 11, 10,
                                                                   tzinfo=timezone.utc),
                               now=now, directory=tmp_path)
    assert stats["failed"] == 2 and stats["stored"] == 1


# --- scoring ---------------------------------------------------------------------------------

def _rain(start, hourly):
    return {start + timedelta(hours=i): v for i, v in enumerate(hourly)}


def test_ensemble_quantiles_from_members_and_from_percentiles():
    members = {"periods": [{"lead_h": 0, "hours": 24, "members_in": [0.0, 1.0, 2.0, 3.0, 4.0]}]}
    q = qpf_verify.ensemble_quantiles(members)[(0, 24)]
    assert q == {10: pytest.approx(0.4), 50: 2.0, 90: pytest.approx(3.6)}
    pct = {"periods": [{"lead_h": 0, "hours": 24,
                        "percentiles_in": {"10": 0.1, "50": 0.5, "90": 0.9}}]}
    assert qpf_verify.ensemble_quantiles(pct)[(0, 24)] == {10: 0.1, 50: 0.5, 90: 0.9}


def test_score_reports_bias_and_how_often_rain_beat_each_percentile():
    rain = _rain(C0, [0.05] * 24 * 4)                                  # 1.2 in a day
    fc = {"nbm": {C0: {(0, 24): {10: 0.2, 50: 0.6, 90: 1.0}}}}
    (row,) = qpf_verify.score(fc, rain)
    assert row["source"] == "nbm" and row["period"] == "day 1" and row["n"] == 1
    assert row["median_over_measured"] == 0.5
    assert row["at_or_below_q90"] == 0.0 and row["wet_above_q90"] == 1.0


def test_common_keeps_only_the_runs_every_source_has():
    rain = _rain(C0, [0.0] * 24 * 5)
    other = C0 + timedelta(hours=12)
    fc = {"nbm": {C0: {(0, 24): {10: 0, 50: 0, 90: 0}}, other: {(0, 24): {10: 0, 50: 0, 90: 0}}},
          "apple": {C0: {(0, 24): {50: 0.0}}}}
    rows = qpf_verify.score(fc, rain, common=True)
    assert all(r["n"] == 1 for r in rows)


def test_apple_totals_sum_the_logged_hourly_forecast():
    log = {"as_of": C0.isoformat(),
           "qpf_outlook": [{"datetime": (C0 + timedelta(hours=i)).isoformat(),
                            "precipitation_in": 0.01} for i in range(80)]}
    t = qpf_verify.apple_totals(log)
    assert t[(0, 24)] == pytest.approx(0.24) and t[(0, 72)] == pytest.approx(0.72)


def test_a_second_update_of_the_same_source_steps_aside(tmp_path):
    """A manual backfill and the hourly job must never fetch the same cycle twice."""
    now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    since = datetime(2026, 11, 11, tzinfo=timezone.utc)
    inner = []

    def fetch(c):
        if not inner:                     # while the first update holds the lock...
            inner.append(qpf_archive.update("nbm", lambda c: _rec("nbm", c), since=since,
                                            now=now, directory=tmp_path))
        return _rec("nbm", c)

    outer = qpf_archive.update("nbm", fetch, since=since, now=now, directory=tmp_path)
    assert inner == [{"skipped": "another update of this source is running"}]
    assert outer["stored"] == 3
    other = qpf_archive.update("ecmwf_ens", lambda c: None, since=since, now=now,
                               directory=tmp_path)
    assert "skipped" not in other                 # the lock is per source


def test_latest_is_the_newest_stored_cycle(tmp_path):
    assert qpf_archive.latest("nbm", tmp_path) is None
    for h in (0, 12):
        qpf_archive.save(_rec("nbm", datetime(2026, 11, 14, h, tzinfo=timezone.utc)), tmp_path)
    assert qpf_archive.latest("nbm", tmp_path)["cycle"] == "2026-11-14T12:00:00+00:00"
