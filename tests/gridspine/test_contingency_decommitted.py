"""The screen at an hour with DECOMMITTED units — the case the hour-0 tests
never see, because at case39's native peak every unit is on.

Found on the v3 year: lightsim2grid 0.10.1's `init_from_pandapower` applies
`gen.in_service` and then, when the slack comes from `ext_grid`, its slack
adder calls `init_generators` a second time over every pandapower gen and
never re-applies the flags. Every out-of-service generator comes back as a
live PV bus holding its setpoint. The unit rows (pandapower) were right; the
branch rows and every N-2 pair were solved on a different grid — at hour 1803
of the v3 run, 0.14 pu off on the base case. 8754 of that year's 8760 hours
have at least one synchronous unit off.

The fixture curtails two synchronous units and one RES unit at hour 0 (the
slack picks up ~1150 MW; the base case converges) and holds lightsim2grid to
pandapower on that grid, for N-1 and N-2 alike. `gridmodel_for` is the one
place the GridModel is built, and the tests pin its status vector to the
pandapower flags so the fix cannot silently regress.
"""
import copy

import numpy as np
import pandapower as pp
import pandas as pd
import pytest

from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net
from gridspine.static.contingency import (
    N1_LEDGER,
    branch_loading_pct,
    gridmodel_for,
    screen_n1,
    screen_n2,
)
from gridspine.static.contingency_set import (
    branch_contingencies,
    n2_candidates,
    unit_contingencies,
)
from gridspine.static.loadflow import apply_snapshot, run_lf

from tests.gridspine.test_shortcircuit import HOUR, _hour_tables

CURTAILED = ("G_BUS_33", "G_BUS_34", "S_BUS_34")


@pytest.fixture(scope="module")
def decommitted():
    net = load_case39_res()
    reg = registry_from_net(net)
    dispatch, loads = _hour_tables(net, reg, curtailed=CURTAILED)
    apply_snapshot(net, dispatch, loads, hour=HOUR, registry=reg)
    assert run_lf(net).converged
    assert not net.gen.loc[net.gen["name"].isin(CURTAILED), "in_service"].any()
    assert not net.sgen.loc[net.sgen["name"].isin(CURTAILED), "in_service"].any()
    n1 = pd.concat([branch_contingencies(net), unit_contingencies(reg)], ignore_index=True)
    return dict(net=net, reg=reg, dispatch=dispatch, loads=loads, n1=n1,
                n2=n2_candidates(branch_contingencies(net)))


@pytest.fixture(scope="module")
def screened(decommitted):
    s = decommitted
    return screen_n1(s["net"], s["n1"], s["dispatch"], s["loads"], HOUR, s["reg"]).set_index("contingency_id")


def _pp_outage(net, lines=(), trafos=(), gens=(), sgens=()):
    w = copy.deepcopy(net)
    for i in lines:
        w.line.at[i, "in_service"] = False
    for i in trafos:
        w.trafo.at[i, "in_service"] = False
    for i in gens:
        w.gen.at[i, "in_service"] = False
    for i in sgens:
        w.sgen.at[i, "in_service"] = False
    try:
        pp.runpp(w)
    except pp.LoadflowNotConverged:
        return None
    return w


def _line_cid(net, i):
    return f"{net.bus.at[net.line.at[i, 'from_bus'], 'name']}-{net.bus.at[net.line.at[i, 'to_bus'], 'name']}-1"


def test_gridmodel_status_vectors_follow_the_pandapower_flags(decommitted):
    work = copy.deepcopy(decommitted["net"])
    pp.runpp(work)
    gm = gridmodel_for(work)
    n_gen = len(work.gen)
    status = list(gm.get_gen_status())
    # pandapower gens in table order, then the slack lightsim2grid adds from ext_grid
    assert len(status) == n_gen + len(work.ext_grid)
    assert status[:n_gen] == work.gen["in_service"].astype(bool).tolist()
    assert all(status[n_gen:])
    assert list(gm.get_sgens_status()) == work.sgen["in_service"].astype(bool).tolist()


