"""No Comparison block may ship a figure without saying whether it resolved.

ADR-0001: zero is a legitimate result in an energy-system model, so an
unresolvable figure must never be indistinguishable from a real zero. Every
block therefore carries `available`, and `available=False` is the only way to
ship the default zeros.
"""
from __future__ import annotations

import inspect
import types

import pytest

from models import schemas
from tests import compare_support as cs
from tests.golden import fixture as gf


def _comparison_models():
    for name, obj in vars(schemas).items():
        if (
            inspect.isclass(obj)
            and name.endswith("Comparison")
            and hasattr(obj, "model_fields")
        ):
            yield name, obj


def test_every_comparison_block_declares_available():
    missing = [n for n, m in _comparison_models() if "available" not in m.model_fields]
    assert missing == [], (
        f"these Comparison blocks can ship a zero indistinguishable from a real "
        f"result: {missing}"
    )


def test_available_defaults_to_false():
    wrong = [
        n for n, m in _comparison_models()
        if m.model_fields["available"].default is not False
    ]
    assert wrong == [], (
        f"a default-constructed block is the early-return path and has resolved "
        f"nothing, so it must default to unavailable: {wrong}"
    )


def test_at_least_nine_blocks_are_covered():
    assert len(list(_comparison_models())) >= 9, (
        "the suite found fewer blocks than exist — the discovery filter is wrong"
    )


@pytest.fixture()
def golden_summary(reset_backend):
    """
    The nine Comparison blocks for the solved golden network, built by the
    same `_compute_*_summary` functions `get_results_summary` calls — see
    `tests/compare_support.py`. Wrapped in a `SimpleNamespace` so callers can
    use `golden_summary.economics` / `.capacity` etc., the same attribute
    names `ResultsSummary` uses for these fields.

    `reset_backend` (autouse, conftest.py) resets `PyPSAService` AND
    `solver_config` to a bare default both before and after every test;
    `install_golden` re-pins the golden discount rate / investment periods
    afterwards, the same trap `test_compare_endpoint.py`'s `golden_project`
    fixture documents for `install_network` — without the re-pin, every
    overnight_cost-priced asset's CAPEX resolves against the wrong discount
    rate.
    """
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return types.SimpleNamespace(**cs.summarise(n))


def test_solved_golden_project_reports_available(golden_summary):
    """The golden fixture is solved, so its populated blocks must say so."""
    assert golden_summary.economics.available is True
    assert golden_summary.capacity.available is True


# ── Negative direction (review Finding 3) ────────────────────────────────────
#
# Regression test for the CapacityComparison bug review Finding 1 caught:
# unlike the other eight `_compute_*_summary` functions, `_compute_capacity_
# summary` had no `has_solve` guard of its own and unconditionally reported
# `available=True` even though every one of its ten dicts was empty on an
# unsolved network — `p_nom_opt`, the column every capacity walk reads, is a
# PyPSA OUTPUT attribute that defaults to 0. before a solve, so every asset
# is skipped by the `opt <= 1e-9` check in `_walk_plain` / `_walk_vintages`,
# and `_compute_total_annuitised_capex` skips the same way. The assertion
# `capacity.available is True` in the test above is satisfied whether or not
# the network is solved, so it could not have caught this — this test can:
# it fails against the pre-fix code (see task-4-report.md for the captured
# RED run) because capacity reported `available=True` here regardless.

def test_unsolved_network_reports_every_block_as_unavailable():
    """An unsolved network has resolved nothing anywhere — every block must say so."""
    from services.dispatch_status import dispatch_status as classify_dispatch

    n = gf.build_golden_network()  # built, never solved
    assert classify_dispatch(n) != "fresh", (
        "premise of this test broke: the network must NOT look solved"
    )

    summary = cs.summarise(n)
    violations = {
        field: summary[field].available
        for field in cs.TAB_FIELDS
        if summary[field].available is not False
    }
    assert violations == {}, (
        f"an unsolved network resolved nothing, so every block must report "
        f"available=False; these did not: {violations}"
    )


# ── Genuinely-resolved zeros (review Finding 4) ──────────────────────────────
#
# Three early returns are NOT "resolved nothing": a solved network can be
# structurally empty of the thing a block measures (no renewable profile to
# curtail, no StorageUnit to cycle, no Generator at all), and the zero that
# falls out of that is a real answer, not an absence. Each site below is
# proven with BOTH directions on the same network shape — solved reports
# `True` with a genuine 0.0, the identical unsolved network reports `False`
# — so neither assertion is satisfiable by a flag that ignores the solve
# state (the same vacuousness Finding 3 flagged for the plain `is True`
# check above).
#
# Built locally rather than added to `compare_local_networks.py` — this
# task's file list is limited to `test_compare_availability.py` itself.

def _build_network_with_no_curtailable_capacity():
    """
    One bus, one thermal generator that serves the whole load (no
    time-varying `p_max_pu`, so it never gets a `generators_t.p_max_pu`
    column and never contributes to curtailment), plus one non-extendable
    renewable generator with an EXPLICIT but permanently-zero `p_max_pu`
    profile — present just so `generators_t.p_max_pu` is non-empty and the
    curtailment walk reaches its loop body instead of exiting on the
    "missing tables" early return (a DIFFERENT site this task leaves alone).
    Inside the loop the renewable's `available = p_max_pu × eff_cap` is 0 at
    every snapshot, so it's skipped too, leaving `curt_by_carrier` empty —
    exactly the `if not curt_by_carrier` branch Finding 4 names. No
    StorageUnit at all, so the identical network also exercises
    StorageCyclingComparison's `if sus.empty` branch.
    """
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b1", carrier="AC")
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Carrier", "wind")
    n.add(
        "Generator", "gas",
        bus="b1", carrier="gas",
        p_nom=100.0, marginal_cost=50.0,
    )
    n.add(
        "Generator", "wind",
        bus="b1", carrier="wind",
        p_nom=50.0, p_nom_extendable=False, marginal_cost=0.0,
        p_max_pu=[0.0, 0.0, 0.0, 0.0],
    )
    n.add("Load", "load1", bus="b1", p_set=20.0)
    return n


