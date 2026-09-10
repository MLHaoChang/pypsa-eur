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


# ── Whole-branch review, Minor 1: emissions on a carrier-less network ───────
#
# `_compute_emissions_summary`'s `if not co2_map` branch fires whenever the
# network has no `carriers` frame (or no `co2_emissions` column) — see
# `services/economics.co2_intensity_map`'s docstring. That is the same
# "solved network, structurally nothing of that kind" shape Finding 4 ruled
# `available=True` for above (curtailment's `gens.empty`, storage cycling's
# `sus.empty`): every carrier is zero-emitting by definition, so 0 kt is the
# real answer, not an unresolved one. This site was missed by that ruling.

def test_emissions_is_a_real_zero_on_a_solved_network_with_no_carriers_frame():
    """
    Same convention as the curtailment test above — `has_solve` passed
    directly, since what's under test is `_compute_emissions_summary`'s own
    branch logic once told the network resolved, not how `has_solve` is
    derived.
    """
    import pypsa

    import routers.compare as CMP

    n = pypsa.Network()  # no carriers frame at all — co2_map is trivially empty

    solved = CMP._compute_emissions_summary(n, [], False, True)
    assert solved.available is True, "solved network with a real zero must not read as unavailable"
    assert solved.total_kt.total == 0.0

    unsolved = CMP._compute_emissions_summary(n, [], False, False)
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


def test_lost_load_reports_uncaptured_when_the_reindexed_total_is_non_finite(tmp_path):
    """
    Review finding I3: the original `not isfinite(total_e) or total_e <=
    1e-9` guard returned `captured=True` for BOTH disjuncts, but only the
    second is a measured zero. A non-finite total (an inf here — the same
    branch a NaN `snapshot_weightings` column would reach, since
    `_build_snapshot_weights` passes NaN through `.astype(float)`) means the
    capture was read but produced garbage, not a real result.
    """
    import routers.compare as CMP
    from tests import compare_local_networks as cln

    n = cln.build_lost_load_network()
    cln.write_lost_load_capture(
        tmp_path, n,
        per_bus_mwh={"bus_elec": [float("inf"), 0.0, 0.0, 0.0], "bus_h2": [0.0, 0.0, 0.0, 0.0]},
        voll=3000.0,
    )
    block = CMP._compute_lost_load_summary(tmp_path, n, [], False, True)
    assert block.available is False
    assert block.captured is False, (
        "a non-finite reindexed total is not a measured zero — the capture "
        "was read but produced garbage, so nothing usable was measured"
    )


# ── Task 8: curtailment — a failed computation vs. a real zero ──────────────
#
# `_compute_curtailment_summary`'s per-generator loop has six `continue`
# paths that all converge on the same `if not curt_by_carrier` empty-result
# return. Paths 1 (`g not in gens.index`), 2 (`g not in p_max_pu_t.columns`,
# thermal generators with no profile of their own) and 6 (`total_a <=
# 1e-9`, no available energy) are legitimate "nothing to curtail" skips.
# Paths 3 (non-finite effective capacity), 4 (the bare `except` around the
# reindex) and 5 (non-finite weighted total) are failures: the computation
# never produced a usable number for that generator, which is not the same
# as it producing zero. Before this task all six converged on
# `available=True`, so a computation that never succeeded reported as a
# measured zero — the same conflation Task 7 fixed for lost load.
#
# Both tests below share a fixture SHAPE: one solved network with a thermal
# generator (no profile, always a legitimate path-2 skip) plus one
# renewable generator with an explicit `p_max_pu` time series so
# `generators_t.p_max_pu` stays non-empty and the walk reaches the loop body
# instead of exiting at the earlier "missing tables" check (a DIFFERENT
# site this task leaves alone — same convention the existing
# `_build_network_with_no_curtailable_capacity` fixture above uses). The
# only difference between the two tests is whether the renewable's own
# figure comes out finite (a real, if zero-ish, answer) or is corrupted
# into a non-finite total (a failure) — so neither assertion is satisfiable
# by a flag that ignores which of those happened.

def test_curtailment_is_unavailable_when_every_generator_failed_to_compute():
    """
    Path 5: the one profiled generator's weighted total goes non-finite.

    Forced with a genuine `inf`, not a mock — injected into
    `generators_t.p_max_pu` for the profiled generator AFTER the solve, so
    the LP itself is untouched and only the post-solve arithmetic sees the
    bad value. A literal NaN would not survive: both `disp` and `pmu` are
    piped through `.fillna(0.0)` before use, which sanitises NaN back to a
    normal (if wrong) number and never reaches the `isfinite` guard. `inf`
    survives `fillna` and multiplies through `available = pmu * eff_cap`
    into both `total_c` and `total_a`, so this exercises the real arithmetic
    path most likely to occur in the field — a corrupted or misaligned
    profile column — rather than a mocked exception.

    The thermal "gas" generator in the same network has no `p_max_pu_t`
    column of its own, so it takes the legitimate path-2 skip; "wind" is
    the only generator that could have contributed, and it failed. The
    result is empty because nothing COULD be computed, not because there
    was nothing to curtail.
    """
    import pandas as pd
    import pypsa

    import routers.compare as CMP

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
        p_max_pu=[0.5, 0.6, 0.4, 0.7],
    )
    n.add("Load", "load1", bus="b1", p_set=40.0)
    n.optimize(solver_name="highs")

    # Corrupt the one profiled generator's availability AFTER the solve —
    # the LP already ran cleanly; only the curtailment arithmetic sees this.
    n.generators_t.p_max_pu.loc[n.snapshots[0], "wind"] = float("inf")

    block = CMP._compute_curtailment_summary(n, [], False, True)
    assert block.available is False, (
        "an empty result caused by a computation failure is not a measured "
        "zero — see ADR-0001"
    )


