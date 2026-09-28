"""
P9 — EH SCR feasibility gate (thin warn-only for weak_flexible).

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md decision 10
Plan: docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md Phase 9

Product rule (pinned here, not in gridspine):
  SCR >= 3 → pass, emt_recommended=False
  SCR < 3  → warn, emt_recommended=True  (incl. <2; fail reserved)
Proxy (no full 60909 study): bus ``eh_sk_mva`` / installed IBR MVA at eh_poc.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from models.energy_hub import (
    AvailabilityTarget,
    GatesBlock,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.solver_service import SolverConfig


def test_scr_gate_module_importable():
    from services.adequacy import scr_gate as G

    assert callable(G.classify_scr)
    assert callable(G.evaluate_network_scr_gate)
    assert G.PASS_SCR == pytest.approx(3.0)


@pytest.mark.parametrize(
    "scr,expected_scr,expected_emt",
    [
        (5.0, "pass", False),
        (3.0, "pass", False),  # edge belongs to pass (gridspine: on-edge → upper)
        (2.9, "warn", True),
        (2.0, "warn", True),
        (1.5, "warn", True),  # thin slice: fail reserved; still warn
    ],
)
def test_classify_scr_product_rule(scr, expected_scr, expected_emt):
    from services.adequacy import scr_gate as G

    gate = G.classify_scr(scr)
    assert isinstance(gate, GatesBlock)
    assert gate.scr == expected_scr
    assert gate.emt_recommended is expected_emt


def _poc_network(*, sk_mva: float, ibr_mva: float | None = None,
                 wind_mw: float = 100.0) -> pypsa.Network:
    """Minimal EH: PoC bus with eh_sk_mva + wind IBR at the same bus."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "wind")
    n.add("Carrier", "gas")
    n.add("Carrier", "AC")
    n.add("Bus", "poc", carrier="AC")
    n.add("Bus", "hub", carrier="AC")
    n.add("Load", "l", bus="hub", p_set=50.0)
    n.add("Generator", "wind1", bus="poc", carrier="wind",
          p_nom=wind_mw, marginal_cost=0.0)
    n.add("Generator", "local", bus="hub", carrier="gas",
          p_nom=80.0, marginal_cost=50.0)
    n.add("Link", "import", bus0="poc", bus1="hub",
          p_nom=100.0, efficiency=1.0, eh_role="grid_import")
    n.buses["eh_poc"] = False
    n.buses.at["poc", "eh_poc"] = True
    n.buses["eh_sk_mva"] = float("nan")
    n.buses.at["poc", "eh_sk_mva"] = sk_mva
    if ibr_mva is not None:
        n.buses["eh_ibr_mva"] = float("nan")
        n.buses.at["poc", "eh_ibr_mva"] = ibr_mva
    return n


def test_evaluate_network_uses_min_scr_across_poc_buses():
    from services.adequacy import scr_gate as G

    n = _poc_network(sk_mva=400.0, wind_mw=100.0)  # SCR=4 → pass
    # Second PoC, weaker.
    n.add("Bus", "poc2", carrier="AC")
    n.buses.at["poc2", "eh_poc"] = True
    n.buses.at["poc2", "eh_sk_mva"] = 250.0  # will need IBR
    n.add("Generator", "wind2", bus="poc2", carrier="wind",
          p_nom=100.0, marginal_cost=0.0)  # SCR=2.5 → warn

    gate, status, payload, note = G.evaluate_network_scr_gate(n)
    assert status == "ok"
    assert gate is not None
    assert gate.scr == "warn"
    assert gate.emt_recommended is True
    assert payload["min_scr"] == pytest.approx(2.5)
    assert payload["method"] == "eh_sk_mva_proxy"
    assert note and "warn" in note.lower()


def test_evaluate_network_ibr_override_on_bus():
    from services.adequacy import scr_gate as G

    # wind_mw=100 but override ibr=50 → SCR = 200/50 = 4 → pass
    n = _poc_network(sk_mva=200.0, ibr_mva=50.0, wind_mw=100.0)
    gate, status, payload, _note = G.evaluate_network_scr_gate(n)
    assert status == "ok"
    assert gate.scr == "pass"
    assert gate.emt_recommended is False
    assert payload["buses"][0]["ibr_mva"] == pytest.approx(50.0)
    assert payload["buses"][0]["scr"] == pytest.approx(4.0)