def test_curtailment_and_storage_cycling_are_real_zeros_when_structurally_empty_on_a_solved_network():
    n = _build_network_with_no_curtailable_capacity()
    n.optimize(solver_name="highs")

    summary = cs.summarise(n)

    curt = summary["curtailment"]
    assert curt.available is True, "solved network with a real zero must not read as unavailable"
    assert curt.total_gwh.total == 0.0
    assert curt.by_carrier_gwh == {}

    cyc = summary["storage_cycling"]
    assert cyc.available is True, "solved network with a real zero must not read as unavailable"
    assert cyc.cycles_by_carrier == {}
    assert cyc.by_unit == []


def test_curtailment_and_storage_cycling_are_unavailable_on_the_same_network_unsolved():
    """
    Same network shape as above, UNSOLVED — the distinguishing half: without
    a solve, both blocks fall back to `available=False`, proving the `True`
    above is conditioned on the solve rather than a blanket default for an
    empty network.
    """
    n = _build_network_with_no_curtailable_capacity()  # never optimized

    summary = cs.summarise(n)
    assert summary["curtailment"].available is False
    assert summary["storage_cycling"].available is False


def test_curtailment_is_a_real_zero_on_a_solved_network_with_no_generators_at_all():
    """
    Finding 4's third site: `_compute_curtailment_summary`'s `if gens.empty`
    branch. A network CAN solve successfully with zero Generator components
    (e.g. an all-Link/Store microgrid) — reproducing that through a real
    HiGHS solve would need a network shape unrelated to what's under test
    here, so — following the same convention `test_compare_invariants.py`'s
    `_solved_lost_load` uses for `_compute_lost_load_summary` — `has_solve`
    is passed directly, bypassing the dispatch-freshness classifier: what's
    under test is the function's OWN branch logic once told the network
    resolved, not how that flag gets derived.
    """
    import pypsa

    import routers.compare as CMP

    n = pypsa.Network()  # no components at all — gens.empty is trivially True

    solved = CMP._compute_curtailment_summary(n, [], False, True)
    assert solved.available is True, "solved network with a real zero must not read as unavailable"
    assert solved.total_gwh.total == 0.0

    unsolved = CMP._compute_curtailment_summary(n, [], False, False)
    assert unsolved.available is False


# ── Task 7: lost load — a measured zero vs. an unread capture ───────────────
#
# LostLoadComparison.available is overloaded in a way none of the checks
# above can see: within the has_solve=True path, `_compute_lost_load_
# summary` has six `available=False` returns and only ONE of them (a real
# zero after reindex/weighting) is a genuine "no shedding" result — the
# other five mean the capture was never read at all (missing pickle, an
# unpickle error, a malformed/empty capture, a reindex failure). Before this
# task, a solved project whose results_state.pkl was simply missing rendered
# `0.0 MWh` on the Compare view's lost-load KPIs, indistinguishable from a
# project that genuinely shed nothing. `captured` is the new field that
# tells the two apart; the two tests below pin one case each, both driven
# directly through `_compute_lost_load_summary` (same convention `test_
# compare_invariants.py`'s `_solved_lost_load` and this file's curtailment
# test above use — has_solve passed explicitly, bypassing the
# dispatch-freshness classifier, since no LP runs for either fixture).

def test_lost_load_reports_uncaptured_when_the_capture_is_unreadable(tmp_path):
    """
    A solved project whose `results_state.pkl` is simply absent (the
    simplest of the five unread-capture branches — see the table in
    task-7-brief.md) has measured NOTHING, not a zero.
    """
    import routers.compare as CMP
    from tests import compare_local_networks as cln

    n = cln.build_lost_load_network()  # tmp_path has no results_state.pkl
    block = CMP._compute_lost_load_summary(tmp_path, n, [], False, True)
    assert block.available is False
    assert block.captured is False, (
        "a solved project whose capture cannot be read has not measured zero "
        "shedding — it has measured nothing"
    )


def test_lost_load_reports_captured_on_a_genuine_zero(tmp_path):
    """
    A solved project whose capture exists and sums to zero after
    reindex/weighting (routers/compare.py's `total_e <= 1e-9` branch) has
    measured a REAL zero — `captured=True` even though `available=False`.
    """
    import routers.compare as CMP
    from tests import compare_local_networks as cln

    n = cln.build_lost_load_network()
    cln.write_lost_load_capture(
        tmp_path, n,
        per_bus_mwh={"bus_elec": [0.0, 0.0, 0.0, 0.0], "bus_h2": [0.0, 0.0, 0.0, 0.0]},
        voll=3000.0,
    )
    block = CMP._compute_lost_load_summary(tmp_path, n, [], False, True)
    assert block.available is False
    assert block.captured is True, "zero shedding is a real, measured result"