def test_curtailment_is_a_real_zero_when_every_skip_was_legitimate():
    """
    Path 2 only: every generator that reaches the loop is skipped because it
    has no `p_max_pu` column of its own — nothing failed.

    `generators_t.p_max_pu` must stay non-empty for the walk to reach the
    per-generator loop at all (an empty table exits earlier, at the
    "missing tables" check this task leaves alone), but a REAL renewable
    generator with its own profile would itself land in the loop and either
    contribute (curt_by_carrier non-empty — a different branch entirely) or
    hit path 6 (zero available energy) rather than path 2 — that shape is
    exactly what `test_curtailment_and_storage_cycling_are_real_zeros_when_
    structurally_empty_on_a_solved_network` above already covers, so
    reusing it here would not isolate path 2.

    So the profile column belongs to a name that never reaches the loop:
    the loop walks `p_t.columns` (dispatch results), and `generators_t.p`
    always has a column for every real Generator post-solve, so a
    `p_max_pu_t` column under a name that ISN'T a generator in the network
    is never looked up by its own name and can never do anything but keep
    the table non-empty. The one real generator, "gas", reaches the loop,
    finds itself absent from `p_max_pu_t.columns`, and takes the legitimate
    path-2 skip. Nothing failed; zero is the real answer.
    """
    import pandas as pd
    import pypsa

    import routers.compare as CMP

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b1", carrier="AC")
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add(
        "Generator", "gas",
        bus="b1", carrier="gas",
        p_nom=100.0, marginal_cost=50.0,
    )
    n.add("Load", "load1", bus="b1", p_set=20.0)
    n.optimize(solver_name="highs")

    # Keeps generators_t.p_max_pu non-empty without giving "gas" (the only
    # real generator) a profile column of its own — see docstring.
    n.generators_t.p_max_pu["not_a_real_generator"] = [0.5, 0.5, 0.5, 0.5]

    block = CMP._compute_curtailment_summary(n, [], False, True)
    assert block.available is True
    assert block.total_gwh.total == 0.0


def test_curtailment_flags_the_partial_case_when_some_generators_failed():
    """
    The NON-empty return: "wind" computes a real figure, "solar" fails on
    path 5, and the block ships wind's number alone.

    `available` is correctly True here — a real measurement IS present — so
    it cannot carry this. Without a second signal the block is
    indistinguishable from a complete answer while understating by exactly
    solar's contribution. Arguably worse than the empty-result case Task 8
    fixed, because nothing about the response looks wrong. Recorded then as
    out of scope; this is that follow-up.

    Same `inf`-after-solve technique as the two tests above, and for the
    same reason: a literal NaN is sanitised by `.fillna(0.0)` before any
    `isfinite` guard sees it, so a NaN fixture would exercise nothing.
    """
    import pandas as pd
    import pypsa

    import routers.compare as CMP

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b1", carrier="AC")
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Carrier", "wind")
    n.add("Carrier", "solar")
    n.add(
        "Generator", "gas",
        bus="b1", carrier="gas",
        p_nom=100.0, marginal_cost=50.0,
    )
    n.add(
        "Generator", "wind",
        bus="b1", carrier="wind",
        p_nom=50.0, p_nom_extendable=False, marginal_cost=0.0,
        p_max_pu=[0.5, 0.6, 0.4, 0.7],
    )
    n.add(
        "Generator", "solar",
        bus="b1", carrier="solar",
        p_nom=50.0, p_nom_extendable=False, marginal_cost=0.0,
        p_max_pu=[0.3, 0.8, 0.2, 0.9],
    )
    n.add("Load", "load1", bus="b1", p_set=20.0)
    n.optimize(solver_name="highs")

    # Corrupt ONLY solar, after the solve. wind still produces a real figure,
    # so curt_by_carrier is non-empty and the walk takes the non-empty return.
    n.generators_t.p_max_pu.loc[n.snapshots[0], "solar"] = float("inf")

    block = CMP._compute_curtailment_summary(n, [], False, True)

    assert block.available is True, (
        "wind produced a real measurement — the block is not unavailable"
    )
    assert block.partial is True, (
        "solar's figure could not be computed, so the shipped total "
        "understates the truth; a complete-looking answer here is the "
        "ADR-0001 failure mode wearing a plausible number instead of a zero"
    )
    assert "wind" in block.by_carrier_gwh
    assert "solar" not in block.by_carrier_gwh
