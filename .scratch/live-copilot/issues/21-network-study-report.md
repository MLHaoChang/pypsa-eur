<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-21 — "create a report" for a network study

Status: ready-for-agent. Owner decision Q6 = A (2026-10-19): add
network-study chapters to the existing report pipeline, built in LC-06 after
LC-05 (after LC-20, whose findings feed the plausibility chapter). This is new
application scope; humans get the chapters in the Reports panel too.
Type: task
Baseline: master `ae95a94a866730c0fe67fad37e238dc281786bd6`
Dependencies: LC-03 adapters (chapter facts), LC-18 (sensitivity chapter),
LC-20 (plausibility chapter, optional)
Catalogue: [feature catalogue](../feature-catalogue.md); journeys OJ-35, OJ-36, OJ-38

## Problem

Report generation is strong but covers only some studies. The evidence is
the Energy Hub reference design, the adequacy surfaces, the FMEA worksheet,
the investment case and the island study (`services/reports/evidence.py:69`,
`services/reports/chapters.py`). For any other project
`POST /reports/generate` answers 400 `no_evidence`
(`routers/report_jobs.py:154-161`). So for the owner's core grid study
(dispatch, capacity expansion, load flow, prices, sensitivities) neither the
human nor the copilot can make a report today. The decision study has its
own report (`generate_study_report`).

Chat can already: `generate_report`, `get_report_status`,
`regenerate_report_section`, templates and mapping, `export_report_docx`/
`pdf`, round trip and diff (`harness/catalogue.py:2249` and neighbours).

## Options (owner question Q6; A chosen)

- **A (recommended):** add network-study chapters to the existing evidence
  pipeline: model summary (LC-15 read), headline results, capacity, dispatch,
  load flow with its proxy caveat, prices and costs, emissions, sensitivity
  comparison (LC-18), plausibility findings (LC-20). Same number audit,
  template binding, round trip and export. Humans get it in the Reports
  panel too.
- **B:** a chat-only "results pack": `export_run_evidence` +
  `export_to_excel` + `create_chart` bundled with `build_delivery`, plus
  `export_chat_summary`. No prose report.
- **C:** defer; the copilot says reports exist for Energy Hub, adequacy,
  investment and island studies only.

## Scope (option A, chosen)

1. One chapter per adapter, each built from the same native facts the tab
   and chat use, with `not_established` when its source is missing.
2. A sensitivity chapter: base + cases table from LC-18, declared changes.
3. Chat: "create a report" → confirm sections and template (Choice) →
   `generate_report` card (spends model tokens) → progress → open Reports
   panel at the new report → offer DOCX/PDF export.

## Done when

OJ-35 passes in Tier A on `oj-grid` with every number in the document
traced by the existing number audit; OJ-36 (Energy Hub) keeps passing.
