"""The day-file archive of minute gauge readings."""

import threading
import time
from datetime import datetime, timedelta, timezone

from lake_rise import gauge_minute

_T = datetime(2026, 11, 14, 23, 58, tzinfo=timezone.utc)


def _rows(*minutes_and_states):
    return [(_T + timedelta(minutes=m), s) for m, s in minutes_and_states]


def test_append_then_load_round_trips_across_a_utc_midnight(tmp_path):
    rows = {"sensor.depth": _rows((0, "1.20"), (1, "unknown"), (2, "1.21"), (3, "1.22")),
            "sensor.rain": _rows((1, "0.0"))}
    gauge_minute.append(rows, tmp_path)
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["2026-11-14.json", "2026-11-15.json"]
    assert gauge_minute.load(_T, _T + timedelta(minutes=3), tmp_path) == rows


def test_overlapping_appends_keep_one_row_per_timestamp(tmp_path):
    gauge_minute.append({"sensor.depth": _rows((0, "1.20"), (1, "1.21"))}, tmp_path)
    gauge_minute.append({"sensor.depth": _rows((1, "1.21"), (2, "1.22"))}, tmp_path)
    loaded = gauge_minute.load(_T, _T + timedelta(minutes=2), tmp_path)
    assert loaded == {"sensor.depth": _rows((0, "1.20"), (1, "1.21"), (2, "1.22"))}


def test_an_append_that_overlaps_another_keeps_both_rows(tmp_path, monkeypatch):
    # the first append pauses between reading the day file and replacing it; the second starts then
    read, go = threading.Event(), threading.Event()
    real_read = gauge_minute._read

    def slow_read(path):
        out = real_read(path)
        if not read.is_set():
            read.set()
            go.wait(5)
        return out
    monkeypatch.setattr(gauge_minute, "_read", slow_read)
    first = threading.Thread(target=gauge_minute.append,
                             args=({"sensor.depth": _rows((0, "1.20"))}, tmp_path))
    second = threading.Thread(target=gauge_minute.append,
                              args=({"sensor.depth": _rows((1, "1.21"))}, tmp_path))
    first.start()
    read.wait(5)
    second.start()
    time.sleep(0.2)                    # unlocked, the second would read and write in this time
    go.set()
    first.join(5)
    second.join(5)
    loaded = gauge_minute.load(_T, _T + timedelta(minutes=1), tmp_path)
    assert loaded == {"sensor.depth": _rows((0, "1.20"), (1, "1.21"))}
