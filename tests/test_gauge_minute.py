"""The day-file archive of minute gauge readings."""

from datetime import datetime, timedelta, timezone

from lake_rise import gauge_minute

_T = datetime(2026, 11, 14, 23, 58, tzinfo=timezone.utc)


def _rows(*minutes_and_states):
    return [(_T + timedelta(minutes=m), s) for m, s in minutes_and_states]


def test_append_then_load_round_trips_across_a_utc_midnight(tmp_path):
    rows = {"sensor.depth": _rows((0, "1.20"), (1, "unknown"), (2, "1.21"), (3, "1.22")),
            "sensor.rain": _rows((1, "0.0"))}
    gauge_minute.append(rows, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-11-14.json", "2026-11-15.json"]
    assert gauge_minute.load(_T, _T + timedelta(minutes=3), tmp_path) == rows


def test_overlapping_appends_keep_one_row_per_timestamp(tmp_path):
    gauge_minute.append({"sensor.depth": _rows((0, "1.20"), (1, "1.21"))}, tmp_path)
    gauge_minute.append({"sensor.depth": _rows((1, "1.21"), (2, "1.22"))}, tmp_path)
    loaded = gauge_minute.load(_T, _T + timedelta(minutes=2), tmp_path)
    assert loaded == {"sensor.depth": _rows((0, "1.20"), (1, "1.21"), (2, "1.22"))}
