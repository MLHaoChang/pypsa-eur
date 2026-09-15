"""
P4a — DtC stress mode (TDD).

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md decision 8
Plan: Phase 4a — fixed-plan islanding re-dispatch; critical unmet vs
non-critical at **bus** aggregate; no per-load shed attribution.
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

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


def _weak_network() -> pypsa.Network:
    """Two-bus hub: critical bus + flex bus; capped import into critical bus.

    Bus-level VOLL attribution (one slack/bus) is the honesty boundary —
    critical and non-critical loads MUST sit on different buses.
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


def test_dtc_config_from_dict_and_sidecar(tmp_path: Path):
    cfg = DtcConfig(
        critical_bus_ids=["crit"],
        islanding_contingencies=["import_poc"],
        attribution="bus_aggregate_not_per_load",
    )
    assert cfg.attribution == "bus_aggregate_not_per_load"
    path = tmp_path / "dtc_config.json"
    path.write_text(json.dumps(cfg.model_dump()))
    loaded = D.load_dtc_config(path)
    assert loaded.critical_bus_ids == ["crit"]


def test_dtc_refuses_per_load_attribution_claim():
    with pytest.raises(Exception, match="per_load|attribution|bus_aggregate"):
        DtcConfig(
            critical_bus_ids=["crit"],
            islanding_contingencies=["import_poc"],
            attribution="per_load_shed",  # type: ignore[arg-type]
        )


def test_apply_islanding_zeros_import_and_undo_restores_static_and_ts():
    """Assessor P4a-B1: undo must restore links_t as well as static."""
    n = _weak_network()
    if "p_max_pu" not in n.links.columns:
        n.links["p_max_pu"] = 1.0
    # Seed a time-series overlay so undo has something to restore.
    n.links_t.p_max_pu = pd.DataFrame(
        1.0, index=n.snapshots, columns=["import_poc"])
    n.links_t.p_min_pu = pd.DataFrame(
        0.0, index=n.snapshots, columns=["import_poc"])
    before_static = float(n.links.at["import_poc", "p_max_pu"])
    before_ts = n.links_t.p_max_pu["import_poc"].copy()
    undo, mut = D.apply_islanding_contingency(n, "import_poc")
    assert float(n.links.at["import_poc", "p_max_pu"]) == 0.0
    assert float(n.links_t.p_max_pu["import_poc"].max()) == 0.0
    assert mut["action"] == "island_import"
    assert "p_max_pu" in mut["restored_time_series"]
    undo()
    assert float(n.links.at["import_poc", "p_max_pu"]) == pytest.approx(before_static)
    assert (n.links_t.p_max_pu["import_poc"] == before_ts).all()


@pytest.mark.live_solve
def test_islanding_stress_separates_critical_and_noncritical_unserved():
    from services.pypsa_service import PyPSAService

    n = _weak_network()
    PyPSAService.set_network(n)
    dtc = DtcConfig(
        critical_bus_ids=["crit"],
        critical_load_ids=["critical"],
        islanding_contingencies=["import_poc"],
    )
    store: dict = {}
    table = D.run_dtc_stress(
        n, SolverConfig(voll=500.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        dtc=dtc,
        store=store,
    )
    assert store["eh_dtc_stress"] is table
    assert table["attribution"] == "bus_aggregate_not_per_load"
    assert "no_per_load_attribution" in table["honesty_notes"]
    rows = [r for r in table["contingencies"] if r["status"] in ("ok", "optimal")]
    assert len(rows) >= 1
    row = rows[0]
    # Assessor P4a-B2: both sides must show unmet, and Class-B island must bind.
    assert row["applied"]["method"] == "p_max_pu_p_min_pu_zero"
    assert row["critical_unserved_mwh"] is not None
    assert row["noncritical_unserved_mwh"] is not None
    assert row["critical_unserved_mwh"] > 0
    assert row["noncritical_unserved_mwh"] > 0
    assert "crit" in row["critical_buses"]
    assert "flex" in row["noncritical_buses"]


@pytest.mark.live_solve
def test_run_eh_study_dtc_stage_default_on_weak_flexible():
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _weak_network()
    pack = default_weak_flexible_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0),
        "mc_certify_required": False,
        "dtc_stress_default": True,
    })
    assert pack.dtc_stress_default is True
    PyPSAService.set_network(n)
    store: dict = {}
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=500.0, ens_cap_permyriad=5000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "dtc_stress", "assemble"),
        store=store,
        dtc_config=DtcConfig(
            critical_bus_ids=["crit"],
            critical_load_ids=["critical"],
            islanding_contingencies=["import_poc"],
        ),
    )
    assert report.completeness["dtc"] in ("ok", "not_established")
    assert "eh_dtc_stress" in store
    assert store["eh_dtc_stress"]["attribution"] == "bus_aggregate_not_per_load"


def test_strong_grid_without_dtc_leaves_section_skipped():
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _weak_network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
        "dtc_stress_default": False,
    })
    PyPSAService.set_network(n)
    store: dict = {}
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
        store=store,
    )
    assert report.completeness.get("dtc") == "skipped"
    assert "eh_dtc_stress" not in store
