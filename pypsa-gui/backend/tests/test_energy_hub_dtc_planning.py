"""
P4b — DtC planning mode (TDD).

Spike decision (spec §10): islanded topology + system ENS under retained
critical demand — NOT per-load slack redesign. Critical vs non-critical loads
must sit on different buses (same honesty boundary as P4a).
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
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.adequacy import dtc as D
from services.solver_service import SolverConfig


def _planning_network() -> pypsa.Network:
    """Critical bus + flex bus; islandable import; local expandables on crit."""
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
          p_nom=10.0, marginal_cost=80.0)
    n.add("Generator", "build", bus="crit", carrier="gas",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=100.0,
          capital_cost=50.0, marginal_cost=100.0)
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


def test_apply_retained_critical_demand_zeros_noncritical_and_undo():
    n = _planning_network()
    dtc = DtcConfig(
        critical_bus_ids=["crit"],
        critical_load_ids=["critical"],
        islanding_contingencies=["import_poc"],
    )
    undo, mut = D.apply_retained_critical_demand(n, dtc)
    assert float(n.loads.at["critical", "p_set"]) == pytest.approx(40.0)
    assert float(n.loads.at["comfort", "p_set"]) == pytest.approx(0.0)
    assert mut["action"] == "retain_critical_demand"
    assert "comfort" in mut["zeroed_load_ids"]
    undo()
    assert float(n.loads.at["comfort", "p_set"]) == pytest.approx(60.0)


def test_planning_honesty_notes_include_retained_critical():
    assert "retained_critical_demand" in D.PLANNING_HONESTY_NOTES
    assert "no_per_load_attribution" in D.PLANNING_HONESTY_NOTES


@pytest.mark.live_solve
def test_run_dtc_planning_expands_under_islanded_critical_demand():
    from services.pypsa_service import PyPSAService

    n = _planning_network()
    PyPSAService.set_network(n)
    dtc = DtcConfig(
        critical_bus_ids=["crit"],
        critical_load_ids=["critical"],
        islanding_contingencies=["import_poc"],
    )
    store: dict = {}
    table = D.run_dtc_planning(
        n, SolverConfig(voll=500.0, ens_cap_permyriad=500.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        dtc=dtc,
        store=store,
    )
    assert store["eh_dtc_planning"] is table
    assert table["mode"] == "planning"
    assert table["attribution"] == "bus_aggregate_not_per_load"
    assert "retained_critical_demand" in table["honesty_notes"]
    rows = [r for r in table["contingencies"] if r["status"] in ("ok", "optimal")]
    assert len(rows) >= 1
    row = rows[0]
    assert row["applied_island"]["method"] == "p_max_pu_p_min_pu_zero"
    assert row["retained_critical_buses"] == ["crit"] or "crit" in row["retained_critical_buses"]
    assert row["cost_at_target_eur"] is not None
    # Islanded + only critical 40 MW load → builder should add capacity.
    assert row.get("built_p_nom_mw") is not None
    assert float(row["built_p_nom_mw"]) > 0


@pytest.mark.live_solve
def test_run_eh_study_dtc_planning_stage():
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _planning_network()
    pack = default_weak_flexible_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=500.0),
        "mc_certify_required": False,
        "dtc_stress_default": False,
        "dtc_planning_default": True,
    })
    PyPSAService.set_network(n)
    store: dict = {}
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=500.0, ens_cap_permyriad=500.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "dtc_planning", "assemble"),
        store=store,
        dtc_config=DtcConfig(
            critical_bus_ids=["crit"],
            critical_load_ids=["critical"],
            islanding_contingencies=["import_poc"],
        ),
    )
    assert report.completeness["dtc"] == "ok"
    assert "eh_dtc_planning" in store
    assert store["eh_dtc_planning"]["mode"] == "planning"
    assert report.sections["dtc"].payload["mode"] == "planning"


def test_default_pipeline_skips_dtc_planning_unless_pack_flag():
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _planning_network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
    })
    assert pack.dtc_planning_default is False
    PyPSAService.set_network(n)
    stop = threading.Event()
    stop.set()  # abort early; only need stage filtering
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=stop,
        log_queue=queue.SimpleQueue(),
        stages=None,
    )
    # Pipeline should list dtc_planning as skipped when pack flag is off.
    stages = {s.stage: s.status for s in report.pipeline.stages}
    assert stages.get("dtc_planning") == "skipped"


def test_claim_wipe_includes_eh_dtc_planning():
    from pathlib import Path
    text = Path("routers/simulation.py").read_text()
    idx = text.find('status="running"')
    chunk = text[idx:idx + 1200]
    assert "eh_dtc_planning=None" in chunk


def test_run_dtc_planning_refuses_missing_ens_cap():
    """Binding P4b-B1: refuse uncapped VoLL-only expansion."""
    from services.pypsa_service import PyPSAService

    n = _planning_network()
    PyPSAService.set_network(n)
    dtc = DtcConfig(
        critical_bus_ids=["crit"],
        critical_load_ids=["critical"],
        islanding_contingencies=["import_poc"],
    )
    with pytest.raises(D.DtcPlanningError, match="ens_cap_permyriad"):
        D.run_dtc_planning(
            n, SolverConfig(voll=500.0, ens_cap_permyriad=None),
            lock=PyPSAService.get_lock(),
            stop_event=threading.Event(),
            log_queue=queue.SimpleQueue(),
            dtc=dtc,
        )

