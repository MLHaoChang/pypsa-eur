"""Task 4: full AC N-1 screening on a snapshot-applied net.

Backend for branch outages is lightsim2grid's `ContingencyAnalysisCPP` on a
GridModel built from the pandapower net; unit outages go through pandapower on
a copy, because a generator is not a branch id and its lost MW has to land on
the slack. Both paths report the SAME loading definition — from/HV-side
current over rating — because lightsim2grid returns the from side only.
pandapower's own `loading_percent` is two-sided, so the two are cross-checked
here by measuring, not by asserting equality.

Probed on case39 before any of this was written (2026-09-05): lightsim2grid
matches pandapower to 1e-10 in |V| and 5e-10 in from-side kA on the connected
cases; 11 of the 46 branch outages leave `is_grid_connected == 0` with
all-zero voltages — nine radial generator connections and two (the line
BUS_16-BUS_19 and the transformer BUS_19-BUS_20) that cut off the whole
{BUS_19, BUS_20, G33, G34} pocket. Those are ISLANDED rows, never converged,
identical in every hour, and flagged so the ranking can see them for what
they are.

THE stage-order guard: the net handed in must already carry the hour. A
screen run on case39's native peak is the increment-1 defect in a new file;
`screen_n1` refuses it, and the mutation check removes the refusal.
"""
import copy

import numpy as np
import pandapower as pp
import pandas as pd
import pytest

from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net
from gridspine.schema.contingency import (
    NON_CONVERGED_SEVERITY,
    validate_contingency_results,
)
from gridspine.schema.contracts import ContractError
from gridspine.static.contingency import (
    LOADING_MAX_PCT,
    N1_LEDGER,
    V_MAX_PU,
    V_MIN_PU,
    branch_loading_pct,
    screen_n1,
    severity,
)
from gridspine.static.contingency_set import branch_contingencies, unit_contingencies
from gridspine.static.loadflow import apply_snapshot, run_lf

from tests.gridspine.test_shortcircuit import HOUR, _hour_tables

N_BRANCH, N_UNIT = 46, 14

# From the probe: every outage that splits case39. Nine radial generator
# connections plus the two that isolate the BUS_19/BUS_20 pocket.
ISLANDING = {
    "BUS_16-BUS_19-1", "BUS_23-BUS_36-1",
    "BUS_02-BUS_30-1", "BUS_06-BUS_31-1", "BUS_10-BUS_32-1", "BUS_19-BUS_20-1",
    "BUS_19-BUS_33-1", "BUS_20-BUS_34-1", "BUS_22-BUS_35-1", "BUS_25-BUS_37-1",
    "BUS_29-BUS_38-1",
}


@pytest.fixture(scope="module")
def snapshot():
    net = load_case39_res()
    reg = registry_from_net(net)
    dispatch, loads = _hour_tables(net, reg)
    apply_snapshot(net, dispatch, loads, hour=HOUR, registry=reg)
    base = run_lf(net)
    assert base.converged
    cset = pd.concat([branch_contingencies(net), unit_contingencies(reg)], ignore_index=True)
    return dict(net=net, reg=reg, dispatch=dispatch, loads=loads, cset=cset, base=base)


@pytest.fixture(scope="module")
def screened(snapshot):
    s = snapshot
    return screen_n1(s["net"], s["cset"], s["dispatch"], s["loads"], HOUR, s["reg"])


# --------------------------------------------------------------------------
# shape and contract
# --------------------------------------------------------------------------

def test_one_validated_row_per_contingency(screened, snapshot):
    validate_contingency_results(screened)
    assert len(screened) == N_BRANCH + N_UNIT
    assert screened["contingency_id"].tolist() == snapshot["cset"]["contingency_id"].tolist()
    assert (screened["hour"] == HOUR).all()


def test_islanding_outages_are_flagged_never_converged_and_carry_the_sentinel(screened):
    isl = screened[screened["islanded"]]
    assert set(isl["contingency_id"]) == ISLANDING
    assert not isl["converged"].any()
    assert (isl["severity"] == NON_CONVERGED_SEVERITY).all()
    assert isl[["max_branch_loading_pct", "min_vm_pu", "max_vm_pu"]].isna().all().all()


