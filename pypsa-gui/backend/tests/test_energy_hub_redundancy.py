"""
P3a — redundancy scenario enumeration (TDD).

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md decisions 5
Plan: Phase 3a — compare cost vs achieved ENS at a fixed availability target
across discrete redundancy options (not FOR derating; not joint MILP).
"""
from __future__ import annotations

import math
import queue
import threading

import pandas as pd
import pypsa
import pytest

from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
from services.adequacy import redundancy as R
from services.solver_service import SolverConfig


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas")
    n.add("Carrier", "battery")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "cheap", bus="b", carrier="gas",
          p_nom=60.0, marginal_cost=10.0)
    n.add("Generator", "backup", bus="b", carrier="gas",
          p_nom=40.0, marginal_cost=200.0, capital_cost=50.0,
          p_nom_extendable=True, p_nom_max=80.0)
    n.add("StorageUnit", "bat", bus="b", carrier="battery",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=50.0,
          max_hours=4.0, capital_cost=80.0, efficiency_store=0.9,
          efficiency_dispatch=0.9)
    return n


def _network_with_conversion(*, p_nom_max=40.0, role="eh_conversion") -> pypsa.Network:
    """Two-bus net with an explicit conversion Link (positive identity)."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas")
    n.add("Carrier", "H2")
    n.add("Bus", "b0", carrier="AC")
    n.add("Bus", "b1", carrier="AC")
    n.add("Load", "l", bus="b0", p_set=100.0)
    n.add("Generator", "cheap", bus="b0", carrier="gas",
          p_nom=60.0, marginal_cost=10.0)
    n.add("Generator", "remote", bus="b1", carrier="gas",
          p_nom=80.0, marginal_cost=50.0)
    kwargs = dict(
        bus0="b0", bus1="b1",
        p_nom=20.0, p_nom_extendable=True,
        capital_cost=30.0, efficiency=0.95, carrier="H2",
    )
    if p_nom_max is not None:
        kwargs["p_nom_max"] = p_nom_max
    n.add("Link", "conv", **kwargs)
    n.links["eh_role"] = ""
    n.links.at["conv", "eh_role"] = role
    return n


def test_default_scenario_ids():
    assert R.DEFAULT_SCENARIOS == (
        "base", "n1_generation", "n1_conversion", "parallel_storage")


def test_apply_n1_generation_adds_spare_and_undo_restores():
    n = _network()
    before = set(n.generators.index)
    undo, mut = R.apply_redundancy_scenario(n, "n1_generation")
    assert "eh_spare_gen" in n.generators.index
    assert mut["cost_basis"] == "synthetic_placeholder"
    assert mut["applied"]
    undo()
    assert set(n.generators.index) == before


def test_apply_parallel_storage_adds_spare_and_undo_restores():
    n = _network()
    before = set(n.storage_units.index)
    undo, mut = R.apply_redundancy_scenario(n, "parallel_storage")
    assert "eh_spare_storage" in n.storage_units.index
    undo()
    assert set(n.storage_units.index) == before


def test_apply_n1_conversion_on_conversion_link_raises_headroom():
    n = _network_with_conversion(p_nom_max=40.0)
    undo, mut = R.apply_redundancy_scenario(n, "n1_conversion")
    assert mut["applied"][0]["name"] == "conv"
    assert mut["applied"][0]["action"] == "raise_p_nom_max"
    assert float(n.links.at["conv", "p_nom_max"]) == 70.0  # 20 + 50
    undo()
    assert float(n.links.at["conv", "p_nom_max"]) == 40.0


def test_apply_n1_conversion_forces_finite_when_p_nom_max_inf():
    n = _network_with_conversion(p_nom_max=None)
    # Explicitly set inf (PyPSA default may already be).
    n.links.at["conv", "p_nom_max"] = float("inf")
    undo, mut = R.apply_redundancy_scenario(n, "n1_conversion")
    after = float(n.links.at["conv", "p_nom_max"])
    assert math.isfinite(after)
    assert after == pytest.approx(70.0)
    undo()


def test_apply_n1_conversion_refuses_to_shrink_existing_max():
    """Assessor D2: never lower a finite p_nom_max."""
    n = _network_with_conversion(p_nom_max=500.0)
    undo, mut = R.apply_redundancy_scenario(n, "n1_conversion")
    assert mut["not_applicable"] is True
    assert mut["applied"] == []
    assert float(n.links.at["conv", "p_nom_max"]) == 500.0
    undo()


def test_select_conversion_excludes_grid_import_and_does_not_guess_ac():
    """Assessor D1: import / bare AC must not be selected as conversion."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Load", "l", bus="hub", p_set=10.0)
    n.add("Generator", "g", bus="hub", carrier="gas", p_nom=5.0, marginal_cost=10.0)
    n.add("Link", "import_poc", bus0="hub", bus1="grid",
          p_nom=50.0, p_nom_extendable=False, p_nom_max=50.0,
          efficiency=1.0, carrier="AC")
    n.links["eh_role"] = ""
    n.links.at["import_poc", "eh_role"] = "grid_import"
    assert R._select_conversion_link(n) is None
    # Applying invents a spare conversion path — does NOT touch import_poc.
    undo, mut = R.apply_redundancy_scenario(n, "n1_conversion")
    assert float(n.links.at["import_poc", "p_nom_max"]) == 50.0
    actions = {a["action"] for a in mut["applied"]}
    assert "add_conversion_path" in actions
    assert "eh_spare_conversion" in n.links.index
    undo()


