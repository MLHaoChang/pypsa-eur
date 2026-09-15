"""
P3c — import-cap + storage-duration scenario levers (TDD).

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md decisions 6–7
Plan: Phase 3c — discrete import_cap / storage_duration options at fixed ENS
target, especially for off_grid / weak_flexible. Import = planning limit only.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from models.energy_hub import AvailabilityTarget, default_off_grid_pack
from services.adequacy import levers as L
from services.solver_service import SolverConfig


def _off_grid_network() -> pypsa.Network:
    """Peaky island where storage *duration* (energy) binds cost@target.

    Mean load ≈ local gen so net energy balances; 6 h peaks need more than
    4 h of nameplate energy at fixed p_nom, so short duration forces peaker
    build while 48 h does not. cyclic_state_of_charge prevents freeloading
    an initial SoC.
    """
    n = pypsa.Network()
    vals = [20.0] * 6 + [80.0] * 6
    n.set_snapshots(pd.date_range("2030-01-01", periods=len(vals), freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Carrier", "battery")
    n.add("Carrier", "AC")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=vals)
    n.add("Generator", "local", bus="b", carrier="gas",
          p_nom=50.0, marginal_cost=20.0)
    n.add("Generator", "peaker", bus="b", carrier="gas",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=100.0,
          capital_cost=500.0, marginal_cost=200.0)
    # Fixed power: duration lever changes energy only.
    n.add("StorageUnit", "bat", bus="b", carrier="battery",
          p_nom=30.0, p_nom_extendable=False,
          max_hours=4.0, capital_cost=0.0,
          efficiency_store=0.95, efficiency_dispatch=0.95,
          cyclic_state_of_charge=True)
    return n


def _off_grid_network_with_import() -> pypsa.Network:
    """Off-grid fixture plus a PoC import Link so apply_pack can island it."""
    n = _off_grid_network()
    n.add("Bus", "grid", carrier="AC")
    n.add("Generator", "remote", bus="grid", carrier="gas",
          p_nom=200.0, marginal_cost=5.0)
    n.add("Link", "import_poc", bus0="grid", bus1="b",
          p_nom=50.0, p_nom_extendable=False, p_nom_max=50.0,
          efficiency=1.0, carrier="AC")
    n.links["eh_role"] = ""
    n.links.at["import_poc", "eh_role"] = "grid_import"
    n.buses["eh_poc"] = False
    n.buses.at["grid", "eh_poc"] = True
    return n


def _weak_import_network() -> pypsa.Network:
    """Load with capped grid import Link (planning limit); both caps feasible."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Carrier", "AC")
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Load", "l", bus="hub", p_set=50.0)
    # Local alone + 10% ENS can meet target with tight import; cheap remote
    # via larger import cuts cost.
    n.add("Generator", "local", bus="hub", carrier="gas",
          p_nom=35.0, marginal_cost=200.0)
    n.add("Generator", "remote", bus="grid", carrier="gas",
          p_nom=200.0, marginal_cost=10.0)
    n.add("Link", "import_poc", bus0="grid", bus1="hub",
          p_nom=20.0, p_nom_extendable=False, p_nom_max=20.0,
          efficiency=1.0, carrier="AC")
    n.links["eh_role"] = ""
    n.links.at["import_poc", "eh_role"] = "grid_import"
    n.buses["eh_poc"] = False
    n.buses.at["grid", "eh_poc"] = True
    return n


def test_default_lever_scenario_ids():
    assert "import_cap" in L.DEFAULT_LEVER_KINDS
    assert "storage_duration" in L.DEFAULT_LEVER_KINDS


def test_apply_import_cap_sets_planning_limit_and_undo_restores():
    n = _weak_import_network()
    before = float(n.links.at["import_poc", "p_nom_max"])
    undo, mut = L.apply_lever_scenario(n, "import_cap", value=50.0)
    assert float(n.links.at["import_poc", "p_nom_max"]) == 50.0
    assert float(n.links.at["import_poc", "p_nom"]) == 50.0
    assert mut["firmness"] == "planning_limit_only"
    undo()
    assert float(n.links.at["import_poc", "p_nom_max"]) == before


def test_apply_storage_duration_sets_max_hours_and_undo_restores():
    n = _off_grid_network()
    before = float(n.storage_units.at["bat", "max_hours"])
    undo, mut = L.apply_lever_scenario(n, "storage_duration", value=24.0)
    assert float(n.storage_units.at["bat", "max_hours"]) == 24.0
    assert mut["autonomy_note"]
    undo()
    assert float(n.storage_units.at["bat", "max_hours"]) == before


def test_unknown_lever_raises():
    with pytest.raises(L.LeverScenarioError, match="unknown"):
        L.apply_lever_scenario(_off_grid_network(), "not_a_lever", value=1.0)


@pytest.mark.live_solve
def test_storage_duration_options_change_cost_at_target_on_off_grid():
    """Acceptance 3c: ≥2 storage-duration options affect cost@target."""
    from services.pypsa_service import PyPSAService

    n = _off_grid_network()
    PyPSAService.set_network(n)
    table = L.compare_lever_scenarios(
        n, SolverConfig(voll=1000.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        kind="storage_duration",
        values=(4.0, 48.0),
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
    )
    assert table["kind"] == "storage_duration"
    assert table["import_firmness"] == "planning_limit_only"
    assert "annual_ens_is_not_multi_day_autonomy" in table["honesty_notes"]
    opts = [o for o in table["options"] if o["status"] in ("ok", "optimal")]
    assert len(opts) >= 2
    costs = {o["value"]: o["cost_at_target_eur"] for o in opts}
    assert costs[4.0] is not None and costs[48.0] is not None
    assert costs[4.0] != costs[48.0]


@pytest.mark.live_solve
def test_import_cap_options_change_cost_at_target_on_weak_import():
    from services.pypsa_service import PyPSAService

    n = _weak_import_network()
    PyPSAService.set_network(n)
    table = L.compare_lever_scenarios(
        n, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        kind="import_cap",
        values=(10.0, 80.0),
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
        store={},
    )
    assert table["kind"] == "import_cap"
    opts = [o for o in table["options"] if o["status"] in ("ok", "optimal")]
    assert len(opts) >= 2
    costs = {o["value"]: o["cost_at_target_eur"] for o in opts}
    assert costs[10.0] != costs[80.0]


@pytest.mark.live_solve
def test_run_eh_study_levers_stage_writes_store():
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _off_grid_network_with_import()
    pack = default_off_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
        "mc_certify_required": False,
        # Storage-duration only — import_cap after Class-B islanding is a
        # no-op on p_max_pu=0 and would dilute the comparable-solved gate.
        "levers": default_off_grid_pack().levers.model_copy(update={
            "import_cap": False,
            "storage_duration": True,
        }),
    })
    assert pack.levers.storage_duration is True
    PyPSAService.set_network(n)
    store: dict = {}
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=1000.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "levers", "assemble"),
        store=store,
    )
    assert report.completeness["levers"] == "ok"
    assert "eh_lever_comparison" in store
    assert store["eh_lever_comparison"]["comparable_solved"] >= 2
