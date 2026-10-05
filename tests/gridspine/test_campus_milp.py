"""The electrical asset choice as a MILP, solved in a loop of successive
linearisations with an adaptive convergence rate (plan C11).

The oracles do not run the code under test:
* sensitivities are checked against a central finite difference of an AC
  load flow on a network written directly in pandapower here;
* the joint optimum is a brute force over every combination of the tiny
  library, each built and solved directly in pandapower here;
* the polygon is checked against the circle numerically;
* the returned compliance is checked against a direct pandapower solve of
  the returned campus file with the returned dispatch.
"""
import math

import pandapower as pp
import pytest

from gridspine.static.campus_flow import SizingCriteria
from gridspine.static.campus_invest import open_candidates, select_assets
from gridspine.static.campus_reactive import ReactiveRequirement
from tests.gridspine.test_campus_invest import (
    PROFILE, WIDE, H7, comp, dark_hours, hourly, library, lowpf_spec, single_spec, tr,
)


# --------------------------------------------------------------------------
# the open candidate sets: C8's generators without C8's adequacy filters
# --------------------------------------------------------------------------

def test_open_candidates_are_c8s_generators_without_the_adequacy_filter(tmp_path):
    lib = library(tmp_path, transformers=[tr("T40", 40.0, 0.8e6), tr("T63", 63.0, 1.2e6)],
                  capacitor_banks=[comp("capacitor_banks", "CAP3", 3.0, 120e3)])
    table, sel = dark_hours((2030, 25.0))
    out = select_assets(lowpf_spec(existing=False), table, sel, lib, ReactiveRequirement(15.0, "c", "code"), PROFILE)
    st = out["state"]
    by = {n.key: n for n in st.needs}
    lib_by_id = st.lib_by_id
    trafo = by["transformer TR1+TR2"]
    opened = open_candidates(trafo, lib, lib_by_id, SizingCriteria(), 50)
    # a redundant pair stays redundant: n = 2 or 3, every size, cheapest first
    assert sorted(c.items for c in opened) == sorted(
        (("transformer", i, n),) for i in ("T40", "T63") for n in (2, 3))
    assert {c.items for c in trafo.candidates} <= {c.items for c in opened}
    reactive = open_candidates(by["reactive"], lib, lib_by_id, SizingCriteria(), 50)
    assert reactive[0].label == "none" and reactive[0].items == ()
    assert sorted(c.label for c in reactive[1:]) == ["1 x CAP3", "2 x CAP3", "3 x CAP3"]
    # C8's own reactive need had a gap, so "none" was not among its candidates
    assert "none" not in {c.label for c in by["reactive"].candidates}


# --------------------------------------------------------------------------
# sensitivities: the load-flow Jacobian against a finite-difference re-solve
# --------------------------------------------------------------------------

def _quantities(net):
    """Every monitored quantity of a solved net, read from pandapower's own
    result tables."""
    out = {("pcc_q",): float(net.res_ext_grid["q_mvar"].sum())}
    for i, name in zip(net.bus.index, net.bus["name"]):
        out[("vm", name)] = float(net.res_bus.at[i, "vm_pu"])
    for i, name in zip(net.trafo.index, net.trafo["name"]):
        if net.trafo.at[i, "in_service"]:
            out[("trafo_s", name)] = math.hypot(net.res_trafo.at[i, "p_hv_mw"], net.res_trafo.at[i, "q_hv_mvar"])
            out[("trafo_load", name)] = net.res_trafo.at[i, "loading_percent"] / 100 * net.trafo.at[i, "sn_mva"]
    for i, name in zip(net.line.index, net.line["name"]):
        out[("line_i", name)] = float(net.res_line.at[i, "i_ka"])
    return out


def _fd(net, bus, h=0.02):
    """Central difference of every quantity for +-h Mvar injected at ``bus``."""
    import copy
    vals = []
    for dq in (h, -h):
        n = copy.deepcopy(net)
        pp.create_sgen(n, int(n.bus.index[n.bus["name"] == bus][0]), p_mw=0.0, q_mvar=dq)
        pp.runpp(n)
        vals.append(_quantities(n))
    return {k: (vals[0][k] - vals[1][k]) / (2 * h) for k in vals[0]}


