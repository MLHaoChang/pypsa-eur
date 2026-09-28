"""
The evidence collector — one union over everything a study report may quote.

WP2 of docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md;
the gap named in the feasibility assessment §1.1: ``build_study_report``
covers the adequacy surfaces, ``ReferenceDesignReport`` covers the Energy
Hub, and neither knows the other. This module joins them, plus the FMEA
worksheet's expert rows, into one ``Evidence`` that the generator (WP3),
the number audit, the figures (WP4) and the writer (WP5) all read.

Rules it holds to:

* **Pure.** The caller passes the dicts; nothing here touches the network,
  the result state or a file, so it is testable with fixtures alone.
* **Every section is present, established or not.** The adequacy sections
  keep their ``engine``/``fidelity``; the EH sections carry their
  ``status`` and ``note`` the same way. A section that has no input at all
  is ``not_established`` with the hint ``get_adequacy_results`` gives, so
  the omission is stated rather than silent.
* **Tables are built here, once, by code**, every cell through
  ``services.reports.formatting`` so ``None`` is "not established" and
  never 0 (ADR-0001). Each table names its ``source_path`` (a JSON pointer
  into the evidence) so the audit and the viewer can cite it.
* **The hash is a function of the evidence alone** — canonical JSON, floats
  to six significant digits, NaN/inf as null — so a report knows when the
  state it was written from has moved.
* **Slices are small.** ``slice_for`` gives a section's prompt its own
  payload, the headline and the disclosures that concern it, and nothing
  from any other section (assessment §7, decision 9).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from models.energy_hub import REPORT_SECTIONS
from services.adequacy.study_report import (
    _SECTION_CAVEATS,
    _SECTION_QUESTION,
    SECTION_ORDER,
)
from services.reports import formatting as F

# ── vocabulary ───────────────────────────────────────────────────────────

WORKSHEET_SECTION = "fmea_expert_rows"

# One id lives in both vocabularies: the EH stage ``frontier`` and the
# adequacy campaign's ``frontier`` study are different runs of the same
# engine. The EH one keeps the bare id (it is the report's first target);
# the adequacy one is keyed by its alias, and ``study_id`` keeps the name
# ``get_adequacy_results`` knows it by.
ADEQUACY_ALIASES: dict[str, str] = {"frontier": "adequacy_frontier"}


def adequacy_section_id(study_id: str) -> str:
    """The evidence key of an adequacy surface (``SECTION_ORDER`` name)."""
    return ADEQUACY_ALIASES.get(study_id, study_id)


ADEQUACY_SECTION_IDS: tuple[str, ...] = tuple(
    adequacy_section_id(s) for s in SECTION_ORDER)

SectionKind = Literal["eh", "adequacy", "worksheet"]
SectionStatus = Literal["ok", "not_established", "skipped"]

SECTION_TITLES: dict[str, str] = {
    # Energy Hub (REPORT_SECTIONS order).
    "target": "Availability target and achieved adequacy",
    "certification": "Certification (sequential Monte Carlo)",
    "cost": "Cost at target",
    "frontier": "Cost-vs-availability frontier",
    "sizing": "Sizing of the least-cost plan",
    "redundancy": "Redundancy options",
    "levers": "Optimisation levers",
    "dtc": "Critical-load (DtC) stress",
    "fmea_top": "Residual failure modes (FMEA top-N)",
    "tea": "Techno-economic summary (LCOE / LCOH)",
    "gates": "Dynamics gates",
    "multi_energy": "Multi-energy adequacy",
    # Adequacy surfaces (SECTION_ORDER).
    "adequacy": "Enforced reliability target (LP proxy)",
    "reserve_margin": "Firm-capacity reserve margin",
    "mc": "Sequential Monte-Carlo LOLE/EUE",
    "copt": "COPT screening",
    "adequacy_frontier": "Cost-of-reliability frontier (adequacy campaign)",
    "coupling_loop": "Coupling loop (ENS-cap lever)",
    "margin_loop": "Margin loop (firm-capacity lever)",
    "fmea_sweep": "Contingency sweep (FMEA classes B/C)",
    # Worksheet.
    WORKSHEET_SECTION: "Expert-entered failure modes (class D)",
}

# Why a surface can be empty — the sentences ``get_adequacy_results`` gives
# (``services.chat_tools._ADEQUACY_NO_DATA_HINTS``), copied rather than
# imported: that module pulls FastAPI and the network singleton at import,
# and this one must stay importable without either.
NO_DATA_HINTS: dict[str, str] = {
    "copt": (
        "the COPT engine found no dispatchable fleet to convolve — add "
        "conventional generators, or check that outage rates are set"
    ),
    "fmea_sweep": "no class-B/C contingency sweep has run in this session",
    "frontier": "no cost-vs-availability study has run in this session",
    "mc": "no sequential Monte-Carlo study has run in this session",
    "coupling_loop": "no coupling loop has run in this session",
    "margin_loop": "no margin loop has run in this session",
    "adequacy": (
        "nothing has been solved, or the last solve ran without a "
        "reliability target"
    ),
    "reserve_margin": (
        "nothing has been solved, the last solve set no reserve margin, or "
        "it produced no dispatch to judge one against"
    ),
    "eh_reference_design": (
        "no Energy Hub ReferenceDesignReport has been stored — run "
        "run_eh_study first"
    ),
    WORKSHEET_SECTION: "no expert (class-D) rows have been entered in the "
                       "FMEA worksheet",
}

NOTHING_MEASURED_DISCLOSURE = (
    "Nothing was measured: no reliability surface and no Energy Hub "
    "reference design were found in this session, so every section of this "
    "report is not established and no number in it may be quoted."
)

HEADLINE_KEYS: tuple[str, ...] = (
    "archetype", "ens_cap_permyriad", "achieved_ens_permyriad",
    "achieved_shed_hours", "mc_lole_h", "cost_at_target_eur", "period_basis",
    "excludes_shed_cost", "tea", "gates", "pack_hash", "assumptions_hash",
)

# tea / gates are blocks on the report even when their section is empty;
# the WP0 writer treats a present block as established evidence, so do we.
_BLOCK_SECTIONS = ("tea", "gates")

_MAX_CELL_CHARS = 200


# ── models ───────────────────────────────────────────────────────────────


class EvidenceTable(BaseModel):
    """One code-rendered table; every cell already a string."""

    table_id: str
    columns: list[str]
    rows: list[list[str]]
    caption: str | None = None
    source_path: str


class NumberFact(BaseModel):
    """A numeric leaf the prose may quote, with where it came from."""

    path: str
    value: float | int | bool
    unit: str | None = None


class EvidenceSection(BaseModel):
    section_id: str
    title: str
    kind: SectionKind
    status: SectionStatus
    note: str | None = None
    engine: str | None = None
    fidelity: str | None = None
    payload: dict[str, Any] | None = None
    # Where a reader can re-read it: the adequacy study name for
    # ``get_adequacy_results``, the EH report key, or the worksheet file.
    study_id: str | None = None
    source: str | None = None


class Evidence(BaseModel):
    sections: dict[str, EvidenceSection] = Field(default_factory=dict)
    headline: dict[str, Any] = Field(default_factory=dict)
    pipeline: dict[str, Any] | None = None
    objective: str | None = None
    campaign: dict[str, Any] | None = None
    required_disclosures: list[str] = Field(default_factory=list)
    not_established: list[str] = Field(default_factory=list)
    evidence_gaps: list[dict[str, Any]] = Field(default_factory=list)
    tables: dict[str, EvidenceTable] = Field(default_factory=dict)


# ── sections ─────────────────────────────────────────────────────────────


def _provenance(payload: dict | None) -> tuple[str | None, str | None]:
    """Engine + fidelity as the payload states them (top level or result)."""
    if not isinstance(payload, dict):
        return None, None
    engine, fidelity = payload.get("engine"), payload.get("fidelity")
    result = payload.get("result")
    if isinstance(result, dict):
        engine = engine or result.get("engine")
        fidelity = fidelity or result.get("fidelity")
    return (str(engine) if engine else None,
            str(fidelity) if fidelity else None)


def _missing(section_id: str, kind: SectionKind, hint: str, *,
             study_id: str | None = None,
             source: str | None = None) -> EvidenceSection:
    return EvidenceSection(
        section_id=section_id, title=SECTION_TITLES.get(section_id, section_id),
        kind=kind, status="not_established", note=hint, study_id=study_id,
        source=source)


def _adequacy_sections(study_report: dict | None) -> dict[str, EvidenceSection]:
    rows = {}
    if isinstance(study_report, dict):
        for row in study_report.get("sections") or []:
            if isinstance(row, dict) and row.get("id"):
                rows[row["id"]] = row
    out: dict[str, EvidenceSection] = {}
    for study_id in SECTION_ORDER:
        section_id = adequacy_section_id(study_id)
        source = f"get_adequacy_results('{study_id}')"
        row = rows.get(study_id)
        hint = NO_DATA_HINTS.get(study_id, "not run in this session")
        if row is None:
            out[section_id] = _missing(section_id, "adequacy", hint,
                                       study_id=study_id, source=source)
            continue
        if row.get("status") != "ok" or not isinstance(row.get("payload"), dict):
            reason = row.get("reason") or hint
            out[section_id] = _missing(section_id, "adequacy", str(reason),
                                       study_id=study_id, source=source)
            continue
        payload = copy.deepcopy(row["payload"])
        engine, fidelity = _provenance(payload)
        note = row.get("caveat") or _SECTION_CAVEATS.get(study_id)
        if row.get("provenance_note"):
            note = f"{note}. {row['provenance_note']}" if note \
                else row["provenance_note"]
        out[section_id] = EvidenceSection(
            section_id=section_id, title=SECTION_TITLES.get(section_id, section_id),
            kind="adequacy", status="ok", note=note,
            engine=row.get("engine") or engine,
            fidelity=row.get("fidelity") or fidelity, payload=payload,
            study_id=study_id, source=source)
    return out


def _eh_sections(eh_report: dict | None) -> dict[str, EvidenceSection]:
    out: dict[str, EvidenceSection] = {}
    if not isinstance(eh_report, dict):
        for section_id in REPORT_SECTIONS:
            out[section_id] = _missing(
                section_id, "eh", NO_DATA_HINTS["eh_reference_design"],
                source=f"eh_reference_design.sections.{section_id}")
        return out
    sections = eh_report.get("sections") or {}
    completeness = eh_report.get("completeness") or {}
    for section_id in REPORT_SECTIONS:
        state = sections.get(section_id) or {}
        status = completeness.get(section_id) or state.get("status") \
            or "not_established"
        if status not in ("ok", "not_established", "skipped"):
            status = "not_established"
        payload = state.get("payload")
        block = eh_report.get(section_id) if section_id in _BLOCK_SECTIONS \
            else None
        if status == "ok" and not isinstance(payload, dict) and isinstance(
                block, dict):
            payload = block
        if status == "ok" and not isinstance(payload, dict):
            status = "not_established"
        note = state.get("note")
        if status != "ok" and not note:
            note = F.fmt_status(status)
        payload = copy.deepcopy(payload) if status == "ok" else None
        engine, fidelity = _provenance(payload)
        out[section_id] = EvidenceSection(
            section_id=section_id, title=SECTION_TITLES[section_id], kind="eh",
            status=status, note=str(note) if note else None,
            engine=engine, fidelity=fidelity, payload=payload,
            source=f"eh_reference_design.sections.{section_id}")
    return out


def _worksheet_section(worksheet: dict | None) -> EvidenceSection:
    rows = list((worksheet or {}).get("manual_rows") or [])
    source = "adequacy_worksheet.manual_rows"
    if not rows:
        return _missing(WORKSHEET_SECTION, "worksheet",
                        NO_DATA_HINTS[WORKSHEET_SECTION], source=source)
    return EvidenceSection(
        section_id=WORKSHEET_SECTION, title=SECTION_TITLES[WORKSHEET_SECTION],
        kind="worksheet", status="ok", source=source,
        note=("rows entered by the analyst in the FMEA worksheet; engine "
              "'expert', fidelity 'expert_judgement' — never an engine's "
              "provenance"),
        engine="expert", fidelity="expert_judgement",
        payload={
            "manual_rows": copy.deepcopy(rows),
            "overlays": copy.deepcopy(dict((worksheet or {}).get("overlays") or {})),
            "version": (worksheet or {}).get("version"),
        })


def _headline(eh_report: dict | None) -> dict[str, Any]:
    if not isinstance(eh_report, dict):
        return {}
    return {key: copy.deepcopy(eh_report.get(key)) for key in HEADLINE_KEYS}


# ── disclosures and the negative space ───────────────────────────────────


def _eh_disclosures(sections: dict[str, EvidenceSection],
                    headline: dict[str, Any]) -> list[str]:
    """The EH engines' own standing warnings and notes, read never asserted."""
    out: list[str] = []
    if headline.get("cost_at_target_eur") is not None:
        out.append(
            f"The cost at target ({F.fmt_number(headline['cost_at_target_eur'], unit='€')}) "
            f"is on a {F.fmt_text(headline.get('period_basis'))} basis and "
            f"excludes the cost of load shedding; do not add a VoLL term to it.")
    for section_id in REPORT_SECTIONS:
        section = sections.get(section_id)
        if section is None or section.status != "ok" or not section.payload:
            continue
        payload = section.payload
        for key in ("warning", "note", "copt_fidelity_note"):
            text = payload.get(key)
            if text:
                out.append(f"{section_id} — the engine's own standing note, "
                           f"which travels with this result: {text}")
        class_b = payload.get("class_b") if section_id == "fmea_top" else None
        if isinstance(class_b, dict) and class_b.get("status") not in (None, "run"):
            line = (f"fmea_top — the class-B Link sweep was "
                    f"{F.fmt_status(class_b.get('status'))}")
            if class_b.get("reason"):
                line += f": {class_b['reason']}"
            out.append(line + ".")
    tea = headline.get("tea")
    if isinstance(tea, dict) and tea.get("lcoh_note") \
            and tea.get("lcoh_eur_per_kg") is None:
        out.append(f"tea — {tea['lcoh_note']}")
    return out


