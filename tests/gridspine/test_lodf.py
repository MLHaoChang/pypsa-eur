"""Task 5: DC sensitivities (PTDF/LODF) and the LODF-pruned N-2 screen.

Every identity here is PROVEN against pandapower's own DC solve, never
asserted: post-outage flows from the LODF are compared to `rundcpp` with the
branch out, for every non-islanding branch and for a sample of double
outages, and the DC islanding detection is compared to lightsim2grid's AC
connectivity over all 1035 pairs.

Two facts measured on case39 at hour 0 that shape the design (2026-09-05):

* All 1035 N-2 pairs solve in lightsim2grid in ~0.07 s, so on this fixture the
  prune buys no time. It is built and MEASURED because the spec requires it at
  client scale, not because case39 needs it.
* Three lines exceed 100 % in the BASE case (L11 127.6 %, L21 127.1 %, L19
  111.7 %), so an absolute loading threshold keeps every pair. The prune
  criterion is therefore the DC-estimated max loading, and the threshold the
  plan asks to measure is measured against pairs the full AC run shows to
  create a NEW violation beyond the base case.

Pruned pairs go to a separate prune log, not the results table: a results row
means "this outage was solved", and a half-estimated row in that table would
be read as an outcome by every consumer downstream.
"""
import copy
import itertools

import numpy as np
import pandapower as pp
import pandas as pd
import pytest

from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net
from gridspine.schema.contingency import NON_CONVERGED_SEVERITY, validate_contingency_results
from gridspine.schema.contracts import ContractError
from gridspine.static.contingency import (
    LOADING_MAX_PCT,
    branch_loading_pct,
    measure_prune_threshold,
    screen_n2,
)
from gridspine.static.contingency_set import branch_contingencies, n2_candidates
from gridspine.static.lodf import (
    N2_LEDGER,
    dc_base,
    dc_loading_pct,
    lodf_column,
    n1_dc_flows,
    n2_dc_flows,
)
from gridspine.static.loadflow import apply_snapshot

from tests.gridspine.test_contingency import ISLANDING
from tests.gridspine.test_shortcircuit import HOUR, _hour_tables

N_BRANCH, N_PAIRS = 46, 1035
DC_TOL_MW = 1e-6


@pytest.fixture(scope="module")
def snapshot():
    net = load_case39_res()
    reg = registry_from_net(net)
    dispatch, loads = _hour_tables(net, reg)
    apply_snapshot(net, dispatch, loads, hour=HOUR, registry=reg)
    n1 = branch_contingencies(net)
    return dict(net=net, reg=reg, dispatch=dispatch, loads=loads, n1=n1, n2=n2_candidates(n1))


@pytest.fixture(scope="module")
def state(snapshot):
    return dc_base(snapshot["net"])


def _pp_dc_flows(net, out_ids):
    """pandapower DC solve with the given branch ids (lines-then-trafos) out."""
    w = copy.deepcopy(net)
    nl = len(w.line)
    for k in out_ids:
        if k < nl:
            w.line.at[w.line.index[k], "in_service"] = False
        else:
            w.trafo.at[w.trafo.index[k - nl], "in_service"] = False
    pp.rundcpp(w)
    return np.concatenate([w.res_line["p_from_mw"].values, w.res_trafo["p_hv_mw"].values])


def _id_of(state, contingency_id):
    keys = state.branch_keys
    hit = keys.index[(keys["from_bus"] + "-" + keys["to_bus"] + "-" + keys["ckt"]) == contingency_id]
    assert len(hit) == 1
    return int(hit[0])


# --------------------------------------------------------------------------
# the base DC state
# --------------------------------------------------------------------------

def test_dc_base_reproduces_pandapowers_dc_flows(snapshot, state):
    want = _pp_dc_flows(snapshot["net"], [])
    assert state.flows_mw.shape == (N_BRANCH,)
    np.testing.assert_allclose(state.flows_mw, want, atol=DC_TOL_MW)


