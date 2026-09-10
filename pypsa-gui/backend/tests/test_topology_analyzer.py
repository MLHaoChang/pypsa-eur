"""
Graph-level diagnosis: islands and what each one can serve.

`validation_service` checks values. A network passes every one of those checks
and is still two disconnected halves, one holding the demand and the other the
plant meant to serve it — and the LP answers that with `infeasible` and a
traceback naming neither. These tests pin the two claims the module is allowed
to make, and the several it is not.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services import chat_tools as T
from services import topology_analyzer as A
from services.solver_service import SolverConfig
from services.validation_service import has_errors, validate_for_run


def _two_islands(*, link: bool = False, supply_on_b: bool = True) -> pypsa.Network:
    """
    Two halves. A carries generation, B carries demand. They are joined only
    if `link` — which is the point of the link test: a link IS a connection
    for the energy balance, whatever `sub_networks` says.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    for bus in ("a1", "a2", "b1", "b2"):
        n.add("Bus", bus)
    n.add("Line", "a", bus0="a1", bus1="a2", x=0.1, r=0.01, s_nom=100.0)
    n.add("Line", "b", bus0="b1", bus1="b2", x=0.1, r=0.01, s_nom=100.0)
    if link:
        n.add("Link", "bridge", bus0="a2", bus1="b1", p_nom=100.0)
    n.add("Generator", "gen_a", bus="a1", p_nom=500.0)
    n.add("Load", "load_b", bus="b2", p_set=100.0)
    if supply_on_b:
        n.add("Generator", "gen_b", bus="b1", p_nom=500.0)
    return n


# ── Islanding ──────────────────────────────────────────────────────────────


def test_two_halves_are_two_islands():
    report = A.analyse_topology(_two_islands())
    assert report["n_islands"] == 2
    assert sorted(i["n_buses"] for i in report["islands"]) == [2, 2]


def test_a_link_counts_as_a_connection():
    """
    PyPSA's `sub_networks` deliberately exclude links — a converter is not an
    impedance. For FEASIBILITY it plainly connects, and reporting an
    electrolyser bus as islanded would send the user chasing a line that
    should not exist.
    """
    report = A.analyse_topology(_two_islands(link=True))
    assert report["n_islands"] == 1


def test_multi_port_link_buses_connect_too():
    """bus2 on a CHP/electrolyser is a real connection, not decoration."""
    n = pypsa.Network()
    for bus in ("elec", "h2", "heat"):
        n.add("Bus", bus)
    n.add("Link", "electrolyser", bus0="elec", bus1="h2", bus2="heat",
          p_nom=50.0, efficiency2=0.3)
    assert A.analyse_topology(n)["n_islands"] == 1


def test_island_ids_are_stable_across_calls():
    """The ids appear in messages a user reads twice."""
    n = _two_islands()
    first = [(i["id"], i["buses"]) for i in A.analyse_topology(n)["islands"]]
    second = [(i["id"], i["buses"]) for i in A.analyse_topology(n)["islands"]]
    assert first == second


def test_a_bus_with_no_branch_is_isolated():
    n = _two_islands()
    n.add("Bus", "stranded")
    report = A.analyse_topology(n)
    assert report["isolated_buses"] == ["stranded"]
    assert report["n_islands"] == 3


def test_an_empty_network_is_silent():
    report = A.analyse_topology(pypsa.Network())
    assert report["n_islands"] == 0
    assert report["islands"] == []


# ── Verdicts ───────────────────────────────────────────────────────────────


def test_demand_with_no_injection_is_no_supply():
    report = A.analyse_topology(_two_islands(supply_on_b=False))
    verdicts = {i["verdict"] for i in report["islands"]}
    assert "no_supply" in verdicts
    island = next(i for i in report["islands"] if i["verdict"] == "no_supply")
    assert "NO injection capacity" in island["reason"]
    assert "VOLL" in island["reason"], "the remedy has to be named"


def test_an_island_with_no_load_is_no_demand():
    report = A.analyse_topology(_two_islands(supply_on_b=False))
    island = next(i for i in report["islands"] if i["verdict"] == "no_demand")
    assert island["peak_load_mw"] == 0


def test_a_certain_shortfall_is_reported():
    n = _two_islands()
    n.generators.loc["gen_b", "p_nom"] = 10.0     # 100 MW of demand
    island = next(i for i in A.analyse_topology(n)["islands"]
                  if i["verdict"] == "under_capacity")
    assert island["peak_load_mw"] == pytest.approx(100.0)
    assert island["nameplate_mw"] == pytest.approx(10.0)
    assert "certain, not indicative" in island["reason"]


