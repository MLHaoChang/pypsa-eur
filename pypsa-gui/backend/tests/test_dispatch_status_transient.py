"""
`dispatch_status` must not report 'stale' for a solve's TRANSIENT rows.

A study on the live network (FMEA sweep, frontier, coupling loop) adds VOLL
slack generators (`__voll_<load>`) for each LP and removes them afterwards,
marking them transient first. `/api/simulation/status` reads lock-free, so a
poll inside that window saw the slack in `n.generators` but not in
`generators_t.p` and said 'stale' — about half the polls during an
eh_h2_hub FMEA sweep, which the Guided greeting then showed as "Solved
earlier, but the results are stale — the network changed since."
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.dispatch_status import dispatch_status, dispatch_status_detail


def _solved_net() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(3))
    n.add("Bus", "b")
    n.add("Load", "l", bus="b", p_set=1.0)
    n.add("Generator", "g", bus="b", p_nom=5.0)
    n.generators_t.p = pd.DataFrame({"g": [1.0, 1.0, 1.0]}, index=n.snapshots)
    n.loads_t.p = pd.DataFrame({"l": [1.0, 1.0, 1.0]}, index=n.snapshots)
    return n


def test_solved_network_is_fresh():
    assert dispatch_status(_solved_net(), transient={}) == "fresh"


def test_slack_added_mid_solve_is_not_stale():
    n = _solved_net()
    n.add("Generator", "__voll_l", bus="b", p_nom=10.0)   # the add window
    tr = {"Generator": {"__voll_l"}}
    assert dispatch_status(n, transient=tr) == "fresh"
    assert dispatch_status_detail(n, transient=tr) == {
        "state": "fresh", "mismatched_classes": []}


def test_slack_in_dispatch_but_gone_from_static_is_not_stale():
    n = _solved_net()
    n.generators_t.p["__voll_l"] = 0.0                  # the remove window
    assert dispatch_status(n, transient={"Generator": {"__voll_l"}}) == "fresh"


def test_a_real_edit_is_still_stale():
    n = _solved_net()
    n.add("Generator", "g2", bus="b", p_nom=1.0)
    assert dispatch_status(n, transient={"Generator": {"__voll_l"}}) == "stale"
    assert dispatch_status_detail(n, transient={})["mismatched_classes"] == ["generators"]


def test_default_reads_the_transient_registry():
    from services.pypsa_service import PyPSAService
    n = _solved_net()
    n.add("Generator", "__voll_l", bus="b", p_nom=10.0)
    assert dispatch_status(n) == "stale"      # unmarked → a real mismatch
    PyPSAService.mark_transient("Generator", "__voll_l")
    try:
        assert dispatch_status(n) == "fresh"
        assert dispatch_status_detail(n)["state"] == "fresh"
    finally:
        PyPSAService.unmark_transient("Generator", "__voll_l")
