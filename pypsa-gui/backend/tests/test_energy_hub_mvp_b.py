"""
MVP-B DoD — weak + off-grid packs fill dtc / lever report sections.

Plan: docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md
  Phase 5 acceptance — MVP-B DoD checkbox.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from models.energy_hub import (
    AvailabilityTarget,
    DtcConfig,
    default_off_grid_pack,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.solver_service import SolverConfig


def _weak_mvp_b_network() -> pypsa.Network:
    """Weak-flexible fixture: import Link + critical bus (no storage).

    Lever stage soft-skips ``storage_duration`` when no StorageUnits exist;
    ``import_cap`` alone must still establish the levers section.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Carrier", "AC")
    n.add("Bus", "crit", carrier="AC")
    n.add("Bus", "flex", carrier="AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Load", "critical", bus="crit", p_set=40.0)
    n.add("Load", "comfort", bus="flex", p_set=60.0)
    n.add("Generator", "local", bus="crit", carrier="gas",
          p_nom=30.0, marginal_cost=80.0)
    n.add("Generator", "remote", bus="grid", carrier="gas",
          p_nom=200.0, marginal_cost=10.0)
    n.add("Link", "crit_flex", bus0="crit", bus1="flex",
          p_nom=200.0, p_nom_extendable=False, efficiency=1.0, carrier="AC")
    n.add("Link", "import_poc", bus0="grid", bus1="crit",
          p_nom=100.0, p_nom_extendable=False, p_nom_max=100.0,
          efficiency=1.0, carrier="AC")
    n.links["eh_role"] = ""
    n.links.at["import_poc", "eh_role"] = "grid_import"
    n.buses["eh_poc"] = False
    n.buses.at["grid", "eh_poc"] = True
    n.buses["eh_critical"] = False
    n.buses.at["crit", "eh_critical"] = True
    return n


def _off_grid_mvp_b_network() -> pypsa.Network:
    """Off-grid fixture with PoC import (for apply_pack) + duration-binding storage."""
    vals = [20.0] * 6 + [80.0] * 6
    n = pypsa.Network()
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
    n.add("StorageUnit", "bat", bus="b", carrier="battery",
          p_nom=30.0, p_nom_extendable=False,
          max_hours=4.0, capital_cost=0.0,
          efficiency_store=0.95, efficiency_dispatch=0.95,
          cyclic_state_of_charge=True)
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


def test_mvp_b_pack_defaults_enable_expected_stages():
    """Weak enables dtc+levers; off-grid enables storage levers only; strong skips both."""
    weak = default_weak_flexible_pack()
    assert weak.dtc_stress_default is True
    assert weak.levers.import_cap is True
    assert weak.levers.storage_duration is True

    off = default_off_grid_pack()
    assert off.dtc_stress_default is False
    assert off.levers.storage_duration is True
    assert off.levers.import_cap is False

    strong = default_strong_grid_pack()
    assert strong.dtc_stress_default is False
    assert strong.levers.import_cap is False
    assert strong.levers.storage_duration is False


@pytest.mark.live_solve
def test_mvp_b_weak_default_pipeline_fills_dtc_and_levers():
    """MVP-B DoD: weak pack default pipeline → filled dtc + levers sections."""
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _weak_mvp_b_network()
    pack = default_weak_flexible_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0),
        "mc_certify_required": False,
    })
    assert pack.dtc_stress_default is True
    assert pack.levers.import_cap is True
    PyPSAService.set_network(n)
    store: dict = {}
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=500.0, ens_cap_permyriad=5000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=None,  # default pipeline — pack flags keep levers + dtc_stress
        store=store,
        dtc_config=DtcConfig(
            critical_bus_ids=["crit"],
            critical_load_ids=["critical"],
            islanding_contingencies=["import_poc"],
        ),
    )
    assert report.completeness["dtc"] == "ok"
    assert report.completeness["levers"] == "ok"
    assert report.sections["dtc"].payload is not None
    assert report.sections["levers"].payload is not None
    assert "eh_dtc_stress" in store
    assert "eh_lever_comparison" in store
    assert store["eh_dtc_stress"]["attribution"] == "bus_aggregate_not_per_load"
    # storage_duration soft-skipped (no StorageUnits); import_cap must remain.
    skipped = store["eh_lever_comparison"].get("skipped_kinds") or []
    assert any(s.startswith("storage_duration:") for s in skipped)
    assert store["eh_lever_comparison"]["comparable_solved"] >= 2


@pytest.mark.live_solve
def test_mvp_b_off_grid_default_pipeline_fills_levers():
    """MVP-B DoD: off-grid pack default pipeline → filled levers; dtc skipped."""
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _off_grid_mvp_b_network()
    pack = default_off_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
        "mc_certify_required": False,
    })
    assert pack.levers.storage_duration is True
    assert pack.dtc_stress_default is False
    PyPSAService.set_network(n)
    store: dict = {}
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=1000.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=None,
        store=store,
    )
    assert report.completeness["levers"] == "ok"
    assert report.completeness["dtc"] == "skipped"
    assert report.sections["levers"].payload is not None
    assert "eh_lever_comparison" in store
    assert store["eh_lever_comparison"]["comparable_solved"] >= 2
    assert "eh_dtc_stress" not in store