def test_select_conversion_prefers_role_over_insertion_order():
    n = _network_with_conversion(p_nom_max=40.0)
    n.add("Link", "order_first", bus0="b0", bus1="b1",
          p_nom=5.0, p_nom_extendable=True, p_nom_max=10.0,
          capital_cost=10.0, efficiency=0.95, carrier="heat")
    # Reorder: ensure order_first is index[0] by rebuilding roles
    n.links.at["order_first", "eh_role"] = ""
    n.links.at["conv", "eh_role"] = "eh_conversion"
    # Insertion order may put order_first last; force selection by role.
    assert R._select_conversion_link(n) == "conv"


def test_unknown_scenario_raises():
    with pytest.raises(R.RedundancyScenarioError, match="unknown"):
        R.apply_redundancy_scenario(_network(), "not_a_scenario")


def test_compare_signature_has_no_state_update():
    import inspect
    assert "state_update" not in inspect.signature(
        R.compare_redundancy_scenarios).parameters


def test_section_status_not_ok_when_nothing_solved():
    status, note = R.redundancy_section_status({
        "aborted": False,
        "options": [
            {"status": "error", "not_applicable": False},
            {"status": "not_applicable", "not_applicable": True},
        ],
    })
    assert status == "not_established"
    status2, _ = R.redundancy_section_status({
        "aborted": False,
        "options": [
            {"status": "ok", "not_applicable": False},
            {"status": "ok", "not_applicable": False},
        ],
    })
    assert status2 == "ok"


def test_default_pipeline_skips_redundancy_when_lever_off():
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
    })
    assert pack.levers.redundancy is False
    PyPSAService.set_network(n)
    stop = threading.Event(); stop.set()
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=stop,
        log_queue=queue.SimpleQueue(),
        stages=None,
    )
    assert report.completeness["redundancy"] == "skipped"


def test_claim_wipe_includes_eh_keys():
    from pathlib import Path
    text = Path("routers/simulation.py").read_text()
    idx = text.find('status="running"')
    chunk = text[idx:idx + 900]
    assert "eh_redundancy_comparison=None" in chunk
    assert "eh_lever_comparison=None" in chunk
    assert "eh_reference_design_report=None" in chunk


@pytest.mark.live_solve
def test_compare_persists_to_store_with_provenance():
    from services.pypsa_service import PyPSAService

    n = _network()
    PyPSAService.set_network(n)
    store: dict = {}
    table = R.compare_redundancy_scenarios(
        n, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        scenarios=("base",),
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
        store=store,
        pack_hash="packdeadbeef",
        assumptions_hash="assumdeadbeef",
    )
    assert store.get("eh_redundancy_comparison") is table
    assert table["pack_hash"] == "packdeadbeef"
    assert table["assumptions_hash"] == "assumdeadbeef"
    assert "cost_basis" in table["options"][0]
    assert "applied" in table["options"][0]


@pytest.mark.live_solve
def test_compare_returns_at_least_two_solved_options_with_costs():
    from services.pypsa_service import PyPSAService

    n = _network()
    PyPSAService.set_network(n)
    table = R.compare_redundancy_scenarios(
        n, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        scenarios=("base", "n1_generation"),
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
    )
    solved = [o for o in table["options"] if o["status"] in ("ok", "optimal")]
    assert len(solved) >= 2
    for opt in solved:
        assert opt["cost_at_target_eur"] is not None
        assert opt["binding_metric"] == "ens"


