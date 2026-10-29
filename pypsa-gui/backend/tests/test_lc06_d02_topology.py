"""LC-06 S11, LC-D02: topology built and edited through the copilot's component tools.

The seam is ``create_component``, ``delete_component``, ``cascade_delete_bus``,
``validate_network`` and ``save_project``. The oracles are counted by hand and read
independently: the network file written to disk is opened with ``pypsa`` alone, and
the tool-built network is solved on a copy.

Hand network (project "Alpha" supplies B1, G1 20 MW at 10 EUR/MWh and L1 5 MW at B1):
B2 carries G2 (7 MW at 50 EUR/MWh) and L2 3 MW; B3 carries L3 2 MW; Ln1 joins B1-B2
and Ln2 joins B2-B3 (x 0.1, s_nom 50 MVA).
- Kirchhoff: G1 is cheaper than G2, so it serves all 10 MW. Ln1 carries the 5 MW
  (L2 + L3) from B1 to B2 and Ln2 the 2 MW (L3) from B2 to B3, with the sign of
  bus0 -> bus1. A swapped bus0/bus1 or a lost line flips or zeroes them.
- Reference count: bus B2 is referenced by G2, L2, Ln1 and Ln2, four rows. A plain
  bus delete leaves exactly those four dangling and ``validate_network`` reports
  ``bus_ref_unknown`` for those four names and no others. ``cascade_delete_bus``
  removes B2 and those four: B1 and B3, G1, L1 and L3 remain, in memory and in the
  saved file, nothing dangles, and B3 is reported as an island with no supply.
"""
# ruff: noqa: F811 -- shared public harness fixtures
import pytest
import pypsa

from services import chat_tools, project_registry
from services.pypsa_service import PyPSAService
from tests.test_lc06_d01_projects import alpha  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401

REL = 1e-6


@pytest.fixture
def chain(alpha):
    c = chat_tools
    for bus in ("B2", "B3"):
        c.create_component("Bus", bus, {"v_nom": 110})
    c.create_component("Generator", "G2", {"bus": "B2", "p_nom": 7, "marginal_cost": 50})
    c.create_component("Load", "L2", {"bus": "B2", "p_set": 3})
    c.create_component("Load", "L3", {"bus": "B3", "p_set": 2})
    c.create_component("Line", "Ln1", {"bus0": "B1", "bus1": "B2", "x": 0.1, "s_nom": 50})
    c.create_component("Line", "Ln2", {"bus0": "B2", "bus1": "B3", "x": 0.1, "s_nom": 50})


def _codes(code):
    return sorted(i["name"] for i in chat_tools.validate_network()["issues"] if i["code"] == code)


def _members(n):
    return {k: sorted(getattr(n, k).index) for k in ("buses", "lines", "generators", "loads")}


def _saved_members():
    chat_tools.save_project("Alpha", force=True)
    with chat_tools._acting() as (db, user):
        path = project_registry.project_dir(project_registry.resolve_project(db, user, "Alpha"))
    return _members(pypsa.Network(str(path / "network.nc")))


def test_the_built_topology_carries_the_hand_kirchhoff_flows(chain):
    n = PyPSAService.get_network().copy()
    status, _ = n.optimize(solver_name="highs")
    assert status == "ok"
    assert n.generators_t.p["G1"].iloc[0] == pytest.approx(10.0, rel=REL)
    assert n.generators_t.p["G2"].iloc[0] == pytest.approx(0.0, abs=1e-6)
    assert n.lines_t.p0["Ln1"].iloc[0] == pytest.approx(5.0, rel=REL)
    assert n.lines_t.p0["Ln2"].iloc[0] == pytest.approx(2.0, rel=REL)
    assert n.lines_t.p1["Ln2"].iloc[0] == pytest.approx(-2.0, rel=REL)


def test_a_plain_bus_delete_leaves_exactly_the_four_references_dangling(chain):
    chat_tools.delete_component("Bus", "B2")
    assert _codes("bus_ref_unknown") == ["G2", "L2", "Ln1", "Ln2"]


def test_a_cascade_delete_removes_the_bus_and_its_four_references_and_nothing_else(chain):
    assert _codes("bus_ref_unknown") == []
    chat_tools.cascade_delete_bus("B2")
    expected = {"buses": ["B1", "B3"], "lines": [], "generators": ["G1"], "loads": ["L1", "L3"]}
    assert _members(PyPSAService.get_network()) == expected
    assert _codes("bus_ref_unknown") == []
    assert "island_no_supply" in {i["code"] for i in chat_tools.validate_network()["issues"]}
    assert _saved_members() == expected