def _not_established(sections: dict[str, EvidenceSection]) -> list[str]:
    out: list[str] = []
    for section in sections.values():
        if section.status == "ok":
            continue
        if section.kind == "adequacy":
            study_id = section.study_id or section.section_id
            question = _SECTION_QUESTION.get(study_id, section.title)
            out.append(f"{question} — not established ({study_id} "
                       f"was never run in this session: {section.note})")
        else:
            out.append(f"{section.title} — {F.fmt_status(section.status)}"
                       f" ({section.note})")
    return out


# ── tables ───────────────────────────────────────────────────────────────


def _cell(value: Any) -> str:
    """A generic payload leaf as a table cell."""
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, default=str, separators=(",", ":"))
        if len(text) > _MAX_CELL_CHARS:
            text = text[:_MAX_CELL_CHARS - 1] + "…"
        return text
    if value is None or isinstance(value, (bool, int, float)):
        return F.fmt_number(value)
    return F.fmt_text(value)


def _kv(table_id: str, rows: list[tuple[str, str]], caption: str | None,
        source_path: str) -> EvidenceTable:
    return EvidenceTable(table_id=table_id, columns=["Item", "Value"],
                         rows=[[k, v] for k, v in rows], caption=caption,
                         source_path=source_path)


def _t_headline(headline: dict[str, Any]) -> EvidenceTable:
    tea = headline.get("tea") or {}
    cost = F.fmt_number(headline.get("cost_at_target_eur"), unit="€")
    if cost != F.NOT_ESTABLISHED:
        cost += (f" ({F.fmt_text(headline.get('period_basis'))}; "
                 f"excludes load-shedding cost)")
    lcoh = F.fmt_number(tea.get("lcoh_eur_per_kg"), unit="€/kg", digits=2)
    if lcoh == F.NOT_ESTABLISHED and tea.get("lcoh_note"):
        lcoh = f"{F.NOT_ESTABLISHED} — {tea['lcoh_note']}"
    rows = [
        ("Archetype", F.fmt_text(headline.get("archetype"))),
        ("ENS cap (‱ of demand)", F.fmt_number(headline.get("ens_cap_permyriad"), digits=2)),
        ("Achieved ENS (‱ of demand)", F.fmt_number(headline.get("achieved_ens_permyriad"), digits=2)),
        ("Achieved shed hours (h/yr)", F.fmt_number(headline.get("achieved_shed_hours"), digits=1)),
        ("MC LOLE (h/yr)", F.fmt_number(headline.get("mc_lole_h"), unit="h/yr", digits=2)),
        ("Cost at target", cost),
        ("LCOE", F.fmt_number(tea.get("lcoe_eur_per_mwh"), unit="€/MWh", digits=1)),
        ("LCOH", lcoh),
        ("Pack hash", F.fmt_text(headline.get("pack_hash"))),
        ("Assumptions hash", F.fmt_text(headline.get("assumptions_hash"))),
    ]
    return _kv("headline", rows, "Headline results of the reference design",
               "/headline")


