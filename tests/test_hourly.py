"""The shared raw→hourly primitives that the live source and the backtest both build on."""

from datetime import datetime, timedelta, timezone

import pytest

from lake_rise.hourly import (
    floor_hour,
    hour_grid,
    parse_ha_rows,
    parse_ha_states,
    rain_hourly_from_daily_accumulator,
)


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


# --- daily-accumulator rain -------------------------------------------------------------

_T0 = datetime(2026, 11, 4, 22, 0, tzinfo=timezone.utc)


def _at(minutes):
    return _T0 + timedelta(minutes=minutes)


def test_parse_ha_states_keeps_unavailable_as_none_and_sorts():
    rows = [
        {"last_changed": "2026-11-04T22:30:00Z", "state": "unavailable"},
        {"last_changed": "2026-11-04T22:00:00Z", "state": "0.1"},
        {"last_changed": "bad", "state": "0.2"},
    ]
    assert parse_ha_states(rows) == [(_at(0), 0.1), (_at(30), None)]


def test_daily_accumulator_flat_value_is_dry_not_missing():
    # HA writes no rows while the value holds: 24 silent hours are observed-dry.
    out = rain_hourly_from_daily_accumulator([(_at(0), 0.4)], _T0, _T0 + timedelta(hours=24))
    assert out == [0.0] * 24


def test_daily_accumulator_sums_within_hour_and_handles_midnight_reset():
    states = [(_at(0), 0.30), (_at(10), 0.40), (_at(50), 0.55),       # +0.25 in hour 0
              (_at(65), 0.0), (_at(90), 0.05)]                         # reset, then +0.05
    out = rain_hourly_from_daily_accumulator(states, _T0, _T0 + timedelta(hours=3))
    assert out == [pytest.approx(0.25), pytest.approx(0.05), 0.0]


def test_daily_accumulator_unavailable_hours_are_missing_and_recovery_is_not_rain():
    states = [(_at(0), 0.10), (_at(70), None), (_at(190), 0.60), (_at(250), 0.62)]
    out = rain_hourly_from_daily_accumulator(states, _T0, _T0 + timedelta(hours=5))
    assert out[0] == 0.0
    assert out[1:4] == [None, None, None]             # unavailable from 23:10 through the 01:10 recovery
    assert out[4] == pytest.approx(0.02)              # measured from the recovered baseline


def test_daily_accumulator_no_baseline_is_missing():
    # No reading at or before the window start: the first hours can't be measured.
    states = [(_at(90), 0.2), (_at(150), 0.3)]
    out = rain_hourly_from_daily_accumulator(states, _T0, _T0 + timedelta(hours=3))
    assert out == [None, None, pytest.approx(0.1)]
