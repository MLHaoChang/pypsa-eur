"""`n1_severity_ac`: the AC screen's own worst-N-1 number, per hour, over a
year — the column `max_n1_severity` ranks on since follow-ups task F2.

Increment 3 locked "DC ranks the year, AC verifies the selection" on the
premise that AC N-1 over 8760 hours was unaffordable. Measured on case39 it is
~4 ms for all 46 branch outages (lightsim2grid) and ~0.3 ms per unit outage
on the same GridModel; the 0.5 s the screen used to cost was the pandapower
unit loop. The v3 year then showed the DC proxy anticorrelated with the AC
screen over the selected hours (rho -0.57, ruling 30), because the extremes
the study exists to find are overvoltage hours DC cannot see.

The property these tests pin is identity: the ranking column at hour h IS
the number `screen_n1` reports at hour h — one code path, no second
definition of severity. A base case that does not converge is a NaN, not an
exception, because one bad hour must not lose the year; it is reported.
"""
import copy

import numpy as np
import pandapower as pp
import pandas as pd
import pytest

from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net
from gridspine.schema.contracts import ContractError
from gridspine.static.contingency import (
    BaseCaseNotConverged,
    N1_LEDGER,
    branch_loading_pct,
    n1_severity_ac,
    screen_n1,
)
from gridspine.static.contingency_set import branch_contingencies, unit_contingencies
from gridspine.static.loadflow import apply_snapshot

from tests.gridspine.test_shortcircuit import _hour_tables

# hour 0: native peak, all on; hour 1: 80 % load, two units off; hour 2: 60 %
# load, three units off (a light hour of the kind the v3 year selected)
CASES = {0: (1.0, ()), 1: (0.8, ("G_BUS_33", "G_BUS_34")), 2: (0.6, ("G_BUS_33", "G_BUS_34", "S_BUS_34"))}


def _year_tables(net, reg, cases=CASES, scale_extra=None):
    d_parts, l_parts = [], []
    for hour, (lf, cur) in cases.items():
        d, l = _hour_tables(net, reg, curtailed=cur)
        d = d.assign(hour=hour)
        l = l.assign(hour=hour, p_mw=l["p_mw"] * lf, q_mvar=l["q_mvar"] * lf)
        if scale_extra and hour in scale_extra:
            l = l.assign(p_mw=l["p_mw"] * scale_extra[hour], q_mvar=l["q_mvar"] * scale_extra[hour])
        d_parts.append(d)
        l_parts.append(l)
    return pd.concat(d_parts, ignore_index=True), pd.concat(l_parts, ignore_index=True)


@pytest.fixture(scope="module")
def fixture():
    net = load_case39_res()
    reg = registry_from_net(net)
    dispatch, loads = _year_tables(net, reg)
    cset = pd.concat([branch_contingencies(net), unit_contingencies(reg)], ignore_index=True)
    return dict(net=net, reg=reg, dispatch=dispatch, loads=loads, cset=cset)


def _direct_worst(net, cset, dispatch, loads, hour, reg):
    work = copy.deepcopy(net)
    apply_snapshot(work, dispatch, loads, hour=hour, registry=reg)
    rows = screen_n1(work, cset, dispatch, loads, hour, reg)
    ok = rows[rows["converged"] & ~rows["islanded"]]
    return float(ok["severity"].max())


def test_the_year_column_is_the_screens_number_at_every_hour(fixture):
    f = fixture
    sev = n1_severity_ac(f["net"], f["cset"], f["dispatch"], f["loads"], f["reg"])
    assert sev.name == "n1_severity_ac"
    assert list(sev.index) == sorted(CASES)
    assert sev.index.name == "hour"
    for hour in CASES:
        assert sev.loc[hour] == pytest.approx(
            _direct_worst(f["net"], f["cset"], f["dispatch"], f["loads"], hour, f["reg"]), rel=1e-9
        )
    # the light, decommitted hour is the severe one in AC — the v3 pattern
    assert sev.loc[2] > sev.loc[0]


def test_the_callers_net_is_untouched_by_the_year_pass(fixture):
    f = fixture
    before = (f["net"].gen[["p_mw", "in_service"]].copy(), f["net"].load["p_mw"].copy())
    n1_severity_ac(f["net"], f["cset"], f["dispatch"], f["loads"], f["reg"])
    pd.testing.assert_frame_equal(f["net"].gen[["p_mw", "in_service"]], before[0])
    pd.testing.assert_series_equal(f["net"].load["p_mw"], before[1])


def test_a_diverging_base_hour_is_a_nan_not_an_exception(fixture):
    f = fixture
    dispatch, loads = _year_tables(f["net"], f["reg"], scale_extra={1: 4.0})
    sev = n1_severity_ac(f["net"], f["cset"], dispatch, loads, f["reg"])
    assert np.isnan(sev.loc[1])
    assert np.isfinite(sev.loc[0]) and np.isfinite(sev.loc[2])


def test_screen_n1_names_a_diverging_base_case_as_its_own_contract_error(fixture):
    f = fixture
    dispatch, loads = _year_tables(f["net"], f["reg"], scale_extra={1: 4.0})
    work = copy.deepcopy(f["net"])
    apply_snapshot(work, dispatch, loads, hour=1, registry=f["reg"])
    with pytest.raises(BaseCaseNotConverged):
        screen_n1(work, f["cset"], dispatch, loads, 1, f["reg"])
    assert issubclass(BaseCaseNotConverged, ContractError)


def test_hours_subset_is_honoured_and_unknown_hours_are_refused(fixture):
    f = fixture
    sev = n1_severity_ac(f["net"], f["cset"], f["dispatch"], f["loads"], f["reg"], hours=[2, 0])
    assert list(sev.index) == [0, 2]
    with pytest.raises(ContractError):
        n1_severity_ac(f["net"], f["cset"], f["dispatch"], f["loads"], f["reg"], hours=[7])


def test_every_unit_outage_matches_pandapower_at_the_light_decommitted_hour(fixture):
    """The unit loop moved from pandapower onto the GridModel for F2; pandapower
    stays the oracle, here for EVERY unit at the hour with three units off."""
    f = fixture
    net = copy.deepcopy(f["net"])
    apply_snapshot(net, f["dispatch"], f["loads"], hour=2, registry=f["reg"])
    rows = screen_n1(net, f["cset"], f["dispatch"], f["loads"], 2, f["reg"]).set_index("contingency_id")
    compared = 0
    for kind in ("gen", "sgen"):
        table = getattr(net, kind)
        for i in table.index:
            uid = table.at[i, "name"]
            w = copy.deepcopy(net)
            getattr(w, kind).at[i, "in_service"] = False
            try:
                pp.runpp(w)
            except pp.LoadflowNotConverged:
                assert not rows.at[uid, "converged"] and not rows.at[uid, "islanded"], uid
                continue
            row = rows.loc[uid]
            assert row["converged"], uid
            assert row["min_vm_pu"] == pytest.approx(w.res_bus["vm_pu"].min(), abs=1e-6), uid
            assert row["max_vm_pu"] == pytest.approx(w.res_bus["vm_pu"].max(), abs=1e-6), uid
            want = branch_loading_pct(w, w.res_line["i_from_ka"].values, w.res_trafo["i_hv_ka"].values).max()
            assert row["max_branch_loading_pct"] == pytest.approx(want, rel=1e-6), uid
            compared += 1
    assert compared >= 12


def test_n1_ledger_says_units_are_solved_on_the_gridmodel_and_pandapower_is_the_oracle():
    text = " ".join(N1_LEDGER).lower()
    assert "gridmodel" in text and "oracle" in text