def _t_completeness(sections: dict[str, EvidenceSection]) -> EvidenceTable:
    rows = [[sections[name].title, F.fmt_status(sections[name].status)]
            for name in REPORT_SECTIONS]
    return EvidenceTable(
        table_id="completeness", columns=["Section", "Status"], rows=rows,
        caption="What this study established, and what it did not",
        source_path="/sections")


def _t_adequacy_sections(sections: dict[str, EvidenceSection]) -> EvidenceTable:
    rows = []
    for name in SECTION_ORDER:
        s = sections[adequacy_section_id(name)]
        rows.append([
            name, _SECTION_QUESTION.get(name, s.title), F.fmt_status(s.status),
            F.fmt_text(s.engine), F.fmt_text(s.fidelity), F.fmt_text(s.note),
        ])
    return EvidenceTable(
        table_id="adequacy_sections",
        columns=["Section", "Question", "Status", "Engine", "Fidelity",
                 "Caveat / reason"],
        rows=rows,
        caption="Reliability surfaces of this session, each with its engine "
                "and fidelity — a screening convolution, an LP proxy and a "
                "sampler are not interchangeable",
        source_path="/sections")


def _t_certification(payload: dict) -> EvidenceTable:
    rows = [
        ("Metric", F.fmt_text(payload.get("metric"))),
        ("Target LOLE", F.fmt_number(payload.get("target_lole_h"), unit="h/yr", digits=2)),
        ("MC LOLE", F.fmt_number(payload.get("mc_lole_h"), unit="h/yr", digits=2)),
        ("LOLE interval", F.fmt_ci(payload.get("lole_ci"), unit="h/yr")),
        ("EUE", F.fmt_number(payload.get("eue_mwh"), unit="MWh", digits=1)),
        ("EUE interval", F.fmt_ci(payload.get("eue_ci"), unit="MWh", digits=1)),
        ("Draws (sampled / requested)",
         f"{F.fmt_number(payload.get('n_samples'))} / "
         f"{F.fmt_number(payload.get('draws_requested'))}"),
        ("Converged", F.fmt_number(payload.get("converged"))),
        ("ENS target met by the plan", F.fmt_number(payload.get("ens_met"))),
        ("Verdict", F.fmt_text(payload.get("verdict"))),
        ("Engine / fidelity",
         f"{F.fmt_text(payload.get('engine'))} / {F.fmt_text(payload.get('fidelity'))}"),
    ]
    return _kv("certification", rows,
               "MC LOLE certification of the fixed plan (sampled: the "
               "interval travels with the mean)",
               "/sections/certification/payload")