def test_every_non_islanding_branch_outage_converges_on_case39(screened):
    rest = screened[~screened["islanded"] & screened["contingency_id"].str.contains("-")]
    assert len(rest) == N_BRANCH - len(ISLANDING)
    assert rest["converged"].all(), rest.loc[~rest["converged"], "contingency_id"].tolist()
    assert rest[["max_branch_loading_pct", "min_vm_pu", "max_vm_pu"]].notna().all().all()
    assert (rest["severity"] < NON_CONVERGED_SEVERITY).all()


def test_unit_outages_are_screened_and_losing_the_equivalent_is_a_recorded_collapse(screened):
    """Every unit outage gets a row. Thirteen converge. Losing G_BUS_39 — the
    500 s aggregated interconnection equivalent — does not, under default
    Newton, 100 iterations, a DC start or Gauss-Seidel at 5000 (probed
    2026-09-05): the slack cannot pick up its output on this snapshot. That is
    a RESULT — converged=False, islanded=False (the bus is still connected),
    sentinel severity — not a crash, and not an islanding."""
    units = screened[~screened["contingency_id"].str.contains("-")].set_index("contingency_id")
    assert len(units) == N_UNIT
    collapsed = units.index[~units["converged"]].tolist()
    assert collapsed == ["G_BUS_39"], collapsed
    row = units.loc["G_BUS_39"]
    assert not row["islanded"] and row["severity"] == NON_CONVERGED_SEVERITY
    assert units.drop(index="G_BUS_39")["converged"].all()


# --------------------------------------------------------------------------
# the solver is pandapower to solver precision — cross-checked, not trusted
# --------------------------------------------------------------------------

def test_a_branch_outage_matches_a_pandapower_solve_of_the_same_outage(screened, snapshot):
    net = copy.deepcopy(snapshot["net"])
    i = net.line.index[0]
    cid = f"{net.bus.at[net.line.at[i, 'from_bus'], 'name']}-{net.bus.at[net.line.at[i, 'to_bus'], 'name']}-1"
    net.line.at[i, "in_service"] = False
    pp.runpp(net)
    row = screened.set_index("contingency_id").loc[cid]
    assert row["min_vm_pu"] == pytest.approx(net.res_bus["vm_pu"].min(), abs=1e-6)
    assert row["max_vm_pu"] == pytest.approx(net.res_bus["vm_pu"].max(), abs=1e-6)
    want = branch_loading_pct(net, net.res_line["i_from_ka"].values, net.res_trafo["i_hv_ka"].values).max()
    assert row["max_branch_loading_pct"] == pytest.approx(want, rel=1e-6)


def test_a_unit_outage_matches_a_pandapower_solve_with_the_gen_out(screened, snapshot):
    net = copy.deepcopy(snapshot["net"])
    i = net.gen.index[net.gen["name"] == "G_BUS_32"][0]
    net.gen.at[i, "in_service"] = False
    pp.runpp(net)
    row = screened.set_index("contingency_id").loc["G_BUS_32"]
    assert row["min_vm_pu"] == pytest.approx(net.res_bus["vm_pu"].min(), abs=1e-6)
    want = branch_loading_pct(net, net.res_line["i_from_ka"].values, net.res_trafo["i_hv_ka"].values).max()
    assert row["max_branch_loading_pct"] == pytest.approx(want, rel=1e-6)


def test_from_side_loading_is_within_a_measured_band_of_pandapowers_two_sided(snapshot):
    """Not equality — pandapower takes the max of both ends. Pin the deviation
    so a definition drift shows up as a number, and record that band."""
    net = copy.deepcopy(snapshot["net"])
    pp.runpp(net)
    ours = branch_loading_pct(net, net.res_line["i_from_ka"].values, net.res_trafo["i_hv_ka"].values)
    theirs = np.concatenate([net.res_line["loading_percent"].values, net.res_trafo["loading_percent"].values])
    assert (ours <= theirs + 1e-6).all(), "from-side loading can never exceed the two-sided max"
    # Measured 6.08 percentage points on case39 (2026-09-05); the bound leaves room
    # for a different hour, not for a different definition.
    assert np.max(theirs - ours) < 8.0, np.max(theirs - ours)


# --------------------------------------------------------------------------
# severity: one definition, hand-checkable
# --------------------------------------------------------------------------