def test_gridmodel_base_case_matches_pandapower_on_the_decommitted_grid(decommitted):
    work = copy.deepcopy(decommitted["net"])
    pp.runpp(work)
    gm = gridmodel_for(work)
    v = gm.ac_pf(np.ones(len(work.bus), dtype=complex), 30, 1e-8)
    assert len(v) == len(work.bus)
    assert np.abs(np.abs(v) - work.res_bus["vm_pu"].values).max() < 1e-8


def test_every_converged_branch_outage_matches_pandapower_with_units_off(screened, decommitted):
    net = decommitted["net"]
    compared = 0
    for i in net.line.index:
        row = screened.loc[_line_cid(net, i)]
        if not row["converged"]:
            continue
        w = _pp_outage(net, lines=[i])
        assert w is not None, f"pandapower diverges where lightsim2grid converged: {_line_cid(net, i)}"
        assert row["min_vm_pu"] == pytest.approx(w.res_bus["vm_pu"].min(), abs=1e-6)
        assert row["max_vm_pu"] == pytest.approx(w.res_bus["vm_pu"].max(), abs=1e-6)
        want = branch_loading_pct(w, w.res_line["i_from_ka"].values, w.res_trafo["i_hv_ka"].values).max()
        assert row["max_branch_loading_pct"] == pytest.approx(want, rel=1e-6)
        compared += 1
    assert compared >= 25


def test_a_unit_outage_still_matches_pandapower_with_units_off(screened, decommitted):
    net = decommitted["net"]
    i = net.gen.index[net.gen["name"] == "G_BUS_32"][0]
    w = _pp_outage(net, gens=[i])
    row = screened.loc["G_BUS_32"]
    assert row["max_vm_pu"] == pytest.approx(w.res_bus["vm_pu"].max(), abs=1e-6)
    assert row["min_vm_pu"] == pytest.approx(w.res_bus["vm_pu"].min(), abs=1e-6)


def test_outaging_an_already_off_unit_reproduces_the_base_case(screened, decommitted):
    """A decommitted unit's outage row is the base case; a live PV ghost of it
    would move the voltages."""
    net = decommitted["net"]
    w = _pp_outage(net)
    for uid in CURTAILED:
        row = screened.loc[uid]
        assert row["converged"]
        assert row["max_vm_pu"] == pytest.approx(w.res_bus["vm_pu"].max(), abs=1e-6)
        assert row["min_vm_pu"] == pytest.approx(w.res_bus["vm_pu"].min(), abs=1e-6)


def test_n2_pairs_match_pandapower_double_outages_with_units_off(decommitted):
    s = decommitted
    net = s["net"]
    results, log = screen_n2(net, s["n2"], s["dispatch"], s["loads"], HOUR, s["reg"], prune_threshold_pct=0.0)
    results = results.set_index("contingency_id")
    lines = list(net.line.index)
    rng = np.random.default_rng(7)
    compared = 0
    for _ in range(60):
        a, b = rng.choice(len(lines), size=2, replace=False)
        cid_a, cid_b = _line_cid(net, lines[a]), _line_cid(net, lines[b])
        cid = "--".join(sorted([cid_a, cid_b]))
        if cid not in results.index or not results.at[cid, "converged"]:
            continue
        w = _pp_outage(net, lines=[lines[a], lines[b]])
        if w is None:
            continue
        assert results.at[cid, "max_vm_pu"] == pytest.approx(w.res_bus["vm_pu"].max(), abs=1e-6), cid
        assert results.at[cid, "min_vm_pu"] == pytest.approx(w.res_bus["vm_pu"].min(), abs=1e-6), cid
        compared += 1
    assert compared >= 8


def test_n1_ledger_records_the_in_service_fix():
    text = " ".join(N1_LEDGER).lower()
    for word in ("in_service", "init_from_pandapower", "decommitted"):
        assert word in text, word