def test_ptdf_has_a_zero_reference_column_and_the_right_shape(snapshot, state):
    assert state.ptdf.shape == (N_BRANCH, len(snapshot["net"].bus))
    assert np.abs(state.ptdf[:, state.ref_bus]).max() == 0.0
    assert np.isfinite(state.ptdf).all()


def test_case39_hour0_base_case_already_violates_three_line_ratings(snapshot, state):
    """Pinned because it explains why severity is never zero on this fixture and
    why the prune threshold is measured against NEW violations."""
    net = copy.deepcopy(snapshot["net"])
    pp.runpp(net)
    ac = branch_loading_pct(net, net.res_line["i_from_ka"].values, net.res_trafo["i_hv_ka"].values)
    ac_over = set(np.where(ac > LOADING_MAX_PCT)[0])
    assert len(ac_over) == 3
    assert set(state.branch_keys.loc[list(ac_over), "element_type"]) == {"line"}
    # DC is the more conservative estimate here (L15 sits at 98.7 % in AC and
    # crosses 100 % in DC), so the AC set is contained in the DC set, not equal.
    dc_over = set(np.where(dc_loading_pct(state, state.flows_mw) > LOADING_MAX_PCT)[0])
    assert ac_over <= dc_over
    assert len(dc_over) - len(ac_over) <= 1


# --------------------------------------------------------------------------
# LODF: proven against brute force
# --------------------------------------------------------------------------

def test_lodf_diagonal_is_minus_one_and_islanding_columns_are_nan_never_inf(state):
    L = state.lodf
    assert L.shape == (N_BRANCH, N_BRANCH)
    assert not np.isinf(L).any()
    live = ~state.islanding
    np.testing.assert_allclose(np.diag(L)[live], -1.0, atol=1e-9)
    assert np.isnan(L[:, state.islanding]).all()
    assert not np.isnan(L[:, live]).any()


def test_dc_islanding_branches_are_exactly_the_ac_islanding_outages(state):
    ids = {_id_of(state, cid) for cid in ISLANDING}
    assert set(np.where(state.islanding)[0]) == ids


def test_lodf_column_raises_for_an_islanding_branch_rather_than_returning_inf(state):
    k = int(np.where(state.islanding)[0][0])
    with pytest.raises(ContractError, match="island"):
        lodf_column(state, k)
    live = int(np.where(~state.islanding)[0][0])
    assert np.isfinite(lodf_column(state, live)).all()


def test_n1_dc_flows_match_pandapower_for_every_non_islanding_branch(snapshot, state):
    for k in np.where(~state.islanding)[0]:
        want = _pp_dc_flows(snapshot["net"], [k])
        got = n1_dc_flows(state, int(k))
        # the outaged branch carries nothing; compare the rest
        mask = np.arange(N_BRANCH) != k
        np.testing.assert_allclose(got[mask], want[mask], atol=DC_TOL_MW, err_msg=f"branch {k}")
        assert abs(got[k]) < DC_TOL_MW


def test_n1_dc_flows_refuse_an_islanding_branch(state):
    k = int(np.where(state.islanding)[0][0])
    with pytest.raises(ContractError, match="island"):
        n1_dc_flows(state, k)


def test_n2_dc_flows_match_pandapower_on_a_sample_of_connected_pairs(snapshot, state):
    rng = np.random.default_rng(39)
    live = np.where(~state.islanding)[0]
    checked = 0
    for a, b in rng.choice(live, size=(60, 2), replace=True):
        if a == b:
            continue
        flows, islanded = n2_dc_flows(state, int(a), int(b))
        if islanded:
            continue
        want = _pp_dc_flows(snapshot["net"], [int(a), int(b)])
        mask = ~np.isin(np.arange(N_BRANCH), [a, b])
        np.testing.assert_allclose(flows[mask], want[mask], atol=DC_TOL_MW, err_msg=f"pair {(a, b)}")
        checked += 1
    assert checked >= 25