def _t_frontier(payload: dict) -> EvidenceTable:
    knee = payload.get("knee_index")
    rows = []
    for i, pt in enumerate(payload.get("points") or []):
        pt = pt if isinstance(pt, dict) else {}
        point = pt.get("point") or {}
        status = F.fmt_text(pt.get("status"))
        if knee is not None and i == knee:
            status += " (knee)"
        rows.append([
            F.fmt_number(pt.get("target_permyriad"), digits=2),
            status,
            F.fmt_number(point.get("total_system_cost_eur"), unit="€"),
            F.fmt_number(point.get("achieved_ens_mwh")),
            F.fmt_number(point.get("achieved_shed_hours"), digits=1),
        ])
    return EvidenceTable(
        table_id="frontier",
        columns=["Target (‱)", "Status", "Total system cost", "ENS (MWh)",
                 "Shed hours (h/yr)"],
        rows=rows,
        caption=(f"Cost of reliability, one full expansion solve per target; "
                 f"{F.fmt_text(payload.get('period_basis'))}; excludes "
                 f"load-shedding cost; VoLL "
                 f"{F.fmt_number(payload.get('voll_eur_per_mwh'), unit='€/MWh')}"),
        source_path="/sections/frontier/payload/points")


def _t_sizing(payload: dict) -> EvidenceTable:
    by_carrier = payload.get("by_carrier") or {}
    rows = [[str(k), F.fmt_number(v, unit="MW", digits=1)]
            for k, v in by_carrier.items()]
    rows.append(["Total", F.fmt_number(payload.get("total_p_nom_mw"),
                                       unit="MW", digits=1)])
    return EvidenceTable(
        table_id="sizing", columns=["Carrier", "Installed capacity"],
        rows=rows, caption="Installed capacity of the least-cost plan by carrier",
        source_path="/sections/sizing/payload")


