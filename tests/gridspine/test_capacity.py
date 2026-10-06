"""Increment 9: connection capacity at a bus.

Capacity at bus B is the most MW that, added at B and balanced pro-rata over
the committed synchronous units (the slack taking what they cannot), creates
NO NEW violation and WORSENS NO EXISTING ONE, intact and under every N-1
outage. The criteria are the N-1 screen's own (100 % loading, 0.9-1.1 pu).

Every grid below is small enough that the answer is known before the code
runs: a single line's rating, a parallel pair's N-1 rating, a long line's
voltage drop. The last test takes the AC answer on case39 and checks it with
pandapower directly, with no capacity code in the loop.
"""
import math

import numpy as np
import pandapower as pp
import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.static.capacity import (
    BINDING_KINDS,
    CapacityCriteria,
    apply_connection,
    capacity_ac,
    capacity_dc,
    validate_capacity,
)
from gridspine.static.contingency_set import branch_contingencies

KV = 110.0
I_LINE = 0.5                                   # kA -> sqrt(3) * 110 * 0.5 = 95.26 MVA
RATING = math.sqrt(3.0) * KV * I_LINE


def _bus(net, n):
    return pp.create_bus(net, vn_kv=KV, name=f"BUS_{n:02d}")


def _line(net, a, b, km=10.0, i_ka=I_LINE):
    return pp.create_line_from_parameters(
        net, from_bus=a, to_bus=b, length_km=km,
        r_ohm_per_km=0.05, x_ohm_per_km=0.4, c_nf_per_km=0.0, max_i_ka=i_ka,
    )


def _slack(net, b):
    pp.create_ext_grid(net, bus=b, vm_pu=1.0, name="SLK_BUS_01")


def radial():
    """BUS_01 (slack) -- BUS_02 -- BUS_03, 30 MW already at BUS_03. The
    BUS_02-BUS_03 line (95.26 MVA) is the only thing that can bind."""
    net = pp.create_empty_network(sn_mva=100.0)
    b1, b2, b3 = _bus(net, 1), _bus(net, 2), _bus(net, 3)
    _slack(net, b1)
    _line(net, b1, b2, i_ka=4 * I_LINE)
    _line(net, b2, b3)
    pp.create_load(net, bus=b3, p_mw=30.0, q_mvar=0.0)
    return net


def parallel(existing_mw):
    """BUS_01 (slack) = two identical circuits = BUS_02. Intact, each carries
    half; lose one and the other carries everything."""
    net = pp.create_empty_network(sn_mva=100.0)
    b1, b2 = _bus(net, 1), _bus(net, 2)
    _slack(net, b1)
    _line(net, b1, b2)
    _line(net, b1, b2)
    pp.create_load(net, bus=b2, p_mw=existing_mw, q_mvar=0.0)
    return net


def long_line():
    """A 200 km line rated far above anything that flows: voltage binds first."""
    net = pp.create_empty_network(sn_mva=100.0)
    b1, b2 = _bus(net, 1), _bus(net, 2)
    _slack(net, b1)
    _line(net, b1, b2, km=200.0, i_ka=10.0)
    pp.create_load(net, bus=b2, p_mw=5.0, q_mvar=0.0)
    return net


def _cset(net):
    return branch_contingencies(net)


# --------------------------------------------------------------------------
# DC: closed form
# --------------------------------------------------------------------------

def test_dc_estimate_on_a_radial_line_is_rating_minus_existing_flow():
    net = radial()
    dc = capacity_dc(net, ["BUS_03"], "load")
    row = dc.loc["BUS_03"]
    assert row["dc_estimate_mw"] == pytest.approx(RATING - 30.0, abs=1e-6)
    assert row["dc_binding_element"] == "BUS_02-BUS_03-1"


def test_dc_n1_on_a_parallel_pair_binds_on_the_surviving_circuit():
    net = parallel(20.0)
    dc = capacity_dc(net, ["BUS_02"], "load")
    row = dc.loc["BUS_02"]
    # Intact would allow 2R - 20; losing one circuit leaves R - 20.
    assert row["dc_estimate_mw"] == pytest.approx(RATING - 20.0, abs=1e-6)
    assert {row["dc_binding_element"], row["dc_binding_contingency"]} == {
        "BUS_01-BUS_02-1", "BUS_01-BUS_02-2"}


