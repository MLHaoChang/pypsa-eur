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

def test_soft_skip_surfaces_skipped_kinds_without_store():
    """Binding: skipped_kinds visible on report payload even when store is None."""
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _weak_mvp_b_network()
    pack = default_weak_flexible_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0),
        "mc_certify_required": False,
        "dtc_stress_default": False,  # isolate levers
    })
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=500.0, ens_cap_permyriad=5000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "levers", "assemble"),
        store=None,
    )
    payload = report.sections["levers"].payload
    assert payload is not None
    skipped = payload.get("skipped_kinds") or []
    assert any(s.startswith("storage_duration:") for s in skipped)
    assert payload.get("kind") == "import_cap"


def test_soft_skip_does_not_swallow_config_errors():
    """Binding: non-asset LeverScenarioError must fail closed, not soft-skip."""
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _weak_mvp_b_network()
    # Force a config-class lever failure: empty ens cap makes compare refuse.
    pack = default_weak_flexible_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0),
        "mc_certify_required": False,
        "dtc_stress_default": False,
        "levers": default_weak_flexible_pack().levers.model_copy(update={
            "import_cap": True,
            "storage_duration": False,
        }),
    })
    PyPSAService.set_network(n)
    # Pass cfg with ens_cap wiped after pack availability is set — compare_lever
    # uses availability.ens_cap_permyriad; override availability to None path by
    # monkeypatching compare to raise a non-asset LeverScenarioError.
    from services.adequacy import levers as lev
    real = lev.compare_lever_scenarios

    def _boom(*args, **kwargs):
        raise lev.LeverScenarioError("ens_cap_permyriad must be > 0")

    lev.compare_lever_scenarios = _boom  # type: ignore[assignment]
    try:
        report = S.run_eh_study(
            n, pack, SolverConfig(voll=500.0, ens_cap_permyriad=5000.0),
            lock=PyPSAService.get_lock(),
            stop_event=threading.Event(),
            log_queue=queue.SimpleQueue(),
            stages=("apply_pack", "ens_solve", "levers", "assemble"),
            store={},
        )
    finally:
        lev.compare_lever_scenarios = real  # type: ignore[assignment]
    assert report.completeness["levers"] == "not_established"
    note = report.sections["levers"].note or ""
    assert "ens_cap_permyriad" in note
    assert "soft-skipped" not in note.lower()


def test_all_kinds_soft_skipped_marks_pipeline_skipped():
    """Binding: total soft-skip → not_established + pipeline skipped, skip list kept."""
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    # Network with neither import identity nor storage.
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=10.0)
    n.add("Generator", "g", bus="b", carrier="gas",
          p_nom=20.0, marginal_cost=10.0)
    # Off-grid/weak packs require import Links for apply_pack — use strong +
    # explicit levers stage with both kinds enabled.
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
        "mc_certify_required": False,
        "levers": default_weak_flexible_pack().levers.model_copy(update={
            "import_cap": True,
            "storage_duration": True,
        }),
    })
    PyPSAService.set_network(n)
    store: dict = {}
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "levers", "assemble"),
        store=store,
    )
    assert report.completeness["levers"] == "not_established"
    payload = report.sections["levers"].payload
    assert payload is not None
    skipped = payload.get("skipped_kinds") or []
    assert len(skipped) >= 2
    assert "eh_lever_comparison" in store
    assert store["eh_lever_comparison"].get("skipped_kinds")
    # Pipeline stage must not claim an unqualified run.
    lever_stages = [s for s in report.pipeline.stages if s.stage == "levers"]
    assert lever_stages and lever_stages[0].status == "skipped"



# ── P11: MVP-B DoD with REAL certification (no mc_certify_required override) ─


def _run_default(n, pack):
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    PyPSAService.set_network(n)
    return S.run_eh_study(
        n, pack, SolverConfig(voll=5000.0), lock=PyPSAService.get_lock(),
        stop_event=threading.Event(), log_queue=queue.SimpleQueue(),
        stages=None)


@pytest.mark.live_solve
@pytest.mark.parametrize("factory", [default_weak_flexible_pack,
                                     default_off_grid_pack])
def test_mvp_b_dod_default_packs_certify_on_a_week_long_hub(factory):
    """The unmodified default pack (MC required) on a 168 h hub: certification
    is established and carries a verdict — the real MVP-B DoD."""
    from tests.test_energy_hub_mc_certify import _cert_network

    pack = factory()
    assert pack.mc_certify_required is True
    report = _run_default(_cert_network(), pack)
    assert report.completeness["target"] == "ok"
    sec = report.sections["certification"]
    assert sec.status == "ok", sec.note
    assert sec.payload["verdict"] in ("pass", "fail", "inconclusive")
    assert report.certified is not None
    assert "remote" in sec.payload["fleet_boundary"]["removed_generators"]


@pytest.mark.live_solve
@pytest.mark.parametrize("hours", [4, 12])
def test_mvp_b_short_fixtures_are_refused_with_the_mttr_reason(hours):
    from tests.test_energy_hub_mc_certify import _cert_network

    report = _run_default(_cert_network(hours=hours), default_off_grid_pack())
    sec = report.sections["certification"]
    assert sec.status == "not_established"
    assert "MTTR" in (sec.note or "")
    assert report.certified is None
