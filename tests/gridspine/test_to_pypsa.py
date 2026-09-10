import numpy as np
import pytest

from gridspine.ingest.pandapower_source import load_case39
from gridspine.producers.pypsa_nodal import LOAD_SHAPE, to_pypsa


def test_identity_mapping_bus_names():
    net = load_case39()
    n = to_pypsa(net)
    assert set(n.buses.index) == set(net.bus["name"])


def test_all_units_present_and_committable_flags():
    net = load_case39()
    n = to_pypsa(net)
    assert len(n.generators) == 10
    assert int(n.generators["committable"].sum()) == 9  # ext_grid unit is not
    slack_units = [u for u in n.generators.index if u.startswith("SLK_")]
    assert len(slack_units) == 1
    assert not n.generators.loc[slack_units[0], "committable"]


def test_snapshots_and_load_shape():
    net = load_case39()
    n = to_pypsa(net, snapshots=24)
    assert len(n.snapshots) == 24
    assert len(LOAD_SHAPE) == 24
    total_p = float(net.load["p_mw"].sum())
    peak = float(n.loads_t.p_set.sum(axis=1).max())
    assert abs(peak - total_p * max(LOAD_SHAPE)) < 1.0


def test_branch_counts():
    net = load_case39()
    n = to_pypsa(net)
    assert len(n.lines) == len(net.line)
    assert len(n.transformers) == len(net.trafo)


def test_load_shape_anchors():
    """Peak and valley are load-bearing: Task 6's commitment test needs the deep
    valley, and the driver's default peak hour is 19."""
    assert len(LOAD_SHAPE) == 24
    peak, valley = max(LOAD_SHAPE), min(LOAD_SHAPE)
    assert peak == 1.00
    assert valley == 0.45
    assert LOAD_SHAPE.index(peak) == 19
    assert LOAD_SHAPE.index(valley) == 3
    assert LOAD_SHAPE.count(peak) == 1
    assert LOAD_SHAPE.count(valley) == 1


def test_conversion_arithmetic_matches_source_columns():
    """Pin the unit arithmetic, recomputed from the net's own columns."""
    net = load_case39()
    n = to_pypsa(net)

    li = net.line.index[0]
    ln = net.line.loc[li]
    vn = net.bus.at[ln["from_bus"], "vn_kv"]
    line = n.lines.loc[f"L_{li:02d}"]
    assert line.r == pytest.approx(
        ln["r_ohm_per_km"] * ln["length_km"] / ln["parallel"], rel=1e-9)
    assert line.x == pytest.approx(
        ln["x_ohm_per_km"] * ln["length_km"] / ln["parallel"], rel=1e-9)
    assert line.s_nom == pytest.approx(
        np.sqrt(3) * vn * ln["max_i_ka"] * ln["parallel"], rel=1e-9)
    assert line.bus0 == net.bus.at[ln["from_bus"], "name"]
    assert line.bus1 == net.bus.at[ln["to_bus"], "name"]

    ti = net.trafo.index[0]
    tr = net.trafo.loc[ti]
    trafo = n.transformers.loc[f"T_{ti:02d}"]
    assert trafo.x == pytest.approx(tr["vk_percent"] / 100.0, rel=1e-9)
    assert trafo.r == pytest.approx(tr["vkr_percent"] / 100.0, rel=1e-9)
    assert trafo.s_nom == pytest.approx(tr["sn_mva"], rel=1e-9)
    assert trafo.bus0 == net.bus.at[tr["hv_bus"], "name"]
    assert trafo.bus1 == net.bus.at[tr["lv_bus"], "name"]

    g = net.gen.iloc[0]
    gen = n.generators.loc[g["name"]]
    assert gen.p_nom == pytest.approx(g["max_p_mw"], rel=1e-9)
    assert gen.marginal_cost == pytest.approx(10.0, rel=1e-9)
    assert gen.bus == net.bus.at[g["bus"], "name"]

    # The 10 + 4i stagger is invisible at i == 0, so pin a later unit as well.
    last_i = len(net.gen) - 1
    g_last = net.gen.iloc[last_i]
    gen_last = n.generators.loc[g_last["name"]]
    assert gen_last.p_nom == pytest.approx(g_last["max_p_mw"], rel=1e-9)
    assert gen_last.marginal_cost == pytest.approx(10.0 + 4.0 * last_i, rel=1e-9)


def test_parallel_circuits_scale_line_impedance_and_rating():
    """Every case39 line has parallel == 1, so the parallel factor is a no-op on
    the stock net and no assertion over it can see the term. Perturb one line to
    a double circuit: impedance must halve and rating must double."""
    net = load_case39()
    li = net.line.index[0]
    assert net.line.at[li, "parallel"] == 1, "fixture assumption: stock line is single"
    base = to_pypsa(net).lines.loc[f"L_{li:02d}"]

    net2 = load_case39()
    net2.line.loc[li, "parallel"] = 2
    doubled = to_pypsa(net2).lines.loc[f"L_{li:02d}"]

    ln = net2.line.loc[li]
    assert doubled.r == pytest.approx(
        ln["r_ohm_per_km"] * ln["length_km"] / 2.0, rel=1e-9)
    assert doubled.x == pytest.approx(
        ln["x_ohm_per_km"] * ln["length_km"] / 2.0, rel=1e-9)
    assert doubled.r == pytest.approx(base.r / 2.0, rel=1e-9)
    assert doubled.x == pytest.approx(base.x / 2.0, rel=1e-9)
    assert doubled.s_nom == pytest.approx(base.s_nom * 2.0, rel=1e-9)