# --------------------------------------------------------------------------
# AC: bisection on the no-new, no-worse rule
# --------------------------------------------------------------------------

def test_ac_capacity_on_a_radial_line_binds_on_that_line_intact():
    net = radial()
    r = capacity_ac(net, "BUS_03", "load", _cset(net))
    assert r["binding_kind"] == "thermal_intact"
    assert r["binding_element"] == "BUS_02-BUS_03-1"
    # AC carries Q (pf 0.98) and losses, so it lands below the DC 65.3 MW.
    assert 50.0 < r["capacity_mw"] < RATING - 30.0


def test_ac_capacity_on_a_parallel_pair_is_set_by_n1_not_intact():
    net = parallel(20.0)
    r = capacity_ac(net, "BUS_02", "load", _cset(net))
    assert r["binding_kind"] == "thermal_n1"
    assert {r["binding_element"], r["binding_contingency"]} == {
        "BUS_01-BUS_02-1", "BUS_01-BUS_02-2"}
    assert 55.0 < r["capacity_mw"] < RATING - 20.0     # far below the intact ~170 MW


def test_an_existing_overload_is_not_zeroed_away_but_is_not_allowed_to_deepen():
    # 120 MW over two 95 MVA circuits: fine intact, ~126 % after losing one.
    net = parallel(120.0)
    crit = CapacityCriteria()
    load = capacity_ac(net, "BUS_02", "load", _cset(net), crit)
    # Any added load deepens the existing N-1 overload beyond the tolerance.
    assert load["capacity_mw"] <= 2.0
    assert load["binding_kind"] == "thermal_n1"
    # Generation at the same bus RELIEVES it, so its capacity is large: the flow
    # reverses and may grow back to the overload it had (~120 MW), not beyond.
    gen = capacity_ac(net, "BUS_02", "generation", _cset(net), crit)
    assert 200.0 < gen["capacity_mw"] < 260.0


def test_a_limit_set_by_an_existing_overload_says_so():
    # Found in the browser run: at BUS_16 on a real UC hour the AC answer was
    # 13.8 MW, bound by BUS_02-BUS_03, which was ALREADY overloaded under N-1
    # before anything connected. That number is the worsening tolerance divided
    # by the bus's distribution factor, not room on the network, and read as
    # "13.8 MW available" it misleads. So a result says whether what binds it
    # was already violated at 0 MW.
    pre = capacity_ac(parallel(120.0), "BUS_02", "load", _cset(parallel(120.0)))
    assert pre["binding_preexisting"] is True
    fresh = capacity_ac(parallel(20.0), "BUS_02", "load", _cset(parallel(20.0)))
    assert fresh["binding_preexisting"] is False
    assert capacity_dc(parallel(120.0), ["BUS_02"], "load").loc["BUS_02", "dc_binding_preexisting"]
    assert not capacity_dc(parallel(20.0), ["BUS_02"], "load").loc["BUS_02", "dc_binding_preexisting"]


def test_dc_applies_the_same_worsening_tolerance_as_ac():
    # One definition, two methods: DC used to allow an existing overload no
    # growth at all (0.0 MW) where AC allowed its 0.5 % tolerance (13.8 MW on
    # the same row), so the two columns disagreed about the rule, not the physics.
    crit = CapacityCriteria()
    dc = capacity_dc(parallel(120.0), ["BUS_02"], "load", crit).loc["BUS_02", "dc_estimate_mw"]
    assert dc == pytest.approx(crit.worsen_tol_pct / 100.0 * RATING, rel=1e-6)


def test_voltage_binds_on_a_long_line():
    net = long_line()
    r = capacity_ac(net, "BUS_02", "load", _cset(net))
    assert r["binding_kind"] == "v_low_intact"
    assert r["binding_element"] == "BUS_02"
    assert r["capacity_mw"] > 0.0


def test_nothing_binding_up_to_the_cap_is_reported_as_the_cap_not_infinity():
    net = parallel(0.0)
    for i in net.line.index:
        net.line.at[i, "max_i_ka"] = 100.0
    crit = CapacityCriteria(cap_mw=50.0)
    r = capacity_ac(net, "BUS_02", "load", _cset(net), crit)
    assert r["binding_kind"] == "none_up_to_cap"
    assert r["capacity_mw"] == pytest.approx(50.0)