def test_no_shortfall_is_claimed_when_anything_is_extendable():
    """The LP may simply build what is missing — a claim here would be wrong."""
    n = _two_islands()
    n.generators.loc["gen_b", "p_nom"] = 10.0
    n.generators.loc["gen_b", "p_nom_extendable"] = True
    verdicts = {i["verdict"] for i in A.analyse_topology(n)["islands"]}
    assert "under_capacity" not in verdicts


def test_a_store_withdraws_the_shortfall_claim():
    """
    A Store cannot be a standing source — it must be charged first — so it
    never counts toward nameplate. But its presence is enough that a
    shortfall is no longer certain, which is the only claim being made.
    """
    n = _two_islands()
    n.generators.loc["gen_b", "p_nom"] = 10.0
    n.add("Store", "battery", bus="b1", e_nom=500.0)
    island = next(i for i in A.analyse_topology(n)["islands"]
                  if "b1" in i["buses"])
    assert island["verdict"] != "under_capacity"
    assert island["nameplate_mw"] == pytest.approx(10.0)


def test_storage_units_count_toward_nameplate():
    n = _two_islands(supply_on_b=False)
    n.add("StorageUnit", "battery", bus="b1", p_nom=250.0)
    island = next(i for i in A.analyse_topology(n)["islands"]
                  if "b1" in i["buses"])
    assert island["nameplate_mw"] == pytest.approx(250.0)
    assert island["verdict"] == "ok"


# ── Peak demand ────────────────────────────────────────────────────────────


def test_peak_is_taken_per_snapshot_not_per_load():
    """
    Two loads peaking in different hours must not add into a peak that never
    happens — that would manufacture a shortfall out of arithmetic.
    """
    n = _two_islands(supply_on_b=False)
    n.add("Load", "load_b2", bus="b1", p_set=0.0)
    n.loads_t.p_set["load_b"] = pd.Series([100, 0, 100, 0], index=n.snapshots,
                                          dtype=float)
    n.loads_t.p_set["load_b2"] = pd.Series([0, 100, 0, 100], index=n.snapshots,
                                           dtype=float)
    island = next(i for i in A.analyse_topology(n)["islands"]
                  if "b1" in i["buses"])
    assert island["peak_load_mw"] == pytest.approx(100.0)


def test_a_profile_wins_over_the_static_value():
    """It is what PyPSA reads."""
    n = _two_islands(supply_on_b=False)
    n.loads_t.p_set["load_b"] = pd.Series([250.0] * 4, index=n.snapshots)
    island = next(i for i in A.analyse_topology(n)["islands"]
                  if "b1" in i["buses"])
    assert island["peak_load_mw"] == pytest.approx(250.0)


# ── Preflight wiring ───────────────────────────────────────────────────────


def test_islanding_reaches_preflight_as_a_warning():
    issues = validate_for_run(_two_islands(supply_on_b=False), SolverConfig())
    codes = {i.code for i in issues}
    assert "network_islanded" in codes
    assert "island_no_supply" in codes


def test_topology_findings_never_block_a_run():
    """
    An unservable island is an infeasibility at VOLL = 0 and priced lost load
    above it. `topology_issues` does not know the config, so it says which it
    is and blocks neither.
    """
    issues = A.topology_issues(_two_islands(supply_on_b=False))
    assert issues
    assert all(i.severity == "warning" for i in issues)
    assert not has_errors(issues)


def test_a_healthy_connected_network_is_silent():
    n = _two_islands(link=True)
    assert A.topology_issues(n) == []


# ── The chat tool ──────────────────────────────────────────────────────────


def test_tool_is_registered_and_read_tier():
    from services import chat_service
    from services import chat_tools_schema as S

    assert callable(T.DISPATCHERS["diagnose_network"])
    assert any(t["name"] == "diagnose_network" for t in S.TOOLS)
    assert chat_service._safety_tier_for("diagnose_network") == "read"


def test_bus_membership_is_omitted_by_default(install_network):
    """
    On a large network the membership lists are the whole payload and would be
    cut by the result cap, taking the verdicts with them.
    """
    install_network(_two_islands(supply_on_b=False))
    out = T.diagnose_network()
    assert all("buses" not in island for island in out["islands"])
    assert "include_buses" in out["note"]
    assert out["islands"][0]["verdict"]


def test_bus_membership_is_returned_on_request(install_network):
    install_network(_two_islands(supply_on_b=False))
    out = T.diagnose_network(include_buses=True)
    assert all(island["buses"] for island in out["islands"])
    assert "note" not in out


def test_the_infeasibility_decoder_routes_here():
    from services import chat_service

    prompt = chat_service._build_system_prompt(chat_service.ChatSession())
    assert "diagnose_network before" in prompt
    assert "island holding demand" in prompt
