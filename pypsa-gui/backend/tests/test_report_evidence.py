"""
WP2 — the evidence collector behind the LLM-assisted study report.

Plan: docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md
(WP2). ``collect_evidence`` is the one union of the adequacy
``build_study_report`` payload, the Energy Hub ``ReferenceDesignReport`` and
the FMEA worksheet; the tests pin what the assessment calls non-negotiable:
a section that was not measured is stated with the hint the chat tool gives
(never omitted), ``None`` never becomes ``0`` in a table cell, the hash is a
function of the numbers alone, every number the prose may quote has a path,
and a section's prompt slice carries nothing from another section.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pypsa

from models.energy_hub import REPORT_SECTIONS
from services.adequacy.study_report import SECTION_ORDER, build_study_report
from services.reports import formatting as F
from services.reports.evidence import (
    ADEQUACY_SECTION_IDS,
    NO_DATA_HINTS,
    Evidence,
    EvidenceTable,
    NumberFact,
    adequacy_section_id,
    collect_evidence,
    evidence_hash,
    flatten_numbers,
    slice_for,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eh_archetypes"


# ── fixtures ─────────────────────────────────────────────────────────────


def _skeleton() -> dict:
    return json.loads((FIXTURES / "mvp_a_report_skeleton.json").read_text())


def _populated() -> dict:
    """
    The P5 MVP-A report with the six sections increment 1 tabulates
    (same dict as test_report_docx_writer's fixture — copied, not imported).
    """
    report = _skeleton()
    report.update({
        "ens_cap_permyriad": 10.0,
        "achieved_ens_permyriad": 8.25,
        "achieved_shed_hours": 12.5,
        "mc_lole_h": 3.21,
        "cost_at_target_eur": 1_234_567.89,
        "period_basis": "single_period",
        "tea": {"lcoe_eur_per_mwh": 61.7, "lcoh_eur_per_kg": None,
                "notes": "LCOE = cost / served", "lcoh_status": "skipped",
                "lcoh_note": "LCOH skipped: the network has no electrolyser Links"},
        "gates": {"scr": "pass", "emt_recommended": False},
    })
    s = report["sections"]
    s["target"] = {"status": "ok", "note": None, "payload": {
        "binding": "ens_cap",
        "system": {"cap_mwh": 990.0, "achieved_ens_mwh": 816.75,
                   "achieved_shed_hours": 12.5},
        "metrics": {"demand_mwh": 99_000.0, "ens_mwh": 816.75},
    }}
    s["cost"] = {"status": "ok", "note": None, "payload": {
        "total_system_cost_eur": 1_234_567.89, "period_basis": "single_period",
        "excludes_shed_cost": True}}
    s["sizing"] = {"status": "ok", "note": None, "payload": {
        "by_carrier": {"gas": 100.0, "wind": 25.0}, "total_p_nom_mw": 125.0}}
    s["certification"] = {"status": "ok", "note": "certified at 200 draws",
                          "payload": {
        "metric": "mc_lole", "target_lole_h": 4.0, "mc_lole_h": 3.21,
        "lole_ci": [2.9, 3.5], "eue_mwh": 41.2, "eue_ci": [36.0, 46.0],
        "n_samples": 200, "draws_requested": 200, "converged": True,
        "verdict": "certified", "engine": "mc", "fidelity": "sequential_mc",
        "warning": "MC LOLE is SAMPLED: the interval travels with the mean",
    }}
    s["frontier"] = {"status": "ok", "note": "5 points around 10‱", "payload": {
        "points": [
            {"target_permyriad": 40.0, "status": "ok", "period_basis": "single_period",
             "point": {"cap_mwh": 3960.0, "achieved_ens_mwh": 3900.0,
                       "achieved_shed_hours": 60.0,
                       "total_system_cost_eur": 900_000.0}},
            {"target_permyriad": 10.0, "status": "ok", "period_basis": "single_period",
             "point": {"cap_mwh": 990.0, "achieved_ens_mwh": 816.75,
                       "achieved_shed_hours": 12.5,
                       "total_system_cost_eur": 1_234_567.89}},
            {"target_permyriad": 2.5, "status": "infeasible", "point": None},
        ],
        "knee_index": 1, "voll_eur_per_mwh": 3000.0, "warning": None,
        "period_basis": "single_period", "excludes_shed_cost": True,
        "engine": "lp_proxy",
    }}
    s["fmea_top"] = {"status": "ok", "note": None, "payload": {
        "top": [
            {"rank": 1, "mode_id": "gen:backup:forced_outage",
             "component_class": "Generator", "name": "backup",
             "failure_class": "A", "occurrence_per_year": 2.5,
             "occurrence_basis": "FOR", "severity_eur": 40_000.0,
             "criticality_eur_per_year": 100_000.0, "delta_eue_mwh": 13.3,
             "engine": "copt", "fidelity": "analytic_convolution"},
            {"rank": 2, "mode_id": "link:import:forced_outage",
             "component_class": "Link", "name": "import",
             "failure_class": "B", "occurrence_per_year": 1.0,
             "occurrence_basis": "FOR", "severity_eur": 25_000.0,
             "criticality_eur_per_year": 25_000.0, "delta_eue_mwh": None,
             "engine": "lp_proxy", "fidelity": "deterministic_scenario"},
        ],
        "top_n": 10, "n_total_modes": 2, "classes_included": ["A", "B"],
        "class_b": {"status": "run", "reason": None, "solves_charged": 3},
        "voll_eur_per_mwh": 3000.0,
        "note": ("Link-primary residual risk (Class-B Link sweep); "
                 "AC Line/Transformer N-1 remains on SCLOPF and is omitted "
                 "from FMEA ranking"),
    }}
    for name in ("target", "cost", "sizing", "certification", "frontier",
                 "fmea_top"):
        report["completeness"][name] = "ok"
    return report


def _worksheet() -> dict:
    return {
        "__schema__": 1, "version": 3,
        "manual_rows": [{
            "mode_id": "expert:control_room:cyber",
            "component_class": "ControlSystem", "name": "SCADA outage",
            "failure_class": "D", "occurrence_per_year": 0.2,
            "occurrence_basis": "expert", "severity_eur": 500_000.0,
            "criticality_eur_per_year": 100_000.0, "in_metric_scope": True,
            "mitigability": "high", "rate_source": "operator interview",
            "engine": "expert", "fidelity": "expert_judgement",
        }],
        "overlays": {"gen:backup:forced_outage": {"mitigability": "medium",
                                                  "notes": "spare on site"}},
    }


def _read_with(**payloads):
    def read(section: str):
        payload = payloads.get(section)
        if payload is None:
            return {"status": "no_data", "message": f"no {section} yet"}
        return payload
    return read


def _study_report(**payloads) -> dict:
    return build_study_report(pypsa.Network(), _read_with(**payloads))


def _cells(table: EvidenceTable) -> list[str]:
    return [cell for row in table.rows for cell in row]


def _all_keys(obj, out: set[str] | None = None) -> set[str]:
    out = set() if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k))
            _all_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _all_keys(v, out)
    return out


# ── nothing present: the omission is the finding ─────────────────────────


def test_no_inputs_every_section_not_established_with_hint():
    ev = collect_evidence(study_report=None, eh_report=None, worksheet=None)
    assert isinstance(ev, Evidence)
    # The EH `frontier` and the adequacy `frontier` are different studies;
    # both must survive the union, so the adequacy one is aliased.
    assert adequacy_section_id("frontier") == "adequacy_frontier"
    assert set(ADEQUACY_SECTION_IDS) == {adequacy_section_id(s) for s in SECTION_ORDER}
    for name in REPORT_SECTIONS:
        section = ev.sections[name]
        assert section.kind == "eh"
        assert section.status == "not_established", name
        assert section.note == NO_DATA_HINTS["eh_reference_design"], name
    for study_id in SECTION_ORDER:
        section = ev.sections[adequacy_section_id(study_id)]
        assert section.kind == "adequacy"
        assert section.study_id == study_id
        assert section.status == "not_established", study_id
        assert section.note == NO_DATA_HINTS[study_id], study_id
    # The hints are the ones get_adequacy_results gives.
    assert "Monte-Carlo" in ev.sections["mc"].note
    assert "run_eh_study" in ev.sections["fmea_top"].note
    assert len(ev.not_established) >= len(REPORT_SECTIONS) + len(SECTION_ORDER)
    assert ev.tables == {}


def test_no_inputs_disclosures_say_nothing_was_measured():
    ev = collect_evidence(study_report=None, eh_report=None, worksheet=None)
    assert ev.required_disclosures
    assert any("nothing" in d.lower() and "measured" in d.lower()
               for d in ev.required_disclosures)


def test_skeleton_eh_report_states_every_section_with_title():
    skeleton = _skeleton()
    skeleton["sections"]["certification"]["status"] = "skipped"
    skeleton["sections"]["certification"]["note"] = "MC certification not requested"
    skeleton["completeness"]["certification"] = "skipped"
    ev = collect_evidence(study_report=None, eh_report=skeleton, worksheet=None)
    assert ev.sections["certification"].status == "skipped"
    line = next(x for x in ev.not_established if "Certification" in x)
    assert "MC certification not requested" in line
    assert ev.sections["target"].status == "not_established"
    assert "fmea_top" not in ev.tables and "frontier" not in ev.tables
    # A headline that is all None still has a table, with no zeros in it.
    assert "0" not in _cells(ev.tables["headline"])
    assert F.NOT_ESTABLISHED in _cells(ev.tables["headline"])


# ── the populated MVP-A fixture ──────────────────────────────────────────


def test_fmea_top_table_rows_follow_the_payload_in_order():
    report = _populated()
    ev = collect_evidence(study_report=None, eh_report=report, worksheet=None)
    table = ev.tables["fmea_top"]
    top = report["sections"]["fmea_top"]["payload"]["top"]
    assert len(table.rows) == len(top)
    assert table.source_path == "/sections/fmea_top/payload/top"
    assert table.columns[0] == "Rank"
    for row, mode in zip(table.rows, top):
        assert row[0] == F.fmt_number(mode["rank"])
        assert mode["name"] in row
        assert F.fmt_number(mode["criticality_eur_per_year"], unit="€/yr") in row
    # The class-B row's None ΔEUE is stated, not zeroed.
    assert F.NOT_ESTABLISHED in table.rows[1]
    assert "0" not in table.rows[1] and "0.00" not in table.rows[1]
    assert "A, B" in (table.caption or "")


def test_headline_table_shows_cost_with_period_basis_and_excludes():
    ev = collect_evidence(study_report=None, eh_report=_populated(), worksheet=None)
    table = ev.tables["headline"]
    assert table.source_path == "/headline"
    cost_row = next(r for r in table.rows if r[0].startswith("Cost at target"))
    assert "1,234,568" in cost_row[1]
    assert "single_period" in cost_row[1]
    assert "excludes" in cost_row[1]
    assert ev.headline["cost_at_target_eur"] == 1_234_567.89
    assert ev.headline["excludes_shed_cost"] is True
    assert ev.headline["archetype"] == "strong_grid"


def test_populated_sections_carry_status_engine_fidelity_and_note():
    ev = collect_evidence(study_report=None, eh_report=_populated(), worksheet=None)
    cert = ev.sections["certification"]
    assert cert.status == "ok"
    assert cert.note == "certified at 200 draws"
    assert (cert.engine, cert.fidelity) == ("mc", "sequential_mc")
    assert ev.sections["frontier"].engine == "lp_proxy"
    assert ev.sections["redundancy"].status == "not_established"
    for name in ("headline", "completeness", "certification", "frontier",
                 "sizing", "fmea_top", "tea", "gates", "pipeline"):
        assert name in ev.tables, name
    assert "redundancy" not in ev.tables
    assert "adequacy_sections" not in ev.tables
    # The engine's own standing warning travels with the result.
    assert any("interval travels with the mean" in d
               for d in ev.required_disclosures)
    assert any("Link-primary" in d for d in ev.required_disclosures)


def test_frontier_table_marks_knee_and_infeasible_point():
    ev = collect_evidence(study_report=None, eh_report=_populated(), worksheet=None)
    table = ev.tables["frontier"]
    assert table.source_path == "/sections/frontier/payload/points"
    assert len(table.rows) == 3
    assert "knee" in table.rows[1][1]
    assert table.rows[2][1].startswith("infeasible")
    assert F.NOT_ESTABLISHED in table.rows[2]


# ── hash ──────────────────────────────────────────────────────────────────


def test_same_state_hashes_identically_and_one_number_changes_it():
    a = collect_evidence(study_report=None, eh_report=_populated(),
                         worksheet=_worksheet())
    b = collect_evidence(study_report=None, eh_report=_populated(),
                         worksheet=_worksheet())
    assert evidence_hash(a) == evidence_hash(b)
    assert len(evidence_hash(a)) == 64
    changed = _populated()
    changed["sections"]["fmea_top"]["payload"]["top"][0]["severity_eur"] = 40_001.0
    c = collect_evidence(study_report=None, eh_report=changed, worksheet=_worksheet())
    assert evidence_hash(c) != evidence_hash(a)


def test_hash_ignores_noise_beyond_six_significant_digits_and_nan():
    base = _populated()
    noisy = copy.deepcopy(base)
    noisy["achieved_ens_permyriad"] = 8.25 + 1e-9
    a = collect_evidence(study_report=None, eh_report=base, worksheet=None)
    b = collect_evidence(study_report=None, eh_report=noisy, worksheet=None)
    assert evidence_hash(a) == evidence_hash(b)
    nan = copy.deepcopy(base)
    nan["sections"]["certification"]["payload"]["eue_mwh"] = math.nan
    ev = collect_evidence(study_report=None, eh_report=nan, worksheet=None)
    assert isinstance(evidence_hash(ev), str)  # NaN is null, not a crash
    eue_row = next(r for r in ev.tables["certification"].rows if r[0] == "EUE")
    assert eue_row[1] == F.NOT_ESTABLISHED


# ── flatten_numbers ──────────────────────────────────────────────────────


def test_flatten_numbers_covers_every_numeric_leaf_of_fmea_top():
    report = _populated()
    ev = collect_evidence(study_report=None, eh_report=report, worksheet=None)
    facts = flatten_numbers(ev)
    assert all(isinstance(f, NumberFact) for f in facts)
    by_path = {f.path: f for f in facts}
    top = report["sections"]["fmea_top"]["payload"]["top"]
    for i, mode in enumerate(top):
        for key, value in mode.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                path = f"/sections/fmea_top/payload/top/{i}/{key}"
                assert path in by_path, path
                assert by_path[path].value == value
    crit = by_path["/sections/fmea_top/payload/top/0/criticality_eur_per_year"]
    assert crit.value == 100_000.0
    assert crit.unit == "€/yr"
    assert by_path["/sections/fmea_top/payload/top/0/rank"].unit == "count"
    assert by_path["/sections/fmea_top/payload/top_n"].unit == "count"
    assert by_path["/sections/fmea_top/payload/n_total_modes"].unit == "count"
    assert by_path["/sections/fmea_top/payload/top/0/occurrence_per_year"].unit == "/yr"
    assert by_path["/sections/fmea_top/payload/top/0/severity_eur"].unit == "€"
    assert by_path["/sections/fmea_top/payload/top/0/delta_eue_mwh"].unit == "MWh"
    # A None leaf is not a number and must not appear as 0.
    assert "/sections/fmea_top/payload/top/1/delta_eue_mwh" not in by_path


def test_flatten_numbers_covers_headline_with_unit_hints():
    ev = collect_evidence(study_report=None, eh_report=_populated(), worksheet=None)
    by_path = {f.path: f for f in flatten_numbers(ev)}
    assert by_path["/headline/ens_cap_permyriad"].unit == "‱"
    assert by_path["/headline/mc_lole_h"].unit == "h"
    assert by_path["/headline/achieved_shed_hours"].unit == "h"
    assert by_path["/headline/cost_at_target_eur"].unit == "€"
    assert by_path["/headline/tea/lcoe_eur_per_mwh"].unit == "€/MWh"
    assert by_path["/sections/certification/payload/n_samples"].unit == "count"
    assert by_path["/sections/certification/payload/draws_requested"].unit == "count"
    assert by_path["/sections/certification/payload/lole_ci/0"].unit == "h"
    assert by_path["/sections/sizing/payload/total_p_nom_mw"].unit == "MW"
    assert by_path["/sections/target/payload/system/cap_mwh"].unit == "MWh"
    assert by_path["/headline/excludes_shed_cost"].unit == "count"
    # Not-established headline fields produce no fact at all.
    assert "/headline/tea/lcoh_eur_per_kg" not in by_path


# ── slices ───────────────────────────────────────────────────────────────


def test_slice_for_fmea_top_contains_nothing_from_frontier():
    ev = collect_evidence(study_report=None, eh_report=_populated(), worksheet=None)
    piece = slice_for(ev, "fmea_top")
    keys = _all_keys(piece)
    assert piece["section_id"] == "fmea_top"
    assert piece["status"] == "ok"
    assert piece["payload"]["top"][0]["rank"] == 1
    assert "frontier" not in keys
    assert not {"points", "knee_index"} & keys
    assert "certification" not in keys and "lole_ci" not in keys
    assert "fmea_top" in piece["table_ids"]
    assert "frontier" not in piece["table_ids"]
    assert any("Link-primary" in d for d in piece["required_disclosures"])
    assert not any("interval travels" in d for d in piece["required_disclosures"])
    assert piece["headline"]["cost_at_target_eur"] == 1_234_567.89
    assert "tea" not in piece["headline"] and "gates" not in piece["headline"]


def test_slice_for_missing_section_states_why():
    ev = collect_evidence(study_report=None, eh_report=None, worksheet=None)
    piece = slice_for(ev, "mc")
    assert piece["status"] == "not_established"
    assert "Monte-Carlo" in piece["note"]
    assert piece["payload"] is None
    assert piece["table_ids"] == []


# ── ADR-0001: None is never 0 ────────────────────────────────────────────


def test_none_in_payload_never_renders_as_zero_in_any_cell():
    report = _populated()
    report["sections"]["certification"]["payload"]["eue_mwh"] = None
    report["sections"]["certification"]["payload"]["eue_ci"] = [None, None]
    report["sections"]["sizing"]["payload"]["total_p_nom_mw"] = None
    report["mc_lole_h"] = None
    ev = collect_evidence(study_report=None, eh_report=report, worksheet=None)
    for table_id, table in ev.tables.items():
        if table_id == "pipeline":
            # `solves_charged: 0` on a pending stage is a measured zero, not
            # a missing number; the pipeline table is checked on its own.
            continue
        for row in table.rows:
            for cell in row:
                assert cell != "0" and cell != "0.00" and cell != "0.0", \
                    (table_id, row)
                assert not cell.startswith("0 "), (table_id, row)
    cert = {r[0]: r[1] for r in ev.tables["certification"].rows}
    assert cert["EUE"] == F.NOT_ESTABLISHED
    assert cert["EUE interval"] == F.NOT_ESTABLISHED
    sizing_total = ev.tables["sizing"].rows[-1]
    assert sizing_total[0] == "Total" and sizing_total[1] == F.NOT_ESTABLISHED
    lole = next(r for r in ev.tables["headline"].rows if r[0].startswith("MC LOLE"))
    assert lole[1] == F.NOT_ESTABLISHED
    # A pipeline stage with no note is an empty cell, never a zero; its
    # solves are the ints the runner recorded.
    for row in ev.tables["pipeline"].rows:
        assert row[3] == "" or row[3] != "0"
        assert row[2] == F.fmt_number(0)


# ── worksheet ────────────────────────────────────────────────────────────


def test_worksheet_class_d_rows_become_fmea_expert_rows():
    ev = collect_evidence(study_report=None, eh_report=None, worksheet=_worksheet())
    table = ev.tables["fmea_expert_rows"]
    assert table.source_path == "/sections/fmea_expert_rows/payload/manual_rows"
    assert len(table.rows) == 1
    row = table.rows[0]
    assert "SCADA outage" in row
    assert F.fmt_number(500_000.0, unit="€") in row
    assert "high" in row
    assert "expert" in (table.caption or "")
    section = ev.sections["fmea_expert_rows"]
    assert section.status == "ok"
    assert section.engine == "expert"
    assert section.payload["overlays"]["gen:backup:forced_outage"]["notes"] == "spare on site"


def test_empty_worksheet_gives_no_expert_table():
    ev = collect_evidence(study_report=None, eh_report=None,
                          worksheet={"manual_rows": [], "overlays": {}, "version": 0})
    assert "fmea_expert_rows" not in ev.tables
    assert ev.sections["fmea_expert_rows"].status == "not_established"


# ── the adequacy study report ────────────────────────────────────────────


def test_adequacy_sections_union_with_engine_fidelity_and_reason():
    sr = _study_report(
        copt={"engine": "copt", "fidelity": "analytic_convolution",
              "metrics": {"lole_hours": 4.0, "eue_mwh": 12.5}},
        mc={"result": {"engine": "mc", "fidelity": "sequential_mc",
                       "metrics": {"converged": False, "n_samples": 200,
                                   "lole_ci": [2.0, 5.0], "lole_hours": 3.5}}},
    )
    ev = collect_evidence(study_report=sr, eh_report=None, worksheet=None)
    copt = ev.sections["copt"]
    assert copt.status == "ok"
    assert (copt.engine, copt.fidelity) == ("copt", "analytic_convolution")
    assert copt.payload["metrics"]["lole_hours"] == 4.0
    assert "screening only" in copt.note
    mc = ev.sections["mc"]
    assert (mc.engine, mc.fidelity) == ("mc", "sequential_mc")
    frontier = ev.sections["adequacy_frontier"]
    assert frontier.status == "not_established"
    assert frontier.study_id == "frontier"
    assert frontier.source == "get_adequacy_results('frontier')"
    assert "no frontier yet" in frontier.note
    # The EH frontier is a different section and keeps its own hint.
    assert "run_eh_study" in ev.sections["frontier"].note
    # The study report's own lists ride through untouched.
    for line in sr["required_disclosures"]:
        assert line in ev.required_disclosures
    for line in sr["not_established"]:
        assert line in ev.not_established
    assert ev.evidence_gaps == sr["evidence_gaps"]
    table = ev.tables["adequacy_sections"]
    assert table.source_path == "/sections"
    assert [r[0] for r in table.rows] == list(SECTION_ORDER)
    copt_row = next(r for r in table.rows if r[0] == "copt")
    assert "established" in copt_row and "copt" in copt_row
    frontier_row = next(r for r in table.rows if r[0] == "frontier")
    assert F.NOT_ESTABLISHED in frontier_row
    # EH sections are still there, still stated.
    assert ev.sections["fmea_top"].status == "not_established"
    by_path = {f.path: f for f in flatten_numbers(ev)}
    assert by_path["/sections/copt/payload/metrics/lole_hours"].unit == "h"
    assert by_path["/sections/copt/payload/metrics/eue_mwh"].value == 12.5


def test_slice_for_adequacy_section_carries_caveat_and_its_disclosures():
    sr = _study_report(
        copt={"engine": "copt", "fidelity": "analytic_convolution",
              "metrics": {"lole_hours": 4.0}},
        reserve_margin={"result": {"margin": 0.15}},
    )
    ev = collect_evidence(study_report=sr, eh_report=_populated(), worksheet=None)
    piece = slice_for(ev, "copt")
    assert "screening" in piece["note"].lower()
    assert any("COPT" in d for d in piece["required_disclosures"])
    assert not any("reserve margin" in d for d in piece["required_disclosures"])
    keys = _all_keys(piece)
    assert "margin" not in keys and "top" not in keys
