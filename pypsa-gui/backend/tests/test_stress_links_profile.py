"""
`profiles` stress entries accept a `links_p_max_pu` slot (Edge Investment Case
P1 WP1.4b, schema extension first).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.4b
design line. `services/adequacy/stress.py` knew `loads_p_set` and
`generators_p_max_pu` only; a firm-capacity-access (FCA) connection's
curtailment hours are a stress condition on the PoC LINK, so the schema gains a
Link slot with the same validate / mutate / undo discipline. It is a small,
disclosed extension, not a new kind.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.adequacy import stress as ST

N = 6


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N, freq="h"))
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "site", carrier="AC")
    n.add("Link", "import", bus0="grid", bus1="site", p_nom=10.0)
    n.add("Link", "shaped", bus0="grid", bus1="site", p_nom=10.0)
    n.links_t.p_max_pu["shaped"] = [0.5] * N
    return n


def _entry(**links):
    return {"id": "fca_import", "kind": "profiles", "frequency_per_year": 1.0,
            "links_p_max_pu": links or {"import": [1, 1, 0, 0, 1, 1]}}


def test_a_links_slot_validates_and_round_trips(tmp_path):
    ST.save_scenarios(tmp_path, [_entry()])
    assert ST.load_scenarios(tmp_path)[0]["links_p_max_pu"] == {"import": [1, 1, 0, 0, 1, 1]}


@pytest.mark.parametrize("bad", [
    {"import": []}, {"import": [1, "x"]}, {"import": [1, float("nan")]}, ["import"],
])
def test_a_malformed_links_slot_is_refused(tmp_path, bad):
    sc = _entry()
    sc["links_p_max_pu"] = bad
    with pytest.raises(ST.StressValidationError, match="links_p_max_pu"):
        ST.save_scenarios(tmp_path, [sc])


def test_link_series_must_match_the_other_series_lengths(tmp_path):
    sc = _entry()
    sc["loads_p_set"] = {"l": [1.0, 2.0]}
    with pytest.raises(ST.StressValidationError, match="length"):
        ST.save_scenarios(tmp_path, [sc])


def test_a_links_only_entry_is_ready_and_matched_to_the_horizon():
    assert ST._profiles_ready(_entry())
    assert ST._profiles_match_horizon(_entry(), N)
    assert not ST._profiles_match_horizon(_entry(), N + 1)


def test_mutate_sets_link_availability_and_undo_restores_it():
    n = _net()
    undo = ST._profiles_mutate(_entry(**{"import": [1, 1, 0, 0, 1, 1], "shaped": [0.2] * N}))(n)
    assert n.links_t.p_max_pu["import"].tolist() == [1, 1, 0, 0, 1, 1]
    assert n.links_t.p_max_pu["shaped"].tolist() == [0.2] * N
    undo()
    assert "import" not in n.links_t.p_max_pu.columns
    assert n.links_t.p_max_pu["shaped"].tolist() == [0.5] * N
    assert float(n.links.at["import", "p_max_pu"]) == 1.0


def test_mutate_refuses_a_wrong_length_without_partial_apply():
    n = _net()
    with pytest.raises(ST.StressValidationError, match="links_p_max_pu"):
        ST._profiles_mutate(_entry(**{"import": [1, 0]}))(n)
    assert "import" not in n.links_t.p_max_pu.columns


def test_a_missing_link_fails_the_scenario_closed():
    """WP1.4 review #6: a renamed PoC Link must not solve an unmutated network."""
    n = _net()
    with pytest.raises(ST.StressValidationError, match="link_missing"):
        ST._profiles_mutate(_entry(**{"ghost": [0.0] * N}))(n)