@pytest.mark.live_solve
def test_n1_conversion_live_changes_optimum_vs_base():
    """Assessor D3 / N1: base must solve; scenario must change the LP outcome."""
    from services.pypsa_service import PyPSAService

    # Feasible two-bus: expensive local + cheap remote behind a tight conversion
    # Link tagged eh_conversion. Raising p_nom_max unlocks cheap remote MW.
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas")
    n.add("Carrier", "AC")
    n.add("Bus", "b0", carrier="AC")
    n.add("Bus", "b1", carrier="AC")
    n.add("Load", "l", bus="b0", p_set=100.0)
    n.add("Generator", "local", bus="b0", carrier="gas",
          p_nom=100.0, marginal_cost=200.0)  # expensive local covers load alone
    n.add("Generator", "remote", bus="b1", carrier="gas",
          p_nom=100.0, marginal_cost=5.0)   # cheap remote
    # bus0=remote, bus1=load so positive flow delivers cheap remote MW to load.
    n.add("Link", "conv", bus0="b1", bus1="b0",
          p_nom=10.0, p_nom_extendable=True, p_nom_max=12.0,
          capital_cost=0.5, efficiency=1.0, carrier="AC")
    n.links["eh_role"] = ""
    n.links.at["conv", "eh_role"] = "eh_conversion"
    # Sanity: selector must pick conversion, not invent a spare.
    assert R._select_conversion_link(n) == "conv"

    PyPSAService.set_network(n)
    table = R.compare_redundancy_scenarios(
        n, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        scenarios=("base", "n1_conversion"),
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
    )
    by_id = {o["scenario_id"]: o for o in table["options"]}
    base = by_id["base"]
    conv = by_id["n1_conversion"]
    assert base["status"] in ("ok", "optimal"), base
    assert conv["status"] in ("ok", "optimal"), conv
    assert not conv.get("not_applicable")
    assert conv["applied"] and conv["applied"][0]["action"] == "raise_p_nom_max"
    assert float(conv["applied"][0]["new_p_nom_max"]) > float(
        conv["applied"][0]["base_mw"])
    # Measurable LP change once conversion headroom opens to remote cheap MW.
    assert base["cost_at_target_eur"] is not None and conv["cost_at_target_eur"] is not None
    assert base["cost_at_target_eur"] != conv["cost_at_target_eur"], (base, conv)


@pytest.mark.live_solve
def test_run_eh_study_redundancy_honest_status_and_store():
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
        "levers": default_strong_grid_pack().levers.model_copy(
            update={"redundancy": True}),
    })
    PyPSAService.set_network(n)
    store: dict = {}
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "redundancy", "assemble"),
        store=store,
    )
    assert "eh_redundancy_comparison" in store
    table = store["eh_redundancy_comparison"]
    assert table.get("pack_hash")
    assert table.get("assumptions_hash")
    # With default scenarios on a solvable net, expect ok completeness.
    assert report.completeness["redundancy"] == "ok"
    assert table["comparable_solved"] >= 2



def test_invented_spare_path_lists_bus_feeder_and_link():
    """Assessor B1: applied must enumerate every invented component."""
    n = _network()  # no conversion link
    undo, mut = R.apply_redundancy_scenario(n, "n1_conversion")
    names = {a["name"] for a in mut["applied"]}
    assert names >= {"eh_n1_conv_bus", "eh_n1_conv_feeder", "eh_spare_conversion"}
    feeder = next(a for a in mut["applied"] if a["name"] == "eh_n1_conv_feeder")
    assert feeder["capital_cost"] == 0.0
    assert feeder.get("note") == "firm_nameplate_zero_capex"
    undo()


def test_conversion_role_on_poc_bus_is_banned():
    """Assessor B3: conversion-role link on a PoC bus must not be selected."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Carrier", "AC")
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "poc", carrier="AC")
    n.buses["eh_poc"] = False
    n.buses.at["poc", "eh_poc"] = True
    n.add("Load", "l", bus="hub", p_set=10.0)
    n.add("Generator", "g", bus="hub", p_nom=10.0, marginal_cost=10.0, carrier="AC")
    n.add("Link", "elyzer", bus0="hub", bus1="poc",
          p_nom=25.0, p_nom_extendable=True, p_nom_max=25.0,
          efficiency=1.0, carrier="H2")
    n.links["eh_role"] = ""
    n.links.at["elyzer", "eh_role"] = "eh_conversion"
    assert "elyzer" in R._import_link_ids(n)
    assert R._select_conversion_link(n) is None


@pytest.mark.live_solve
def test_compare_stamps_effective_voll():
    """Assessor B2: table discloses effective VOLL and whether it was defaulted."""
    from services.pypsa_service import PyPSAService

    n = _network()
    PyPSAService.set_network(n)
    # voll=0 forces default
    table = R.compare_redundancy_scenarios(
        n, SolverConfig(voll=0.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        scenarios=("base",),
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
    )
    assert table["effective_voll"] == 150.0
    assert table["voll_defaulted"] is True
    assert table["options"][0]["effective_voll"] == 150.0
    assert table["options"][0]["voll_defaulted"] is True
