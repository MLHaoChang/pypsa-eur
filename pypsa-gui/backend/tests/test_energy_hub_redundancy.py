"""
P3a — redundancy scenario enumeration (TDD).

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md decisions 5
Plan: Phase 3a — compare cost vs achieved ENS at a fixed availability target
across discrete redundancy options (not FOR derating; not joint MILP).
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
from services.adequacy import redundancy as R
from services.solver_service import SolverConfig


def _network() -> pypsa.Network:
    """Load 100 MW; cheap 60 + expensive backup 40; optional storage slot."""
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


def _network_with_link() -> pypsa.Network:
    """Two-bus net with an extendable conversion Link (distinct topology)."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas")
    n.add("Carrier", "AC")
    n.add("Bus", "b0", carrier="AC")
    n.add("Bus", "b1", carrier="AC")
    n.add("Load", "l", bus="b0", p_set=100.0)
    n.add("Generator", "cheap", bus="b0", carrier="gas",
          p_nom=60.0, marginal_cost=10.0)
    n.add("Generator", "remote", bus="b1", carrier="gas",
          p_nom=80.0, marginal_cost=50.0)
    n.add("Link", "conv", bus0="b0", bus1="b1",
          p_nom=20.0, p_nom_extendable=True, p_nom_max=40.0,
          capital_cost=30.0, efficiency=0.95)
    return n


def test_default_scenario_ids():
    assert R.DEFAULT_SCENARIOS == (
        "base", "n1_generation", "n1_conversion", "parallel_storage")


def test_apply_n1_generation_adds_spare_and_undo_restores():
    n = _network()
    before = set(n.generators.index)
    undo = R.apply_redundancy_scenario(n, "n1_generation")
    assert "eh_spare_gen" in n.generators.index
    assert float(n.generators.at["eh_spare_gen", "p_nom_max"]) > 0
    undo()
    assert set(n.generators.index) == before


def test_apply_parallel_storage_adds_spare_and_undo_restores():
    n = _network()
    before = set(n.storage_units.index)
    undo = R.apply_redundancy_scenario(n, "parallel_storage")
    assert "eh_spare_storage" in n.storage_units.index
    undo()
    assert set(n.storage_units.index) == before


def test_apply_n1_conversion_on_links_bumps_headroom_and_undo_restores():
    """n1_conversion must be Link topology — not an n1_generation alias."""
    n = _network_with_link()
    before_max = float(n.links.at["conv", "p_nom_max"])
    before_gens = set(n.generators.index)
    before_links = set(n.links.index)
    undo = R.apply_redundancy_scenario(n, "n1_conversion")
    assert "eh_spare_gen" not in n.generators.index
    assert set(n.generators.index) == before_gens
    assert set(n.links.index) == before_links
    assert float(n.links.at["conv", "p_nom_max"]) == before_max + 50.0
    assert str(n.links.at["conv", "eh_role"]) == "eh_n1_conversion"
    undo()
    assert float(n.links.at["conv", "p_nom_max"]) == before_max
    assert "eh_role" not in n.links.columns or str(
        n.links.at["conv", "eh_role"]) != "eh_n1_conversion"


def test_apply_n1_conversion_without_links_adds_distinct_path():
    """Link-less nets invent a conversion path — not eh_spare_gen."""
    n = _network()
    before_buses = set(n.buses.index)
    before_gens = set(n.generators.index)
    undo = R.apply_redundancy_scenario(n, "n1_conversion")
    assert "eh_spare_gen" not in n.generators.index
    assert "eh_spare_conversion" in n.links.index
    assert "eh_n1_conv_bus" in n.buses.index
    assert "eh_n1_conv_feeder" in n.generators.index
    undo()
    assert set(n.buses.index) == before_buses
    assert set(n.generators.index) == before_gens
    assert n.links is None or n.links.empty


def test_n1_conversion_distinct_from_n1_generation():
    n = _network()
    R.apply_redundancy_scenario(n, "n1_generation")
    gen_ids = set(n.generators.index)
    n2 = _network()
    R.apply_redundancy_scenario(n2, "n1_conversion")
    assert "eh_spare_gen" in gen_ids
    assert "eh_spare_gen" not in n2.generators.index
    assert "eh_spare_conversion" in n2.links.index


def test_unknown_scenario_raises():
    n = _network()
    with pytest.raises(R.RedundancyScenarioError, match="unknown"):
        R.apply_redundancy_scenario(n, "not_a_scenario")


@pytest.mark.live_solve
def test_compare_persists_to_store():
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
    )
    assert store.get("eh_redundancy_comparison") is table
    assert store["eh_redundancy_comparison"]["certify_method"] == "ens"


def test_default_pipeline_skips_redundancy_when_lever_off():
    """pack.levers.redundancy=False + stages=None → redundancy skipped."""
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
    })
    assert pack.levers.redundancy is False
    PyPSAService.set_network(n)
    stop = threading.Event()
    stop.set()
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=stop,
        log_queue=queue.SimpleQueue(),
        stages=None,
    )
    assert report.completeness["redundancy"] == "skipped"


@pytest.mark.live_solve
def test_compare_returns_at_least_two_options_with_costs():
    from services.pypsa_service import PyPSAService

    n = _network()
    PyPSAService.set_network(n)
    cfg = SolverConfig(voll=150.0, ens_cap_permyriad=1000.0)
    table = R.compare_redundancy_scenarios(
        n, cfg,
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        scenarios=("base", "n1_generation"),
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
    )
    assert len(table["options"]) >= 2
    ids = {o["scenario_id"] for o in table["options"]}
    assert "base" in ids and "n1_generation" in ids
    for opt in table["options"]:
        assert opt["cost_at_target_eur"] is not None
        assert opt["binding_metric"] == "ens"
        assert "achieved_ens_mwh" in opt
        assert opt["meets_target"] in (True, False)
    assert len({o["binding_metric"] for o in table["options"]}) == 1


@pytest.mark.live_solve
def test_compare_provenance_names_ens_not_lole_when_ens_target():
    from services.pypsa_service import PyPSAService

    n = _network()
    PyPSAService.set_network(n)
    table = R.compare_redundancy_scenarios(
        n, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        scenarios=("base", "parallel_storage"),
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
    )
    assert table["certify_method"] == "ens"
    assert all(o["binding_metric"] == "ens" for o in table["options"])


@pytest.mark.live_solve
def test_run_eh_study_redundancy_writes_store_for_get_route():
    """Binding condition: production path must populate GET /eh_redundancy."""
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
    assert report.completeness["redundancy"] == "ok"
    assert "eh_redundancy_comparison" in store
    assert len(store["eh_redundancy_comparison"]["options"]) >= 2