def test_the_callers_net_is_not_mutated():
    net = radial()
    before = (net.load.copy(), net.gen.copy(), len(net.sgen))
    capacity_ac(net, "BUS_03", "load", _cset(net))
    capacity_dc(net, ["BUS_03"], "load")
    pd.testing.assert_frame_equal(net.load, before[0])
    pd.testing.assert_frame_equal(net.gen, before[1])
    assert len(net.sgen) == before[2]


def test_an_unknown_bus_or_kind_is_refused():
    net = radial()
    with pytest.raises(ContractError, match="BUS_99"):
        capacity_ac(net, "BUS_99", "load", _cset(net))
    with pytest.raises(ContractError, match="storage"):
        capacity_ac(net, "BUS_03", "storage", _cset(net))


# --------------------------------------------------------------------------
# Balancing: pro-rata over committed units (the owner's decision)
# --------------------------------------------------------------------------

def _two_gen_net():
    net = pp.create_empty_network(sn_mva=100.0)
    b1, b2, b3, b4 = (_bus(net, n) for n in (1, 2, 3, 4))
    _slack(net, b1)
    for a, b in ((b1, b2), (b2, b3), (b3, b4), (b1, b4)):
        _line(net, a, b, i_ka=5.0)
    pp.create_gen(net, bus=b2, p_mw=50.0, max_p_mw=150.0, min_p_mw=0.0, vm_pu=1.0, name="G_BUS_02")
    pp.create_gen(net, bus=b3, p_mw=100.0, max_p_mw=150.0, min_p_mw=20.0, vm_pu=1.0, name="G_BUS_03")
    pp.create_gen(net, bus=b4, p_mw=0.0, max_p_mw=300.0, min_p_mw=0.0, vm_pu=1.0,
                  name="G_BUS_04", in_service=False)
    pp.create_load(net, bus=b4, p_mw=200.0, q_mvar=0.0)
    return net


def test_load_is_supplied_by_committed_units_in_proportion_to_upward_headroom():
    net = _two_gen_net()
    apply_connection(net, "BUS_04", 30.0, "load")
    # headroom 100 and 50 -> shares 20 and 10; the decommitted unit takes none
    p = net.gen.set_index("name")["p_mw"]
    assert p["G_BUS_02"] == pytest.approx(70.0)
    assert p["G_BUS_03"] == pytest.approx(110.0)
    assert p["G_BUS_04"] == pytest.approx(0.0)
    assert net.load["p_mw"].sum() == pytest.approx(230.0)


def test_beyond_the_committed_headroom_the_slack_takes_the_rest():
    net = _two_gen_net()
    apply_connection(net, "BUS_04", 200.0, "load")
    p = net.gen.set_index("name")["p_mw"]
    assert p["G_BUS_02"] == pytest.approx(150.0)      # at max
    assert p["G_BUS_03"] == pytest.approx(150.0)      # at max; 50 MW left to the slack


def test_generation_backs_committed_units_down_in_proportion_to_downward_headroom():
    net = _two_gen_net()
    apply_connection(net, "BUS_04", 26.0, "generation")
    # downward headroom 50 and 80 -> shares 10 and 16
    p = net.gen.set_index("name")["p_mw"]
    assert p["G_BUS_02"] == pytest.approx(40.0)
    assert p["G_BUS_03"] == pytest.approx(84.0)
    assert net.sgen["p_mw"].sum() == pytest.approx(26.0)


def test_a_load_connection_carries_its_power_factor():
    net = _two_gen_net()
    apply_connection(net, "BUS_04", 98.0, "load", pf=0.98)
    added = net.load.iloc[-1]
    assert added["p_mw"] == pytest.approx(98.0)
    assert added["q_mvar"] == pytest.approx(98.0 * math.tan(math.acos(0.98)))


# --------------------------------------------------------------------------
# The property the feature exists for, checked with pandapower alone
# --------------------------------------------------------------------------

def _from_side_loading(net, element_id):
    """From-side current over rating, as the N-1 screen defines loading, read
    straight off pandapower's own result tables."""
    from gridspine.static.loadflow import branch_keys
    keys = branch_keys(net)
    ids = [f"{k.from_bus}-{k.to_bus}-{k.ckt}" for k in keys.itertuples(index=False)]
    pos = ids.index(element_id)
    nl = len(net.line)
    if pos < nl:
        i = net.line.index[pos]
        return net.res_line.at[i, "i_from_ka"] / (net.line.at[i, "max_i_ka"] * net.line.at[i, "parallel"]) * 100
    t = net.trafo.index[pos - nl]
    rated = net.trafo.at[t, "sn_mva"] / (math.sqrt(3) * net.trafo.at[t, "vn_hv_kv"])
    return net.res_trafo.at[t, "i_hv_ka"] / rated * 100