def test_evaluate_network_not_established_without_sk():
    from services.adequacy import scr_gate as G

    n = _poc_network(sk_mva=300.0)
    n.buses.at["poc", "eh_sk_mva"] = float("nan")
    gate, status, payload, note = G.evaluate_network_scr_gate(n)
    assert gate is None
    assert status == "not_established"
    assert payload is None
    assert note and "eh_sk_mva" in note


def test_evaluate_network_fail_closed_when_any_poc_incomplete():
    """Partial PoC coverage must not soft-ok over the computable subset."""
    from services.adequacy import scr_gate as G

    n = _poc_network(sk_mva=400.0, wind_mw=100.0)  # poc SCR=4 would pass alone
    n.add("Bus", "poc2", carrier="AC")
    n.buses.at["poc2", "eh_poc"] = True
    # poc2 tagged but no eh_sk_mva / IBR → incomplete
    gate, status, payload, note = G.evaluate_network_scr_gate(n)
    assert gate is None
    assert status == "not_established"
    assert payload is None
    assert note and "ALL eh_poc" in note and "poc2" in note


def test_assemble_accepts_gates_block():
    from services.adequacy import eh_report as R
    from models.energy_hub import EHStudyPipeline

    report = R.assemble_reference_design_report(
        archetype="weak_flexible",
        pack_hash="deadbeef",
        assumptions_hash="cafebabe",
        section_payloads={
            "gates": ("ok", {"min_scr": 2.5, "method": "eh_sk_mva_proxy"}, "SCR warn"),
        },
        pipeline=EHStudyPipeline(),
        gates=GatesBlock(scr="warn", emt_recommended=True),
    )
    assert report.gates is not None
    assert report.gates.scr == "warn"
    assert report.gates.emt_recommended is True
    assert report.completeness["gates"] == "ok"


def test_weak_flexible_study_fills_gates_warn():
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _poc_network(sk_mva=250.0, wind_mw=100.0)  # SCR=2.5 → warn
    # Need enough local supply for a cheap ens solve.
    n.generators.at["local", "p_nom"] = 100.0
    pack = default_weak_flexible_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0),
        "dtc_stress_default": False,
        "levers": default_weak_flexible_pack().levers.model_copy(update={
            "import_cap": False, "storage_duration": False,
        }),
    })
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=5000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
    )
    assert report.completeness["gates"] == "ok"
    assert report.gates is not None
    assert report.gates.scr == "warn"
    assert report.gates.emt_recommended is True
    assert report.sections["gates"].payload["min_scr"] == pytest.approx(2.5)


def test_weak_flexible_gates_ok_does_not_imply_mc_certify():
    """SCR gates.ok is orthogonal to required-but-missing mc_certify."""
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _poc_network(sk_mva=250.0, wind_mw=100.0)
    n.generators.at["local", "p_nom"] = 100.0
    pack = default_weak_flexible_pack().model_copy(update={
        "availability": AvailabilityTarget(
            ens_cap_permyriad=5000.0, target_lole_h=3.0,
            certification_metric="mc_lole"),
        "mc_certify_required": True,
        "dtc_stress_default": False,
        "levers": default_weak_flexible_pack().levers.model_copy(update={
            "import_cap": False, "storage_duration": False,
        }),
    })
    assert pack.mc_certify_required is True
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=5000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),  # mc omitted
    )
    assert report.completeness["gates"] == "ok"
    assert report.gates is not None
    assert report.gates.scr == "warn"
    mc_rec = next(s for s in report.pipeline.stages if s.stage == "mc_certify")
    assert mc_rec.status == "skipped"
    assert mc_rec.note and "required" in mc_rec.note
    assert "not implemented" in mc_rec.note or "not_established" in mc_rec.note


def test_strong_grid_study_skips_scr_gate():
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "cheap", bus="b", carrier="gas",
          p_nom=60.0, marginal_cost=10.0)
    n.add("Generator", "backup", bus="b", carrier="gas",
          p_nom=40.0, marginal_cost=200.0)

    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
    })
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
    )
    assert report.completeness["gates"] == "skipped"
    assert report.gates is None
