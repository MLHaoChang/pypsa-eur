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


def _off_grid_network(fixed_battery: bool = False) -> pypsa.Network:
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
    # Fixed power: duration lever changes energy only. template-e2e 22 S3:
    # the lever re-specifies only candidate (extendable) storage, so the
    # battery is a candidate pinned at 30 MW (same optimum as fixed).
    n.add("StorageUnit", "bat", bus="b", carrier="battery",
          p_nom=30.0, p_nom_extendable=not fixed_battery,
          p_nom_min=30.0, p_nom_max=30.0,
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


def test_import_cap_refuses_without_import_identity():
    """Assessor P3c-B2: never fall back to links.index[0]."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Carrier", "H2")
    n.add("Bus", "b0", carrier="AC")
    n.add("Bus", "b1", carrier="AC")
    n.add("Link", "elyzer", bus0="b0", bus1="b1",
          p_nom=10.0, efficiency=0.9, carrier="H2")
    n.links["eh_role"] = ""
    n.links.at["elyzer", "eh_role"] = "eh_conversion"
    with pytest.raises(L.LeverScenarioError, match="no import"):
        L.apply_lever_scenario(n, "import_cap", value=25.0)
    assert float(n.links.at["elyzer", "p_nom"]) == 10.0


def test_levers_section_status_rejects_identical_costs():
    """Assessor P3c-B1: identical cost@target is not an established lever table."""
    table = {
        "aborted": False,
        "options": [
            {"status": "ok", "cost_at_target_eur": 100.0, "ineffective": False},
            {"status": "ok", "cost_at_target_eur": 100.0, "ineffective": False},
        ],
    }
    status, note = L.levers_section_status(table)
    assert status == "not_established"
    assert "differentiate" in (note or "")


def test_levers_section_status_rejects_ineffective_islanded_import():
    table = {
        "aborted": False,
        "options": [
            {"status": "ok", "cost_at_target_eur": 10.0, "ineffective": True},
            {"status": "ok", "cost_at_target_eur": 20.0, "ineffective": True},
        ],
    }
    status, note = L.levers_section_status(table)
    assert status == "not_established"


def test_off_grid_pack_disables_import_cap_lever():
    """Class-B islanding makes import_cap a no-op; pack must not enable it."""
    pack = default_off_grid_pack()
    assert pack.levers.storage_duration is True
    assert pack.levers.import_cap is False


# ── template-e2e 22 S3: the duration lever touches only candidate storage ──

def _ups_and_candidate_network(bess_p_nom: float = 0.0, base=None) -> pypsa.Network:
    """A DC-like site: a fixed, 0-capex UPS (0.25 h) the base design owns, and
    an extendable candidate battery priced per MW at its 4 h template duration.
    Lengthening the UPS would give free energy (the old lever's -475k on DC)."""
    n = (base or _off_grid_network)()
    n.remove("StorageUnit", "bat")
    n.add("StorageUnit", "ups", bus="b", carrier="battery",
          p_nom=30.0, p_nom_extendable=False, max_hours=0.25, capital_cost=0.0,
          efficiency_store=0.95, efficiency_dispatch=0.95, cyclic_state_of_charge=True)
    n.add("StorageUnit", "bess", bus="b", carrier="battery",
          p_nom=bess_p_nom, p_nom_extendable=True, p_nom_max=60.0, max_hours=4.0,
          capital_cost=100.0, efficiency_store=0.95, efficiency_dispatch=0.95,
          cyclic_state_of_charge=True)
    return n


def test_storage_duration_keeps_fixed_storage_and_undo_is_exact():
    """GK A C2/C3: an extendable unit with p_nom > 0 (brownfield) is a
    candidate; the fixed UPS keeps its duration; undo restores the frame."""
    n = _ups_and_candidate_network(bess_p_nom=10.0)
    before = n.storage_units.copy(deep=True)
    undo, mut = L.apply_lever_scenario(n, "storage_duration", value=24.0)
    assert float(n.storage_units.at["bess", "max_hours"]) == 24.0
    assert float(n.storage_units.at["ups", "max_hours"]) == 0.25
    assert mut["applied_storage"] == ["bess"]
    assert mut["kept_storage"] == ["ups"]
    assert mut["energy_costed"] is False
    assert "not costed" in mut["autonomy_note"]
    undo()
    assert n.storage_units.equals(before)


def test_storage_duration_on_fixed_storage_only_is_not_applicable():
    """GK A ruling A / C6: the old fixed-battery fixture names what it kept."""
    n = _off_grid_network(fixed_battery=True)
    with pytest.raises(L.LeverNotApplicable, match="bat"):
        L.apply_lever_scenario(n, "storage_duration", value=24.0)
    assert float(n.storage_units.at["bat", "max_hours"]) == 4.0


def test_storage_duration_on_split_storage_skips_fixed_stores():
    """GK A C2/C3: a fixed split store with a c-rate limit no longer blocks the
    lever; only the extendable Store is re-specified, and its energy is costed."""
    from services.solver.split_storage import add_split_storage
    n = _off_grid_network()
    n.remove("StorageUnit", "bat")
    n.add("Carrier", "high_c_bess")
    add_split_storage(n, "ride", "b", "high_c_bess", p_nom=10.0, e_nom_max=10.0,
                      energy_capital_cost=1.0)
    n.stores.at["ride", "e_nom_extendable"] = False
    n.stores.at["ride", "e_nom"] = 5.0
    n.add("Carrier", "energy_bess")
    add_split_storage(n, "bess", "b", "energy_bess", p_nom=10.0, energy_capital_cost=1.0)
    before = n.stores.copy(deep=True)
    undo, mut = L.apply_lever_scenario(n, "storage_duration", value=4.0)
    assert mut["applied_storage"] == ["bess"]
    assert mut["kept_storage"] == ["ride"]
    assert mut["energy_costed"] is True
    assert float(n.stores.at["bess", "max_hours"]) == 4.0
    assert float(n.stores.at["ride", "e_nom"]) == 5.0
    assert n.stores.at["ride", "max_hours"] == before.at["ride", "max_hours"]
    undo()
    assert n.stores.equals(before)


def _lever_table(n, values, cfg=None):
    from services.pypsa_service import PyPSAService
    PyPSAService.set_network(n)
    return L.compare_lever_scenarios(
        n, cfg or SolverConfig(voll=1000.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(), stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(), kind="storage_duration", values=values,
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
    )


@pytest.mark.live_solve
def test_storage_unit_candidate_extra_hours_are_not_costed():
    """GK A C1 — pins a known limit: a built candidate StorageUnit is priced per
    MW, so a longer rung costs no more. Whoever costs StorageUnit energy per MWh
    flips this test on purpose."""
    table = _lever_table(_ups_and_candidate_network(), (4.0, 72.0))
    cost = {o["value"]: o["cost_at_target_eur"] for o in table["options"]}
    assert all(o["status"] in ("ok", "optimal") for o in table["options"])
    assert cost[72.0] <= cost[4.0] + 1e-6
    assert all(o["applied"]["energy_costed"] is False for o in table["options"])


@pytest.mark.live_solve
def test_the_rung_at_the_template_duration_reconciles_with_the_base():
    """GK A C4: the study's base_cost_eur is st.cost_at_target, and the rung at
    the candidate's own 4 h equals it — the UPS is no longer lengthened."""
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _ups_and_candidate_network(base=_off_grid_network_with_import)
    pack = default_off_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
        "mc_certify_required": False,
    })
    PyPSAService.set_network(n)
    store: dict = {}
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=1000.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(), stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "levers", "assemble"), store=store,
    )
    table = store["eh_lever_comparison"]
    base = report.sections["cost"].payload["total_system_cost_eur"]
    assert table["base_cost_eur"] == base
    four = [o for o in table["options"] if o["value"] == 4.0]
    assert four and four[0]["cost_at_target_eur"] == pytest.approx(base, rel=1e-6)
    assert four[0]["applied"]["kept_storage"] == ["ups"]


def test_a_failed_base_leaves_base_cost_none(monkeypatch):
    """GK A C4: no base solve → base_cost_eur is None, never 0."""
    from services.adequacy import eh_study as S
    fake = {"kind": "storage_duration", "options": [], "solves_attempted": 0,
            "comparable_solved": 0, "aborted": False}
    monkeypatch.setattr(L, "compare_lever_scenarios", lambda *a, **k: dict(fake))
    st = S._Study(pack=default_off_grid_pack(), network=_ups_and_candidate_network(),
                  cfg=SolverConfig(), lock=None, stop_event=threading.Event(),
                  log_queue=queue.SimpleQueue(), store={}, budget_solves=10, solves=1,
                  cost_at_target=None, pack_h="h", sections={}, records=[])
    S._stage_levers(st)
    assert st.store["eh_lever_comparison"]["base_cost_eur"] is None
