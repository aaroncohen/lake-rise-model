"""The spillway elevation drawing reads its heights from the artifact and the EAP table."""

from lake_rise.alerting.eap import EAP_LEVELS, EAP_POOL_ROWS
from lake_rise.spillway_drawing import leg_boards, render_svg


def test_each_stack_runs_from_the_bare_sill_to_the_summer_crest(art):
    sl = art.stop_logs
    for key, boards in leg_boards(art).items():
        leg = getattr(sl, key)
        assert abs(boards[0].bottom - leg.sill_stick_ft(sl.log_height_ft)) < 1e-9
        assert abs(boards[-1].top - leg.crest_stick_ft(sl.summer_count, sl.log_height_ft)) < 1e-9
        assert all(abs(a.top - b.bottom) < 1e-9 for a, b in zip(boards, boards[1:]))   # no gaps
        assert [b.added for b in boards] == sorted(b.added for b in boards)        # summer logs on top


def test_the_drawing_labels_the_crests_and_every_eap_level(art):
    svg = render_svg(art)
    assert "top 0.98" in svg and "top 0.99" in svg                    # the surveyed winter crests
    assert "mortared in place" in svg
    for ft in [lv.gauge_ft for lv in EAP_LEVELS] + [ft for ft, _ in EAP_POOL_ROWS]:
        assert f">{ft:.2f}<" in svg


def test_only_the_surveyed_heights_are_marked_measured(art):
    stacks = leg_boards(art)
    assert all(b.top_measured and b.height_measured for b in stacks["primary"] if not b.added)
    aux_log = next(b for b in stacks["auxiliary"] if not b.added)
    assert aux_log.top_measured and not aux_log.height_measured    # top surveyed, ~5 in log
    assert not any(b.top_measured for bs in stacks.values() for b in bs if b.added)