def _t_fmea_top(payload: dict) -> EvidenceTable:
    rows = []
    for r in payload.get("top") or []:
        r = r if isinstance(r, dict) else {}
        rows.append([
            F.fmt_number(r.get("rank")),
            F.fmt_text(r.get("failure_class")),
            F.fmt_text(r.get("component_class")),
            F.fmt_text(r.get("name")),
            F.fmt_number(r.get("occurrence_per_year"), unit="/yr", digits=2),
            F.fmt_number(r.get("severity_eur"), unit="€"),
            F.fmt_number(r.get("criticality_eur_per_year"), unit="€/yr"),
            F.fmt_number(r.get("delta_eue_mwh"), digits=2),
            f"{F.fmt_text(r.get('engine'))} / {F.fmt_text(r.get('fidelity'))}",
        ])
    return EvidenceTable(
        table_id="fmea_top",
        columns=["Rank", "Class", "Component", "Name", "Occurrence",
                 "Severity", "Criticality", "ΔEUE (MWh)", "Engine / fidelity"],
        rows=rows,
        caption=(f"Ranked by criticality (€/yr) then mode id; classes "
                 f"included: {F.fmt_text(payload.get('classes_included'))}; "
                 f"{F.fmt_number(payload.get('n_total_modes'))} modes in "
                 f"total; VoLL "
                 f"{F.fmt_number(payload.get('voll_eur_per_mwh'), unit='€/MWh')}"),
        source_path="/sections/fmea_top/payload/top")


