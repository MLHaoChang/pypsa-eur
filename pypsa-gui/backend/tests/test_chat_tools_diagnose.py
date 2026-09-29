"""
Improvement #15 — a connectivity answer the agent can actually get.

Nothing in the tool surface reports electrical topology. `validate_for_run`
covers dangling bus references (`_check_bus_references`), bounds, costs and
solver assumptions, but never asks whether the network is one electrical
system or several — and "my solve is infeasible" is most often exactly
that: a load sitting in an island with nothing to serve it.

Before this the agent could only list components and try to reconstruct the
graph itself, one paginated read at a time, which it has no reliable way to
do and no budget for on a real network.

Deliberately NOT re-implemented here: dangling bus refs. The preflight
already reports those, and a second, differently-worded copy of that check
is how two sources of truth start disagreeing.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services import chat_tools


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=2, freq="h"))
    return n


def test_a_healthy_network_is_one_island(install_network):
    n = _net()
    n.add("Bus", "A")
    n.add("Bus", "B")
    n.add("Line", "AB", bus0="A", bus1="B", x=0.1, r=0.01)
    n.add("Generator", "G", bus="A", p_nom=100)
    n.add("Load", "L", bus="B", p_set=[10.0, 10.0])
    install_network(n, name="Healthy")

    out = chat_tools.diagnose_network()

    assert out["island_count"] == 1
    assert out["isolated_buses"] == []
    assert out["islands_without_generation"] == []
    assert out["verdict"] == "connected"


def test_two_unconnected_halves_are_two_islands(install_network):
    n = _net()
    for b in ("A", "B", "C", "D"):
        n.add("Bus", b)
    n.add("Line", "AB", bus0="A", bus1="B", x=0.1, r=0.01)
    n.add("Line", "CD", bus0="C", bus1="D", x=0.1, r=0.01)
    n.add("Generator", "G1", bus="A", p_nom=50)
    n.add("Generator", "G2", bus="C", p_nom=50)
    install_network(n, name="TwoIslands")

    out = chat_tools.diagnose_network()

    assert out["island_count"] == 2
    assert sorted(i["size"] for i in out["islands"]) == [2, 2]
    assert out["verdict"] == "fragmented"


def test_a_load_marooned_without_generation_is_named(install_network):
    """
    The infeasibility smoking gun, and the reason this tool exists: the
    solve fails, every component looks individually fine, and the only
    thing wrong is which side of a missing line the generator sits on.
    """
    n = _net()
    n.add("Bus", "Gen")
    n.add("Bus", "Island")
    n.add("Generator", "G", bus="Gen", p_nom=100)
    n.add("Load", "Stranded", bus="Island", p_set=[7.0, 9.0])
    install_network(n, name="Marooned")

    out = chat_tools.diagnose_network()

    assert len(out["islands_without_generation"]) == 1
    stranded = out["islands_without_generation"][0]
    assert stranded["buses"] == ["Island"]
    assert stranded["peak_load_mw"] == pytest.approx(9.0)
    assert out["verdict"] == "infeasible_topology"


def test_an_island_with_no_load_is_not_flagged(install_network):
    """
    A generation-only island is odd but solvable — flagging it would train
    the agent to ignore the field that matters.
    """
    n = _net()
    n.add("Bus", "Main")
    n.add("Bus", "Spare")
    n.add("Generator", "G", bus="Main", p_nom=10)
    n.add("Load", "L", bus="Main", p_set=[1.0, 1.0])
    n.add("Generator", "Idle", bus="Spare", p_nom=5)
    install_network(n, name="SpareGen")

    out = chat_tools.diagnose_network()

    assert out["island_count"] == 2
    assert out["islands_without_generation"] == []


def test_storage_counts_as_generation(install_network):
    """A battery can serve load; an island holding one is not stranded."""
    n = _net()
    n.add("Bus", "Main")
    n.add("Bus", "Batt")
    n.add("Generator", "G", bus="Main", p_nom=10)
    n.add("StorageUnit", "S", bus="Batt", p_nom=5)
    n.add("Load", "L", bus="Batt", p_set=[1.0, 1.0])
    install_network(n, name="BattIsland")

    out = chat_tools.diagnose_network()

    assert out["islands_without_generation"] == []


def test_a_bus_with_nothing_attached_is_reported(install_network):
    n = _net()
    n.add("Bus", "A")
    n.add("Bus", "B")
    n.add("Bus", "Floating")
    n.add("Line", "AB", bus0="A", bus1="B", x=0.1, r=0.01)
    n.add("Generator", "G", bus="A", p_nom=10)
    install_network(n, name="Floater")

    out = chat_tools.diagnose_network()

    assert out["isolated_buses"] == ["Floating"]


def test_links_join_islands_just_like_lines(install_network):
    """
    A DC interconnector or a heat pump is a Link, not a Line. Walking only
    `lines` would report a sector-coupled network as a pile of fragments.
    """
    n = _net()
    n.add("Bus", "AC")
    n.add("Bus", "H2")
    n.add("Link", "electrolyser", bus0="AC", bus1="H2", p_nom=10)
    n.add("Generator", "G", bus="AC", p_nom=10)
    install_network(n, name="Sector")

    out = chat_tools.diagnose_network()

    assert out["island_count"] == 1


def test_a_multi_port_links_third_bus_is_joined_too(install_network):
    """
    bus2 is how a CHP or heat pump reaches its second output. Ignoring the
    extra ports would strand exactly the buses sector-coupling adds.
    """
    n = _net()
    n.add("Bus", "AC")
    n.add("Bus", "Heat")
    n.add("Bus", "Gas")
    n.add("Link", "chp", bus0="Gas", bus1="AC", bus2="Heat",
          p_nom=10, efficiency2=0.4)
    n.add("Generator", "G", bus="Gas", p_nom=10)
    install_network(n, name="CHP")

    out = chat_tools.diagnose_network()

    assert out["island_count"] == 1


def test_transformers_join_islands(install_network):
    n = _net()
    n.add("Bus", "HV", v_nom=380)
    n.add("Bus", "LV", v_nom=110)
    n.add("Transformer", "T", bus0="HV", bus1="LV", s_nom=100, x=0.1)
    n.add("Generator", "G", bus="HV", p_nom=10)
    install_network(n, name="Trafo")

    out = chat_tools.diagnose_network()

    assert out["island_count"] == 1


def test_an_empty_network_answers_instead_of_crashing(install_network):
    install_network(_net(), name="EmptyNet")

    out = chat_tools.diagnose_network()

    assert out["bus_count"] == 0
    assert out["island_count"] == 0
    assert out["verdict"] == "empty"


def test_a_big_fragmented_network_stays_inside_the_result_budget(install_network):
    """
    A diagnosis the agent cannot read is not a diagnosis. 400 isolated buses
    would serialise past _truncate_result's budget and come back as a
    preview string, so per-island bus lists are capped and the cap is
    declared rather than silently applied.
    """
    import json
    n = _net()
    for i in range(400):
        n.add("Bus", f"B{i:03d}")
    install_network(n, name="Shrapnel")

    out = chat_tools.diagnose_network()

    assert out["island_count"] == 400
    assert len(json.dumps(out, default=str)) < 4000
    assert out["islands_truncated"] is True
    assert len(out["islands"]) < 400


def test_peak_load_is_simultaneous_not_a_sum_of_each_loads_own_peak(install_network):
    """
    Two loads peaking in different hours never draw 200 MW together. Summing
    each load's own maximum reported a peak that never happens — and on a
    stranded island that is the number the agent quotes back to the user.
    """
    n = _net()
    n.add("Bus", "A")
    n.add("Load", "L1", bus="A", p_set=[100.0, 0.0])
    n.add("Load", "L2", bus="A", p_set=[0.0, 100.0])
    install_network(n, name="Staggered")

    out = chat_tools.diagnose_network()

    assert out["islands"][0]["peak_load_mw"] == pytest.approx(100.0)
    assert out["islands_without_generation"][0]["peak_load_mw"] == \
        pytest.approx(100.0)


def test_a_link_port_beyond_bus4_is_joined(install_network):
    """PyPSA sets no ceiling on a link's ports, so neither may the walk."""
    n = _net()
    for b in ("AC", "H2", "Heat", "O2", "Water", "Far"):
        n.add("Bus", b)
    n.add("Link", "plant", bus0="AC", bus1="H2", bus2="Heat", bus3="O2",
          bus4="Water", bus5="Far", p_nom=10)
    n.add("Generator", "G", bus="AC", p_nom=10)
    n.add("Load", "L", bus="Far", p_set=[1.0, 1.0])
    install_network(n, name="SixPort")

    out = chat_tools.diagnose_network()

    assert out["island_count"] == 1
    assert out["isolated_buses"] == []
    assert out["verdict"] == "connected"