def _outage(net, contingency_id):
    from gridspine.static.loadflow import branch_keys
    keys = branch_keys(net)
    ids = [f"{k.from_bus}-{k.to_bus}-{k.ckt}" for k in keys.itertuples(index=False)]
    pos = ids.index(contingency_id)
    nl = len(net.line)
    if pos < nl:
        net.line.at[net.line.index[pos], "in_service"] = False
    else:
        net.trafo.at[net.trafo.index[pos - nl], "in_service"] = False


@pytest.mark.parametrize("bus, kind", [
    # Native case39 already has BUS_02-BUS_03 at ~112 % after losing
    # BUS_26-BUS_27, and pro-rata puts ~72 % of any new load on G_BUS_30,
    # whose power crosses it: a load anywhere may only deepen it by the
    # tolerance. This case exercises the WORSENED-existing branch of the rule.
    ("BUS_16", "load"),
    # Generation at BUS_03 relieves that branch and runs until it creates a
    # NEW N-1 overload (~940 MW): the other branch of the rule.
    ("BUS_03", "generation"),
])
def test_case39_capacity_holds_at_the_answer_and_breaks_just_beyond_it(bus, kind):
    """AC capacity on native case39, then pandapower alone: at the answer the
    binding element sits at or under its limit (or its own base level, if it
    was already over); 2 x tolerance beyond, it crosses."""
    from gridspine.ingest.pandapower_source import load_case39
    import copy

    net = load_case39()
    crit = CapacityCriteria(tol_mw=1.0)
    r = capacity_ac(net, bus, kind, _cset(net), crit)
    assert r["binding_kind"] in ("thermal_intact", "thermal_n1"), r
    assert 0.0 < r["capacity_mw"] < crit.cap_mw

    def loading_at(p_mw):
        w = copy.deepcopy(net)
        apply_connection(w, bus, p_mw, kind, pf=crit.load_pf)
        if r["binding_contingency"]:
            _outage(w, r["binding_contingency"])
        pp.runpp(w)
        return _from_side_loading(w, r["binding_element"])

    base = loading_at(0.0)
    limit = max(crit.loading_max_pct, base + crit.worsen_tol_pct)
    assert loading_at(r["capacity_mw"]) <= limit + 1e-6
    assert loading_at(r["capacity_mw"] + 2 * crit.tol_mw) > limit


# --------------------------------------------------------------------------
# The artifact's schema
# --------------------------------------------------------------------------

def _row(**over):
    row = {
        "bus": "BUS_16", "hour": 0, "kind": "load", "capacity_mw": 412.0,
        "dc_estimate_mw": 455.0, "binding_kind": "thermal_n1",
        "binding_element": "BUS_16-BUS_17-1", "binding_contingency": "BUS_16-BUS_19-1",
        "binding_preexisting": False, "method": "ac",
    }
    row.update(over)
    return row


def test_a_valid_capacity_table_round_trips():
    df = validate_capacity(pd.DataFrame([_row(), _row(kind="generation", hour=3)]))
    assert list(df["kind"]) == ["load", "generation"]


@pytest.mark.parametrize("over, match", [
    ({"kind": "storage"}, "kind"),
    ({"capacity_mw": -1.0}, "capacity_mw"),
    ({"binding_kind": "vibes"}, "binding_kind"),
    ({"method": "guess"}, "method"),
    ({"binding_preexisting": "maybe"}, "binding_preexisting"),
])
def test_an_impossible_capacity_row_is_refused(over, match):
    with pytest.raises(ContractError, match=match):
        validate_capacity(pd.DataFrame([_row(**over)]))


def test_a_missing_column_is_refused():
    with pytest.raises(ContractError, match="binding_kind"):
        validate_capacity(pd.DataFrame([_row()]).drop(columns=["binding_kind"]))


def test_binding_kinds_are_the_documented_set():
    assert set(BINDING_KINDS) == {
        "thermal_intact", "thermal_n1", "v_low_intact", "v_high_intact",
        "v_low_n1", "v_high_n1", "n1_divergence", "ac_divergence", "none_up_to_cap",
    }