def weak_net(out=None):
    """The test campus on a weak grid (Sk 150 MVA) with a 15 km cable, two
    parallel cable runs and a capacitor step in: a strongly loaded,
    strongly non-linear point."""
    from tests.gridspine.test_campus import direct_net
    net = direct_net()
    net.ext_grid.at[0, "s_sc_max_mva"] = 150.0
    net.line.at[0, "length_km"] = 15.0
    net.line.at[0, "parallel"] = 2
    net.load.at[0, "p_mw"], net.load.at[0, "q_mvar"] = 45.0, 20.0
    net.sgen.loc[net.sgen["name"] == "BESS1", ["p_mw", "q_mvar"]] = (5.0, 3.0)
    pp.create_shunt(net, 1, q_mvar=-2.0, p_mw=0.0, step=1, max_step=2, name="CAP_1")
    if out is not None:
        net.trafo.at[out, "in_service"] = False
    pp.runpp(net)
    return net


@pytest.mark.parametrize("out", [None, 1])
def test_jacobian_sensitivities_match_a_finite_difference_ac_re_solve(out):
    from gridspine.static.campus_milp import sensitivities
    net = weak_net(out)
    got = sensitivities(net, ["MV1", "MV2", "PCC"])
    checked = 0
    for bus in ("MV1", "MV2", "PCC"):
        want = _fd(net, bus)
        for key, d in want.items():
            assert got[key][bus] == pytest.approx(d, rel=1e-4, abs=1e-7), (bus, key)
            checked += 1
    assert checked == 3 * (1 + 3 + (2 if out is None else 1) * 2 + 1)
    # the signs a planner expects: Q injected at MV2 lifts its voltage, and
    # reduces what the grid supplies by more than itself (the losses fall)
    assert got[("vm", "MV2")]["MV2"] > 0
    assert got[("pcc_q",)]["MV2"] < -1.0
    assert got[("pcc_q",)]["PCC"] == -1.0 and got[("vm", "MV1")]["PCC"] == 0.0


# --------------------------------------------------------------------------
# the inverter capability polygon is inscribed in its circle
# --------------------------------------------------------------------------

def test_the_capability_polygon_is_inscribed_in_the_circle_and_touches_it_at_its_vertices():
    from gridspine.static.campus_milp import POLYGON_SIDES, polygon, q_range
    import numpy as np
    normals, apothem = polygon()
    assert POLYGON_SIDES == 8 and normals.shape == (8, 2)
    s = 22.0
    # every point of the polygon is inside the circle: sample its boundary
    # by walking each side between its two vertices (intersections, solved here)
    for i in range(8):
        a1, a2 = normals[i], normals[(i + 1) % 8]
        vertex = np.linalg.solve(np.array([a1, a2]), np.array([apothem * s, apothem * s]))
        assert np.hypot(*vertex) == pytest.approx(s, rel=1e-12)           # the vertices lie on the circle
    rng = np.random.default_rng(0)
    pts = rng.uniform(-s, s, size=(20000, 2))
    inside = (pts @ normals.T <= apothem * s + 1e-12).all(axis=1)
    assert inside.mean() == pytest.approx(2 * math.sqrt(2) / 4, abs=0.01)     # the octagon's area, 2 sqrt(2) S^2
    assert (np.hypot(pts[inside, 0], pts[inside, 1]) <= s + 1e-9).all()
    # it is not trivially small: it contains the circle of radius S cos(pi/8)
    near = np.hypot(pts[:, 0], pts[:, 1]) <= s * math.cos(math.pi / 8) - 1e-9
    assert inside[near].all()
    # the Q range at a fixed P: inside the circle's, equal at P = 0 and at a vertex
    for p in np.linspace(-s, s, 41):
        lo, hi = q_range(p, s)
        circle = math.sqrt(max(s * s - p * p, 0.0))
        assert -circle - 1e-9 <= lo <= 0.0 <= hi <= circle + 1e-9
        assert lo == pytest.approx(-hi)
    assert q_range(0.0, s) == pytest.approx((-s, s))
    p45 = s * math.cos(math.pi / 4)
    assert q_range(p45, s)[1] == pytest.approx(s * math.sin(math.pi / 4))
    # and at its worst it gives up 1 - cos(pi/8) of S (midway between vertices)
    p = s * math.cos(math.pi / 8) * math.cos(3 * math.pi / 8)
    assert math.hypot(p, q_range(p, s)[1]) == pytest.approx(s * math.cos(math.pi / 8))
