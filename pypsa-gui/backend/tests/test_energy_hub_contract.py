"""
Energy Hub reference-design contract stub (Phase 0).

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md
Models + fixtures only — no solver / orchestrator behaviour.
"""
from __future__ import annotations

import json
from pathlib import Path

import pydantic
import pytest

from models import energy_hub as EH

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eh_archetypes"


def test_pipeline_constants_match_spec():
    assert EH.DEFAULT_EH_BUDGET_SOLVES == 30
    assert EH.MAX_EH_BUDGET_SOLVES == 120
    assert EH.EH_PIPELINE_STAGES == (
        "apply_pack",
        "ens_solve",
        "frontier",
        "mc_certify",
        "fmea_top",
        "redundancy",
        "levers",
        "dtc_stress",
        "assemble",
    )


def test_default_pipeline_budget_clamped():
    p = EH.EHStudyPipeline()
    assert p.budget_solves == 30
    assert [s.stage for s in p.stages] == list(EH.EH_PIPELINE_STAGES)
    with pytest.raises(pydantic.ValidationError):
        EH.EHStudyPipeline(budget_solves=0)
    with pytest.raises(pydantic.ValidationError):
        EH.EHStudyPipeline(budget_solves=121)


def test_availability_target_requires_a_metric():
    with pytest.raises(pydantic.ValidationError):
        EH.AvailabilityTarget()


def test_availability_target_precedence_fields():
    t = EH.AvailabilityTarget(ens_cap_permyriad=10.0, target_lole_h=3.0,
                              certification_metric="mc_lole")
    assert t.planning_metric == "ens"
    assert t.certification_metric == "mc_lole"


def test_section_status_enum_enforced():
    with pytest.raises(pydantic.ValidationError):
        EH.SectionState(status="missing")


def test_excludes_shed_cost_unfalsifiable():
    r = EH.ReferenceDesignReport(
        archetype="strong_grid",
        pack_hash="a",
        assumptions_hash="b",
        sections=EH.empty_section_map(),
    )
    assert r.excludes_shed_cost is True
    with pytest.raises(pydantic.ValidationError):
        EH.ReferenceDesignReport(
            archetype="strong_grid",
            pack_hash="a",
            assumptions_hash="b",
            excludes_shed_cost=False,
        )


def test_completeness_must_agree_with_sections():
    sections = EH.empty_section_map()
    sections["frontier"] = EH.SectionState(status="skipped")
    with pytest.raises(pydantic.ValidationError):
        EH.ReferenceDesignReport(
            archetype="strong_grid",
            pack_hash="a",
            assumptions_hash="b",
            sections=sections,
            completeness={name: "not_established" for name in EH.REPORT_SECTIONS},
        )


def test_mvp_a_skeleton_allows_not_established_sections():
    r = EH.ReferenceDesignReport(
        archetype="strong_grid",
        pack_hash="fixture-pack",
        assumptions_hash="fixture-assumptions",
        ens_cap_permyriad=10.0,
        sections=EH.empty_section_map(),
    )
    assert r.completeness["redundancy"] == "not_established"
    assert r.completeness["dtc"] == "not_established"
    assert r.cost_at_target_eur is None


@pytest.mark.parametrize("name,factory", [
    ("strong_grid_pack.json", EH.default_strong_grid_pack),
    ("weak_flexible_pack.json", EH.default_weak_flexible_pack),
    ("off_grid_pack.json", EH.default_off_grid_pack),
])
def test_archetype_pack_fixtures_round_trip(name, factory):
    path = FIXTURES / name
    raw = json.loads(path.read_text())
    from_file = EH.ArchetypePack.model_validate(raw)
    expected = factory()
    assert from_file == expected
    assert EH.ArchetypePack.model_validate_json(
        from_file.model_dump_json()) == from_file


def test_mvp_a_report_fixture_round_trips():
    path = FIXTURES / "mvp_a_report_skeleton.json"
    r = EH.ReferenceDesignReport.model_validate_json(path.read_text())
    assert r.archetype == "strong_grid"
    assert r.pipeline.budget_solves == EH.DEFAULT_EH_BUDGET_SOLVES
    assert len(r.pipeline.stages) == len(EH.EH_PIPELINE_STAGES)
    assert set(r.completeness) == set(EH.REPORT_SECTIONS)
    assert all(s == "not_established" for s in r.completeness.values())
    r2 = EH.ReferenceDesignReport.model_validate_json(r.model_dump_json())
    assert r2 == r


def test_import_overlay_firmness_is_planning_limit_only():
    ov = EH.ImportOverlaySpec(import_p_nom_mw=0.0)
    assert ov.import_firmness == "planning_limit_only"
    with pytest.raises(pydantic.ValidationError):
        EH.ImportOverlaySpec(import_firmness="certified_firm")
