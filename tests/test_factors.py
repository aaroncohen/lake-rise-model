"""Pure unit tests for factor_breakdown — no network calls, no API."""

from datetime import datetime, timezone

import pytest

from lake_rise import model
from lake_rise.factors import factor_breakdown


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _start():
    return datetime(2026, 4, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# basic structure and conservation
# ---------------------------------------------------------------------------

def test_factor_breakdown_lengths_match_records(art):
    h0 = 339.5
    control_elev = art.stop_logs.control_elev(3)
    start = _start()
    # 3 dry + 3 rain + 3 dry
    rain = [0.0] * 3 + [0.25] * 3 + [0.0] * 3
    state = model.initial_state(art, h0=h0, sm0=4.0, month=start.month)
    _, records = model.run(art, state, rain, start=start, control_elev=control_elev)

    fb = factor_breakdown(art, records, h0)

    n = len(records)
    assert len(fb["valid_at"]) == n
    assert len(fb["per_hour_ft"]["watershed_runoff"]) == n
    assert len(fb["per_hour_ft"]["direct_rain"]) == n
    assert len(fb["per_hour_ft"]["spillway"]) == n
    assert len(fb["cumulative_ft"]["watershed_runoff"]) == n
    assert len(fb["cumulative_ft"]["direct_rain"]) == n
    assert len(fb["cumulative_ft"]["spillway"]) == n
    assert len(fb["net_ft"]) == n
    assert len(fb["net_cumulative_ft"]) == n
    assert len(fb["state"]["soil_moisture_in"]) == n
    assert len(fb["state"]["soil_saturation_pct"]) == n
    assert len(fb["state"]["interflow_storage_in"]) == n
    assert len(fb["state"]["rain_in"]) == n


def test_factor_breakdown_net_equals_sum_of_parts(art):
    """per_hour parts sum exactly to net_ft (linear decomposition, no rounding in arrays)."""
    h0 = 339.5
    control_elev = art.stop_logs.control_elev(3)
    start = _start()
    rain = [0.0] * 2 + [0.3] * 4 + [0.0] * 2
    state = model.initial_state(art, h0=h0, sm0=4.5, month=start.month)
    _, records = model.run(art, state, rain, start=start, control_elev=control_elev)

    fb = factor_breakdown(art, records, h0)

    ph = fb["per_hour_ft"]
    for i in range(len(records)):
        total = (ph["watershed_runoff"][i] + ph["baseflow"][i]
                 + ph["direct_rain"][i] + ph["spillway"][i])
        assert total == pytest.approx(fb["net_ft"][i], abs=1e-6)


def test_factor_breakdown_net_ft_equals_dh_per_step(art):
    """net_ft[i] ≈ records[i].h - records[i-1].h (or h0 for i=0)."""
    h0 = 339.6
    control_elev = art.stop_logs.control_elev(3)
    start = _start()
    rain = [0.0] * 2 + [0.2] * 5 + [0.0] * 3
    state = model.initial_state(art, h0=h0, sm0=3.5, month=start.month)
    _, records = model.run(art, state, rain, start=start, control_elev=control_elev)

    fb = factor_breakdown(art, records, h0)

    for i, rec in enumerate(records):
        h_prev = h0 if i == 0 else records[i - 1].h
        expected_dh = rec.h - h_prev
        assert fb["net_ft"][i] == pytest.approx(expected_dh, abs=1e-6)


def test_factor_breakdown_net_cumulative_equals_total_rise(art):
    """net_cumulative_ft[-1] ≈ records[-1].h - h0."""
    h0 = 339.4
    control_elev = art.stop_logs.control_elev(3)
    start = _start()
    rain = [0.0] * 2 + [0.25] * 6 + [0.0] * 4
    state = model.initial_state(art, h0=h0, sm0=5.0, month=start.month)
    _, records = model.run(art, state, rain, start=start, control_elev=control_elev)

    fb = factor_breakdown(art, records, h0)

    assert fb["net_cumulative_ft"][-1] == pytest.approx(records[-1].h - h0, abs=1e-6)


# ---------------------------------------------------------------------------
# sign conventions
# ---------------------------------------------------------------------------

def test_factor_breakdown_signs(art):
    """runoff >= 0, direct_rain >= 0, spillway <= 0."""
    h0 = 339.5
    control_elev = art.stop_logs.control_elev(3)
    start = _start()
    rain = [0.2] * 12
    state = model.initial_state(art, h0=h0, sm0=5.0, month=start.month)
    _, records = model.run(art, state, rain, start=start, control_elev=control_elev)

    fb = factor_breakdown(art, records, h0)

    for i in range(len(records)):
        assert fb["per_hour_ft"]["watershed_runoff"][i] >= 0.0, f"runoff < 0 at step {i}"
        assert fb["per_hour_ft"]["direct_rain"][i] >= 0.0, f"direct_rain < 0 at step {i}"
        assert fb["per_hour_ft"]["spillway"][i] <= 0.0, f"spillway > 0 at step {i}"


# ---------------------------------------------------------------------------
# state diagnostics
# ---------------------------------------------------------------------------

def test_factor_breakdown_soil_saturation_pct(art):
    """soil_saturation_pct[i] == records[i].sm / LZSN * 100."""
    h0 = 339.5
    control_elev = art.stop_logs.control_elev(3)
    start = _start()
    rain = [0.1] * 8
    state = model.initial_state(art, h0=h0, sm0=3.0, month=start.month)
    _, records = model.run(art, state, rain, start=start, control_elev=control_elev)

    fb = factor_breakdown(art, records, h0)
    lzsn = art.hspf.LZSN_in

    for i, rec in enumerate(records):
        expected = rec.sm / lzsn * 100.0
        assert fb["state"]["soil_saturation_pct"][i] == pytest.approx(expected, abs=1e-4)


# ---------------------------------------------------------------------------
# totals
# ---------------------------------------------------------------------------

def test_factor_breakdown_totals_match_cumulative_ends(art):
    """totals_ft should equal the last entries in cumulative_ft."""
    h0 = 339.5
    control_elev = art.stop_logs.control_elev(3)
    start = _start()
    rain = [0.0] * 2 + [0.3] * 4 + [0.0] * 4
    state = model.initial_state(art, h0=h0, sm0=4.0, month=start.month)
    _, records = model.run(art, state, rain, start=start, control_elev=control_elev)

    fb = factor_breakdown(art, records, h0)

    assert fb["totals_ft"]["watershed_runoff"] == pytest.approx(
        fb["cumulative_ft"]["watershed_runoff"][-1], abs=1e-6
    )
    assert fb["totals_ft"]["direct_rain"] == pytest.approx(
        fb["cumulative_ft"]["direct_rain"][-1], abs=1e-6
    )
    assert fb["totals_ft"]["spillway"] == pytest.approx(
        fb["cumulative_ft"]["spillway"][-1], abs=1e-6
    )
    assert fb["totals_ft"]["net"] == pytest.approx(
        fb["net_cumulative_ft"][-1], abs=1e-6
    )


# ---------------------------------------------------------------------------
# empty records
# ---------------------------------------------------------------------------

def test_factor_breakdown_empty_records(art):
    fb = factor_breakdown(art, [], h0=339.5)

    assert fb["valid_at"] == []
    assert fb["per_hour_ft"]["watershed_runoff"] == []
    assert fb["per_hour_ft"]["direct_rain"] == []
    assert fb["per_hour_ft"]["spillway"] == []
    assert fb["cumulative_ft"]["watershed_runoff"] == []
    assert fb["net_ft"] == []
    assert fb["net_cumulative_ft"] == []
    assert fb["state"]["soil_moisture_in"] == []
    assert fb["state"]["soil_saturation_pct"] == []
    assert fb["state"]["interflow_storage_in"] == []
    assert fb["state"]["rain_in"] == []
    assert fb["totals_ft"]["watershed_runoff"] == 0.0
    assert fb["totals_ft"]["direct_rain"] == 0.0
    assert fb["totals_ft"]["spillway"] == 0.0
    assert fb["totals_ft"]["net"] == 0.0


# ---------------------------------------------------------------------------
# valid_at timestamps match records
# ---------------------------------------------------------------------------

def test_factor_breakdown_timestamps_match_records(art):
    h0 = 339.5
    control_elev = art.stop_logs.control_elev(3)
    start = _start()
    rain = [0.1] * 5
    state = model.initial_state(art, h0=h0, sm0=3.0, month=start.month)
    _, records = model.run(art, state, rain, start=start, control_elev=control_elev)

    fb = factor_breakdown(art, records, h0)

    for i, rec in enumerate(records):
        assert fb["valid_at"][i] == rec.t.isoformat()
