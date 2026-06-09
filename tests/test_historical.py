"""Historical storm catalog + hyetograph synthesis."""

from lake_rise import historical as H


def test_hyetograph_reproduces_nested_depths():
    # Long storm: 24-hr = 4.91 in, 72-hr = 6.85 in (Burlington 1945).
    s = H.build_hyetograph({24: 4.91, 72: 6.85})
    assert len(s) == 72
    assert abs(sum(s) - 6.85) < 1e-6                 # full storm total
    assert abs(sum(sorted(s, reverse=True)[:24]) - 4.91) < 1e-6   # most intense 24 h


def test_intermediate_depths_reproduced():
    s = H.build_hyetograph({6: 2.38, 18: 5.26})      # Seattle RG12 2007
    assert abs(sum(s) - 5.26) < 1e-6
    assert abs(sum(sorted(s, reverse=True)[:6]) - 2.38) < 1e-6


def test_all_storms_complete():
    assert len(H.all_storms()) == 82                 # full dataset retained for provenance


def test_curated_catalog_is_local_spread_and_sorted():
    cat = H.catalog()
    assert 8 <= len(cat) <= 20                        # thinned from 82
    totals = [c["total_in"] for c in cat]
    assert totals == sorted(totals, reverse=True)     # most severe first
    assert cat[0]["station"] == "Seattle RG12"        # 7.56 in / 72 h is still the worst
    for c in cat:
        assert c["region"] == 31                       # Puget lowland only
        assert c["distance_mi"] <= 40                   # near Woodinville
        assert H.hyetograph_for(c["id"])               # resolves to a hyetograph
    # spans a meaningful severity range, not a cluster
    assert max(totals) - min(totals) > 2.0