def test_a_zero_mw_generator_still_counts_as_generation(install_network):
    """`has_generation` is presence, not nameplate — the contract as shipped."""
    n = _net()
    n.add("Bus", "Main")
    n.add("Bus", "Side")
    n.add("Generator", "G", bus="Main", p_nom=10)
    n.add("Generator", "Z", bus="Side", p_nom=0)
    n.add("Load", "L", bus="Side", p_set=[1.0, 1.0])
    install_network(n, name="ZeroGen")

    out = chat_tools.diagnose_network()

    assert out["islands_without_generation"] == []
    assert all(i["has_generation"] for i in out["islands"])


def test_payload_keys_are_unchanged(install_network):
    """The chat contract: delegating the walk must not reshape the payload."""
    n = _net()
    n.add("Bus", "A")
    n.add("Bus", "B")
    n.add("Load", "L", bus="B", p_set=[1.0, 2.0])
    n.add("Generator", "G", bus="A", p_nom=5)
    install_network(n, name="Keys")

    out = chat_tools.diagnose_network()

    assert set(out) == {
        "bus_count", "island_count", "islands", "islands_truncated",
        "isolated_buses", "isolated_buses_truncated",
        "islands_without_generation", "verdict",
    }
    assert set(out["islands"][0]) == {
        "size", "buses", "has_generation", "has_load", "peak_load_mw",
    }
    assert out["isolated_buses"] == ["A", "B"]
    assert out["verdict"] == "infeasible_topology"


def test_an_empty_network_payload_is_unchanged(install_network):
    install_network(_net(), name="EmptyKeys")

    assert chat_tools.diagnose_network() == {
        "bus_count": 0, "island_count": 0, "islands": [],
        "isolated_buses": [], "islands_without_generation": [],
        "islands_truncated": False, "verdict": "empty",
    }