def test_dc_pair_islanding_matches_lightsim2grid_connectivity_over_all_pairs(snapshot, state):
    """473 of 1035 pairs split case39; the 2x2 LODF system must flag exactly them."""
    from lightsim2grid.contingencyAnalysis import ContingencyAnalysisCPP
    from lightsim2grid.gridmodel import init_from_pandapower

    net = copy.deepcopy(snapshot["net"])
    pp.runpp(net)
    ca = ContingencyAnalysisCPP(init_from_pandapower(net))
    pairs = list(itertools.combinations(range(N_BRANCH), 2))
    for a, b in pairs:
        ca.add_nk([a, b])
    ca.compute(net._ppc["internal"]["V"], 20, 1e-8)
    ac_connected = np.asarray(ca.is_grid_connected_after_contingency()).astype(bool)

    dc_islanded = np.array([n2_dc_flows(state, a, b)[1] for a, b in pairs])
    assert dc_islanded.sum() == (~ac_connected).sum() == 473
    np.testing.assert_array_equal(dc_islanded, ~ac_connected)


# --------------------------------------------------------------------------
# the N-2 screen
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def screened(snapshot):
    s = snapshot
    return screen_n2(s["net"], s["n2"], s["dispatch"], s["loads"], HOUR, s["reg"], prune_threshold_pct=0.0)


def test_screen_n2_at_zero_threshold_verifies_every_connected_pair(screened, snapshot):
    results, log = screened
    validate_contingency_results(results)
    assert len(log) == N_PAIRS
    assert set(log["decision"]) == {"verified", "islanded"}
    assert (log["decision"] == "islanded").sum() == 473
    assert len(results) == N_PAIRS
    assert results["islanded"].sum() == 473
    verified = results[~results["islanded"]]
    assert len(verified) == 562
    # One double outage diverges in AC on this snapshot — a result, recorded as
    # converged=False, islanded=False, sentinel severity. Pinned so a change in
    # that count is seen.
    diverged = verified.loc[~verified["converged"], "contingency_id"].tolist()
    assert diverged == ["BUS_05-BUS_08-1--BUS_06-BUS_07-1"], diverged
    assert (verified.loc[~verified["converged"], "severity"] == NON_CONVERGED_SEVERITY).all()


def test_screen_n2_results_agree_with_the_n1_style_definitions(screened, snapshot):
    """One verified pair, re-solved by pandapower with both branches out."""
    results, _log = screened
    row = results[~results["islanded"]].iloc[0]
    a, b = row["contingency_id"].split("--")
    state = dc_base(snapshot["net"])
    ids = [_id_of(state, a), _id_of(state, b)]
    net = copy.deepcopy(snapshot["net"])
    nl = len(net.line)
    for k in ids:
        if k < nl:
            net.line.at[net.line.index[k], "in_service"] = False
        else:
            net.trafo.at[net.trafo.index[k - nl], "in_service"] = False
    pp.runpp(net)
    want = branch_loading_pct(net, net.res_line["i_from_ka"].values, net.res_trafo["i_hv_ka"].values).max()
    assert row["max_branch_loading_pct"] == pytest.approx(want, rel=1e-6)
    assert row["min_vm_pu"] == pytest.approx(net.res_bus["vm_pu"].min(), abs=1e-6)


def test_prune_log_carries_the_dc_estimate_for_every_pair(screened):
    _results, log = screened
    for col in ("contingency_id", "decision", "dc_max_loading_pct", "dc_new_overloads"):
        assert col in log.columns, col
    est = log.loc[log["decision"] != "islanded", "dc_max_loading_pct"]
    assert est.notna().all() and (est > 0).all()
    assert log.loc[log["decision"] == "islanded", "dc_max_loading_pct"].isna().all()