def _t_expert_rows(payload: dict) -> EvidenceTable:
    overlays = payload.get("overlays") or {}
    rows = []
    for r in payload.get("manual_rows") or []:
        r = r if isinstance(r, dict) else {}
        overlay = overlays.get(r.get("mode_id")) or {}
        rows.append([
            F.fmt_text(r.get("component_class")),
            F.fmt_text(r.get("name")),
            F.fmt_number(r.get("occurrence_per_year"), unit="/yr", digits=2),
            F.fmt_number(r.get("severity_eur"), unit="€"),
            F.fmt_number(r.get("criticality_eur_per_year"), unit="€/yr"),
            F.fmt_text(r.get("rate_source")),
            F.fmt_text(r.get("mitigability") or overlay.get("mitigability")),
        ])
    return EvidenceTable(
        table_id=WORKSHEET_SECTION,
        columns=["Component", "Name", "Occurrence", "Severity", "Criticality",
                 "Rate source", "Mitigability"],
        rows=rows,
        caption="Rows entered by the analyst in the FMEA worksheet (engine: "
                "expert; fidelity: expert judgement)",
        source_path=f"/sections/{WORKSHEET_SECTION}/payload/manual_rows")


def _t_tea(tea: dict) -> EvidenceTable:
    rows = [
        ("LCOE", F.fmt_number(tea.get("lcoe_eur_per_mwh"), unit="€/MWh", digits=1)),
        ("LCOH", F.fmt_number(tea.get("lcoh_eur_per_kg"), unit="€/kg", digits=2)),
        ("LCOH status", F.fmt_status(tea.get("lcoh_status"))),
        ("Notes", F.fmt_text(tea.get("notes"))),
    ]
    return _kv("tea", rows, "Techno-economic summary", "/headline/tea")


def _t_gates(gates: dict) -> EvidenceTable:
    rows = [
        ("Short-circuit ratio gate", F.fmt_text(gates.get("scr"))),
        ("EMT study recommended", F.fmt_number(gates.get("emt_recommended"))),
    ]
    return _kv("gates", rows, "Dynamics gates", "/headline/gates")


def _t_pipeline(pipeline: dict) -> EvidenceTable:
    rows = [[
        F.fmt_text(s.get("stage")),
        F.fmt_status(s.get("status")),
        F.fmt_number(s.get("solves_charged")),
        F.fmt_text(s.get("note")) if s.get("note") else "",
    ] for s in pipeline.get("stages") or [] if isinstance(s, dict)]
    caption = (f"Solves consumed {F.fmt_number(pipeline.get('solves_consumed'))} "
               f"of a budget of {F.fmt_number(pipeline.get('budget_solves'))}")
    if pipeline.get("aborted"):
        caption += "; the study was aborted"
    return EvidenceTable(
        table_id="pipeline", columns=["Stage", "Status", "Solves", "Note"],
        rows=rows, caption=caption, source_path="/pipeline/stages")


