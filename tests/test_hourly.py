"""The shared raw→hourly primitives that the live source and the backtest both build on."""

from datetime import datetime, timedelta, timezone

from lake_rise.hourly import floor_hour, hour_grid, parse_ha_rows


def test_floor_hour_zeros_minute_second_micro():
    ts = datetime(2026, 7, 5, 14, 37, 41, 123456, tzinfo=timezone.utc)
    assert floor_hour(ts) == datetime(2026, 7, 5, 14, 0, 0, 0, tzinfo=timezone.utc)


def test_parse_ha_rows_parses_and_preserves_order():
    rows = [
        {"last_changed": "2026-07-05T14:10:00+00:00", "state": "1.5"},
        {"last_changed": "2026-07-05T15:10:00Z", "state": "2.0"},   # Z suffix normalized
    ]
    out = parse_ha_rows(rows)
    assert out == [
        (datetime(2026, 7, 5, 14, 10, tzinfo=timezone.utc), 1.5),
        (datetime(2026, 7, 5, 15, 10, tzinfo=timezone.utc), 2.0),
    ]


def test_parse_ha_rows_skips_bad_rows():
    rows = [
        {"last_changed": "2026-07-05T14:10:00Z", "state": "unavailable"},  # non-float value
        {"last_changed": "not-a-date", "state": "1.0"},                    # unparseable ts
        {"state": "3.0"},                                                   # missing ts key
        {"last_changed": None, "state": "4.0"},                            # None ts (TypeError→str)
        {"last_changed": "2026-07-05T16:00:00Z", "state": "9.0"},          # the one good row
    ]
    assert parse_ha_rows(rows) == [(datetime(2026, 7, 5, 16, 0, tzinfo=timezone.utc), 9.0)]


def test_parse_ha_rows_custom_ts_key():
    rows = [{"last_updated": "2026-07-05T14:00:00Z", "state": "1.0"}]
    assert parse_ha_rows(rows, ts_key="last_updated")[0][0] == datetime(
        2026, 7, 5, 14, 0, tzinfo=timezone.utc)


def test_hour_grid_contiguous_and_half_open():
    start = datetime(2026, 7, 5, 14, 40, tzinfo=timezone.utc)   # floors to 14:00
    end = datetime(2026, 7, 5, 17, 5, tzinfo=timezone.utc)      # floors to 17:00
    assert hour_grid(start, end) == [
        datetime(2026, 7, 5, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 5, 15, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 5, 16, 0, tzinfo=timezone.utc),
    ]


def test_hour_grid_empty_when_end_not_after_start():
    t = datetime(2026, 7, 5, 14, 40, tzinfo=timezone.utc)
    assert hour_grid(t, t) == []
    assert hour_grid(t, t - timedelta(hours=1)) == []