def test_severity_is_zero_without_violations():
    assert severity(np.array([50.0, 99.9]), np.array([0.95, 1.05])) == 0.0


def test_severity_adds_overload_depth_and_voltage_excursion():
    """120 % loading is 0.2 over; 0.85 pu is 0.05 below V_MIN over a 0.1 band = 0.5."""
    assert V_MIN_PU == 0.9 and V_MAX_PU == 1.1 and LOADING_MAX_PCT == 100.0
    s = severity(np.array([120.0, 80.0]), np.array([0.85, 1.0]))
    assert s == pytest.approx(0.2 + 0.5)
    assert severity(np.array([120.0]), np.array([1.0])) < s


def test_severity_is_monotone_in_both_terms():
    a = severity(np.array([110.0]), np.array([1.0]))
    b = severity(np.array([130.0]), np.array([1.0]))
    c = severity(np.array([130.0]), np.array([1.15]))
    assert 0 < a < b < c


def test_violation_count_matches_the_limits(screened, snapshot):
    row = screened.set_index("contingency_id").loc["BUS_01-BUS_02-1"]
    net = copy.deepcopy(snapshot["net"])
    net.line.at[net.line.index[0], "in_service"] = False
    pp.runpp(net)
    ld = branch_loading_pct(net, net.res_line["i_from_ka"].values, net.res_trafo["i_hv_ka"].values)
    vm = net.res_bus["vm_pu"].values
    want = int((ld > LOADING_MAX_PCT).sum() + ((vm < V_MIN_PU) | (vm > V_MAX_PU)).sum())
    assert int(row["n_violations"]) == want


# --------------------------------------------------------------------------
# the net is left exactly as found
# --------------------------------------------------------------------------

def test_the_callers_net_is_untouched(snapshot):
    net = snapshot["net"]
    before = (net.line["in_service"].copy(), net.trafo["in_service"].copy(),
              net.gen["in_service"].copy(), net.sgen["in_service"].copy())
    lf_before = run_lf(net)
    screen_n1(net, snapshot["cset"], snapshot["dispatch"], snapshot["loads"], HOUR, snapshot["reg"])
    lf_after = run_lf(net)
    pd.testing.assert_series_equal(net.line["in_service"], before[0])
    pd.testing.assert_series_equal(net.trafo["in_service"], before[1])
    pd.testing.assert_series_equal(net.gen["in_service"], before[2])
    pd.testing.assert_series_equal(net.sgen["in_service"], before[3])
    pd.testing.assert_frame_equal(lf_before.bus, lf_after.bus)
    pd.testing.assert_frame_equal(lf_before.branch_flow, lf_after.branch_flow)


# --------------------------------------------------------------------------
# THE stage-order guard
# --------------------------------------------------------------------------

def test_a_net_that_does_not_carry_the_hour_is_refused(snapshot):
    """Mutation target: remove the guard and this goes green on a wrong net."""
    fresh = load_case39_res()  # native peak, RES at installed — not the hour
    with pytest.raises(ContractError, match="does not carry hour"):
        screen_n1(fresh, snapshot["cset"], snapshot["dispatch"], snapshot["loads"], HOUR, snapshot["reg"])


def test_a_contingency_naming_an_unknown_element_is_refused(snapshot):
    bad = snapshot["cset"].copy()
    bad.loc[0, ["contingency_id", "from_bus", "to_bus", "ckt"]] = ["BUS_01-BUS_39-9", "BUS_01", "BUS_39", "9"]
    bad.at[0, "element_ids"] = ["BUS_01-BUS_39-9"]
    with pytest.raises(ContractError, match="BUS_01-BUS_39-9"):
        screen_n1(snapshot["net"], bad, snapshot["dispatch"], snapshot["loads"], HOUR, snapshot["reg"])


def test_n1_ledger_records_the_conventions():
    text = " ".join(N1_LEDGER).lower()
    for word in ("from", "islanded", "slack", "lightsim2grid"):
        assert word in text, word


def test_module_imports_no_pypsa():
    import gridspine.static.contingency as mod

    src = open(mod.__file__, encoding="utf-8").read()
    assert "import pypsa" not in src and "gridspine.producers" not in src