def _t_generic(section_id: str, payload: dict) -> EvidenceTable:
    rows = [(str(k), _cell(v)) for k, v in payload.items()]
    return _kv(section_id, rows, SECTION_TITLES.get(section_id, section_id),
               f"/sections/{section_id}/payload")


def _tables(sections: dict[str, EvidenceSection], headline: dict[str, Any],
            pipeline: dict | None, *, has_eh: bool,
            has_study: bool) -> dict[str, EvidenceTable]:
    out: dict[str, EvidenceTable] = {}
    if has_eh:
        out["headline"] = _t_headline(headline)
        out["completeness"] = _t_completeness(sections)
    if has_study:
        out["adequacy_sections"] = _t_adequacy_sections(sections)

    def ok(section_id: str) -> dict | None:
        s = sections.get(section_id)
        return s.payload if s is not None and s.status == "ok" and s.payload \
            is not None else None

    if (p := ok("certification")) is not None:
        out["certification"] = _t_certification(p)
    if (p := ok("frontier")) is not None:
        out["frontier"] = _t_frontier(p)
    if (p := ok("sizing")) is not None:
        out["sizing"] = _t_sizing(p)
    if (p := ok("fmea_top")) is not None:
        out["fmea_top"] = _t_fmea_top(p)
    if (p := ok(WORKSHEET_SECTION)) is not None:
        out[WORKSHEET_SECTION] = _t_expert_rows(p)
    for section_id in ("redundancy", "levers", "dtc"):
        if (p := ok(section_id)) is not None:
            out[section_id] = _t_generic(section_id, p)
    tea = headline.get("tea")
    if isinstance(tea, dict) and (tea or ok("tea") is not None):
        out["tea"] = _t_tea(tea)
    elif (p := ok("tea")) is not None:
        out["tea"] = _t_tea(p)
    gates = headline.get("gates")
    if isinstance(gates, dict) and (gates or ok("gates") is not None):
        out["gates"] = _t_gates(gates)
    elif (p := ok("gates")) is not None:
        out["gates"] = _t_gates(p)
    if isinstance(pipeline, dict) and pipeline.get("stages"):
        out["pipeline"] = _t_pipeline(pipeline)
    return out


# ── entry points ─────────────────────────────────────────────────────────


def collect_evidence(*, study_report: dict | None, eh_report: dict | None,
                     worksheet: dict | None) -> Evidence:
    """
    Union the adequacy write-up, the EH reference design and the worksheet.

    Pure: the caller passes the dicts (``build_study_report(...)``,
    ``export_reference_design(report)`` or a ``ReferenceDesignReport``
    dump, ``load_worksheet(project_dir)``) and gets back one ``Evidence``
    with every known section present, established or stated as not.
    """
    has_study = isinstance(study_report, dict)
    has_eh = isinstance(eh_report, dict)

    sections: dict[str, EvidenceSection] = {}
    sections.update(_eh_sections(eh_report))
    sections.update(_adequacy_sections(study_report))
    sections[WORKSHEET_SECTION] = _worksheet_section(worksheet)

    headline = _headline(eh_report)
    pipeline = copy.deepcopy(eh_report.get("pipeline")) if has_eh else None

    disclosures: list[str] = []
    if has_study:
        disclosures += [str(d) for d in study_report.get("required_disclosures") or []]
    disclosures += _eh_disclosures(sections, headline)
    if not any(s.status == "ok" for s in sections.values()
               if s.kind != "worksheet"):
        disclosures.insert(0, NOTHING_MEASURED_DISCLOSURE)

    not_established: list[str] = []
    if has_study:
        not_established += [str(x) for x in study_report.get("not_established") or []]
    for line in _not_established(sections):
        if line not in not_established:
            not_established.append(line)

    gaps = copy.deepcopy(study_report.get("evidence_gaps") or []) \
        if has_study else []

    return Evidence(
        sections=sections,
        headline=headline,
        pipeline=pipeline,
        objective=study_report.get("objective") if has_study else None,
        campaign=copy.deepcopy(study_report.get("campaign")) if has_study else None,
        required_disclosures=disclosures,
        not_established=not_established,
        evidence_gaps=[g for g in gaps if isinstance(g, dict)],
        tables=_tables(sections, headline, pipeline, has_eh=has_eh,
                       has_study=has_study),
    )