def test_a_high_threshold_prunes_and_pruned_pairs_leave_the_results(snapshot):
    s = snapshot
    results, log = screen_n2(s["net"], s["n2"], s["dispatch"], s["loads"], HOUR, s["reg"], prune_threshold_pct=250.0)
    assert (log["decision"] == "pruned").sum() > 0
    assert set(results["contingency_id"]) == set(log.loc[log["decision"] != "pruned", "contingency_id"])
    validate_contingency_results(results)


def test_measured_threshold_loses_no_pair_with_a_new_ac_violation(snapshot):
    """The plan's Step 5 as code, and its result on case39 at hour 0 (measured
    2026-09-05): threshold 92.22 %, which prunes NOTHING.

    520 of 561 connected pairs create a violation the base case did not have,
    none by voltage alone. The lowest DC estimate among them is also the lowest
    over ALL connected pairs, so any threshold that keeps every flagged pair
    keeps every pair. The pairs at the bottom sit at 92 % in DC with zero
    predicted new overloads while AC finds one: the blind spot is DC
    UNDERESTIMATING loading on the critical branch, not the missing voltage
    term. Asserted as measured fact so the number in the ledger is the number
    the code produces."""
    s = snapshot
    threshold, report = measure_prune_threshold(s["net"], s["n2"], s["dispatch"], s["loads"], HOUR, s["reg"])
    assert report["ac_pairs_connected"] == 561
    assert report["ac_pairs_with_new_violation"] == 520
    assert report["flagged_by_voltage_only"] == []
    assert report["base_case_overloaded_branches"] == 3
    assert threshold == pytest.approx(92.22, abs=0.01)
    assert threshold == pytest.approx(report["min_dc_loading_among_flagged"])

    _results, log = screen_n2(s["net"], s["n2"], s["dispatch"], s["loads"], HOUR, s["reg"], prune_threshold_pct=threshold)
    kept = set(log.loc[log["decision"] == "verified", "contingency_id"])
    assert set(report["new_violation_pairs"]) <= kept
    connected = log[log["decision"] != "islanded"]
    assert (log["decision"] == "pruned").sum() == 0
    assert connected["dc_max_new_loading_pct"].min() == pytest.approx(threshold)
    bottom = connected.nsmallest(5, "dc_max_new_loading_pct")
    assert (bottom["dc_new_overloads"] == 0).all(), "DC predicted no new overload on the pairs AC flags"

    # The threshold is TIGHT: a hair above it prunes, and loses a flagged pair.
    _r2, log2 = screen_n2(s["net"], s["n2"], s["dispatch"], s["loads"], HOUR, s["reg"], prune_threshold_pct=threshold + 0.01)
    pruned = set(log2.loc[log2["decision"] == "pruned", "contingency_id"])
    assert pruned and (pruned & set(report["new_violation_pairs"]))


def test_screen_n2_refuses_a_net_that_does_not_carry_the_hour(snapshot):
    fresh = load_case39_res()
    with pytest.raises(ContractError, match="does not carry hour"):
        screen_n2(fresh, snapshot["n2"], snapshot["dispatch"], snapshot["loads"], HOUR, snapshot["reg"], prune_threshold_pct=0.0)


def test_screen_n2_refuses_a_non_n2_set(snapshot):
    with pytest.raises(ContractError, match="order"):
        screen_n2(snapshot["net"], snapshot["n1"], snapshot["dispatch"], snapshot["loads"], HOUR, snapshot["reg"], prune_threshold_pct=0.0)


def test_n2_ledger_records_the_measured_facts():
    text = " ".join(N2_LEDGER).lower()
    for word in ("lodf", "base case", "new", "measured", "prune"):
        assert word in text, word


def test_lodf_module_imports_no_engine_but_pandapower():
    import gridspine.static.lodf as mod

    # Import statements, not bare words: the docstring names lightsim2grid when
    # explaining what the detection is proven against.
    src = open(mod.__file__, encoding="utf-8").read()
    assert "import pypsa" not in src and "from pypsa" not in src
    assert "import lightsim2grid" not in src and "from lightsim2grid" not in src
