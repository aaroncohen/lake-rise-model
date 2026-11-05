"""Lake evaporation from recorded weather: method choice, magnitudes, and the hourly mapping
the backtest uses."""

from datetime import datetime, timedelta, timezone

import pytest

from lake_rise import evaporation as EV, model
from lake_rise.calibration.archive import HourSample

_UTC = timezone.utc


def _day(start, *, air=60.0, raw=64.0, rh=60.0, wind=2.0, water=None, hours=24, swing=8.0):
    out = []
    for i in range(hours):
        t = start + timedelta(hours=i)
        a = air + swing / 2 * (1 if 18 <= i or i < 2 else -1)              # a warm and a cool half
        out.append(HourSample(hour=t.isoformat(), air_temp_f=a, air_temp_raw_f=raw, rh_pct=rh,
                              wind_mph=wind, water_temp_f=water))
    return out


def test_mass_transfer_when_the_lake_probe_covers_the_day_else_reference_et(art):
    d0 = datetime(2026, 10, 10, 7, tzinfo=_UTC)                            # local midnight (PDT)
    with_probe = EV.daily_lake_evaporation(art, _day(d0, air=54, raw=58, rh=68, water=58))
    (est, method), = with_probe.values()
    assert method == "mass_transfer" and 0.05 < est < 0.2                  # Oct-like rate
    without = EV.daily_lake_evaporation(art, _day(d0, air=54, raw=58, rh=68))
    assert next(iter(without.values()))[1] == "fao56_reference_et"


def test_a_warm_lake_under_cool_air_evaporates_faster_than_reference_et(art):
    """The fall heat-storage effect the 2026-11-04 entry found: water 2-3 C above the air."""
    d0 = datetime(2026, 10, 10, 7, tzinfo=_UTC)
    mt = next(iter(EV.daily_lake_evaporation(art, _day(d0, air=54, raw=58, rh=68, water=59)).values()))[0]
    et = next(iter(EV.daily_lake_evaporation(art, _day(d0, air=54, raw=58, rh=68)).values()))[0]
    assert mt > 1.5 * et


def test_days_with_too_few_hours_are_left_to_the_table(art):
    d0 = datetime(2026, 10, 10, 7, tzinfo=_UTC)
    assert EV.daily_lake_evaporation(art, _day(d0, hours=EV.MIN_HOURS_PER_DAY - 1)) == {}
    rates = EV.hourly_rates(art, _day(d0, hours=10), d0, 24)
    assert rates == [None] * 24


def test_hourly_rates_map_each_model_hour_to_its_local_day(art):
    d0 = datetime(2026, 10, 10, 7, tzinfo=_UTC)
    samples = _day(d0, water=58) + _day(d0 + timedelta(days=1), water=50)
    rates = EV.hourly_rates(art, samples, d0, 48)
    assert len(set(rates[:24])) == 1 and len(set(rates[24:])) == 1 and rates[0] > rates[24]


def test_model_uses_the_recorded_rate_where_given_and_the_table_elsewhere(art):
    st = model.initial_state(art, h0=338.9, month=11)
    start = datetime(2026, 11, 5, tzinfo=_UTC)
    _, table = model.run(art, st, [0.0] * 2, start, 339.383)
    _, given = model.run(art, st, [0.0] * 2, start, 339.383, lake_evap_in_per_day=[0.5, None])
    assert given[0].q_lake_evap_cfs > table[0].q_lake_evap_cfs
    # Same table rate in hour 2 (the area differs only by hour 1's slightly larger drawdown).
    assert given[1].q_lake_evap_cfs == pytest.approx(table[1].q_lake_evap_cfs, rel=1e-3)
