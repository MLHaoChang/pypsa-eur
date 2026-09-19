"""
P5 — ReferenceDesignReport enrichment (sizing, TEA, export, GET store).

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md §4
Plan: Phase 5 — assembler enrichment for MVP-A.
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
    EHStudyPipeline,
    ReferenceDesignReport,
    SectionState,
    default_strong_grid_pack,
    empty_section_map,
)
from services.adequacy import eh_report as R
from services.adequacy import eh_study as S
from services.solver_service import SolverConfig

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eh_archetypes"


def _ens_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas")
    n.add("Carrier", "wind")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "cheap", bus="b", carrier="gas",
          p_nom=60.0, marginal_cost=10.0)
    n.add("Generator", "backup", bus="b", carrier="gas",
          p_nom=40.0, marginal_cost=200.0)
    n.add("Generator", "wind1", bus="b", carrier="wind",
          p_nom=25.0, marginal_cost=0.0, p_max_pu=0.3)
    return n


def test_sizing_summary_groups_p_nom_by_carrier():
    n = _ens_network()
    sizing = R.sizing_summary_from_network(n)
    assert sizing["by_carrier"]["gas"] == pytest.approx(100.0)
    assert sizing["by_carrier"]["wind"] == pytest.approx(25.0)
    assert sizing["total_p_nom_mw"] == pytest.approx(125.0)


def test_tea_lcoe_is_cost_over_served_energy():
    tea = R.compute_tea(cost_eur=9_720.0, served_energy_mwh=1_080.0)
    assert tea.lcoe_eur_per_mwh == pytest.approx(9_720.0 / 1_080.0)
    assert tea.lcoh_eur_per_kg is None


def test_served_energy_subtracts_ens_from_demand():
    n = _ens_network()
    # Demand = 100 MW × 4 h × weight 3 = 1200 MWh; ENS 120 → served 1080.
    served = R.served_energy_mwh_from_network(n, ens_mwh=120.0)
    assert served == pytest.approx(1080.0)
    tea = R.compute_tea(cost_eur=61_200.0, served_energy_mwh=served)
    assert tea.lcoe_eur_per_mwh == pytest.approx(61_200.0 / 1080.0)


def test_tea_lcoe_none_when_energy_missing():
    tea = R.compute_tea(cost_eur=100.0, served_energy_mwh=0.0)
    assert tea.lcoe_eur_per_mwh is None
    assert tea.notes


def test_export_shape_is_stable_golden_keys():
    sections = empty_section_map(default="skipped")
    sections["target"] = SectionState(
        status="ok", payload={"binding": "system_cap"})
    sections["cost"] = SectionState(
        status="ok",
        payload={"total_system_cost_eur": 1.0, "period_basis": "single_period"},
    )
    sections["frontier"] = SectionState(
        status="not_established", note="not run")
    report = ReferenceDesignReport(
        archetype="strong_grid",
        pack_hash="deadbeef",
        assumptions_hash="cafebabe",
        ens_cap_permyriad=1000.0,
        cost_at_target_eur=1.0,
        period_basis="single_period",
        sections=sections,
        pipeline=EHStudyPipeline(),
    )
    exported = R.export_reference_design(report)
    expected_keys = json.loads(
        (FIXTURES / "mvp_a_export_keys.json").read_text())["keys"]
    assert list(exported.keys()) == expected_keys
    again = json.loads(json.dumps(exported))
    assert list(again.keys()) == expected_keys


@pytest.mark.live_solve
def test_mvp_a_study_fills_sizing_and_tea():
    from services.pypsa_service import PyPSAService

    n = _ens_network()
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
    assert report.completeness["cost"] == "ok"
    assert report.completeness["sizing"] == "ok"
    assert report.sections["sizing"].payload["total_p_nom_mw"] > 0
    assert report.completeness["tea"] == "ok"
    assert report.tea is not None
    assert report.tea.lcoe_eur_per_mwh is not None
    # LCOE must use served energy (demand − ENS), not gross demand.
    cost = float(report.cost_at_target_eur)
    # Demand 100×4×3=1200; ENS at 1000‱ of 1200 = 120; served = 1080.
    assert report.tea.lcoe_eur_per_mwh == pytest.approx(cost / 1080.0, rel=1e-3)
    assert report.completeness["frontier"] in ("skipped", "not_established")
    assert report.completeness["redundancy"] in ("skipped", "not_established")
    assert report.completeness["dtc"] in ("skipped", "not_established")


def test_store_and_load_last_eh_report():
    sections = empty_section_map(default="skipped")
    sections["cost"] = SectionState(
        status="ok", payload={"total_system_cost_eur": 2.0})
    report = ReferenceDesignReport(
        archetype="strong_grid",
        pack_hash="a",
        assumptions_hash="b",
        cost_at_target_eur=2.0,
        sections=sections,
    )
    store: dict = {}
    R.store_eh_report(store, report)
    loaded = R.load_eh_report(store)
    assert loaded is not None
    assert loaded.cost_at_target_eur == 2.0
    assert R.load_eh_report({}) is None


def test_get_eh_reference_design_payload_204_when_missing():
    body, status = R.eh_reference_design_http_payload({})
    assert status == 204
    assert body is None


def test_get_eh_reference_design_payload_200_when_present():
    sections = empty_section_map(default="skipped")
    sections["cost"] = SectionState(status="ok", payload={"x": 1})
    report = ReferenceDesignReport(
        archetype="strong_grid",
        pack_hash="a",
        assumptions_hash="b",
        sections=sections,
    )
    store: dict = {}
    R.store_eh_report(store, report)
    body, status = R.eh_reference_design_http_payload(store)
    assert status == 200
    assert body["archetype"] == "strong_grid"
    assert list(body.keys()) == list(R.EXPORT_KEYS)