def _canonical(value: Any) -> Any:
    """Floats to six significant digits, NaN/inf to null, keys sorted."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if value == 0.0:
            return 0.0
        return float(f"{value:.6g}")
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(
            value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return str(value)


def evidence_hash(evidence: Evidence) -> str:
    """sha256 of the canonical JSON — what a report compares to know it is stale."""
    payload = _canonical(evidence.model_dump(mode="python"))
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── numbers ──────────────────────────────────────────────────────────────

_COUNT_KEYS = {"rank", "top_n"}
_COUNT_PREFIXES = ("n_", "draws_")
_COUNT_SUFFIXES = ("_count",)
_UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_eur_per_mwh", "€/MWh"),
    ("_eur_per_kg", "€/kg"),
    ("_eur_per_year", "€/yr"),
    ("_mwh_per_year", "MWh/yr"),
    ("_per_year", "/yr"),
    ("_eur", "€"),
    ("_mwh", "MWh"),
    ("_permyriad", "‱"),
    ("_hours", "h"),
    ("_h", "h"),
    ("_mw", "MW"),
)


# Bare metric names whose unit the key does not spell out (``lole_ci``,
# ``eue_ci`` bound ``mc_lole_h`` / ``eue_mwh``).
_METRIC_UNITS: dict[str, str] = {"lole": "h", "eue": "MWh"}


def unit_hint(key: str) -> str | None:
    """A unit guessed from the key name; ``count`` for ranks and counts."""
    k = key.lower()
    if k in _COUNT_KEYS or k.startswith(_COUNT_PREFIXES) \
            or k.endswith(_COUNT_SUFFIXES):
        return "count"
    if k.endswith("_ci"):
        k = k[:-3]
    for suffix, unit in _UNIT_SUFFIXES:
        if k.endswith(suffix):
            return unit
    for name, unit in _METRIC_UNITS.items():
        if k == name or k.endswith("_" + name):
            return unit
    return None


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _walk(value: Any, path: str, key: str, out: list[NumberFact]) -> None:
    if isinstance(value, bool):
        out.append(NumberFact(path=path, value=value, unit="count"))
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return
        out.append(NumberFact(path=path, value=value, unit=unit_hint(key)))
    elif isinstance(value, dict):
        for k, v in value.items():
            _walk(v, f"{path}/{_escape(str(k))}", str(k), out)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _walk(v, f"{path}/{i}", key, out)


def flatten_numbers(evidence: Evidence) -> list[NumberFact]:
    """Every numeric leaf of the headline and the section payloads, with its path."""
    out: list[NumberFact] = []
    _walk(evidence.headline, "/headline", "", out)
    for section_id, section in evidence.sections.items():
        if section.payload is not None:
            _walk(section.payload, f"/sections/{_escape(section_id)}/payload",
                  "", out)
    return out


# ── slices ───────────────────────────────────────────────────────────────


def _mentions(text: str, section: EvidenceSection) -> bool:
    lowered = text.lower()
    names = {section.section_id, section.study_id or section.section_id}
    needles = {section.title.lower()}
    for name in names:
        needles.add(name.lower())
        needles.add(name.replace("_", " ").lower())
    return any(n in lowered for n in needles)


def slice_for(evidence: Evidence, section_id: str) -> dict:
    """
    What one section's prompt needs and nothing more: its own payload, note
    and status, the headline, the disclosures and not-established lines
    that mention it, and the ids of the tables built from it.
    """
    section = evidence.sections.get(section_id)
    if section is None:
        section = _missing(section_id, "eh", "unknown section")
    headline = {k: copy.deepcopy(v) for k, v in evidence.headline.items()
                if k not in _BLOCK_SECTIONS or k == section_id}
    own_path = f"/sections/{section_id}"
    table_ids = [t.table_id for t in evidence.tables.values()
                 if t.source_path == own_path
                 or t.source_path.startswith(own_path + "/")
                 or t.source_path == f"/headline/{section_id}"]
    return {
        "section_id": section.section_id,
        "title": section.title,
        "kind": section.kind,
        "status": section.status,
        "note": section.note,
        "engine": section.engine,
        "fidelity": section.fidelity,
        "payload": copy.deepcopy(section.payload),
        "headline": headline,
        "required_disclosures": [d for d in evidence.required_disclosures
                                 if _mentions(d, section)],
        "not_established": [d for d in evidence.not_established
                            if _mentions(d, section)],
        "table_ids": table_ids,
    }
