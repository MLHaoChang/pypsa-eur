# LLM-assisted study reports — increment 1 findings

**Plan:** `docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md`
**Assessment:** `docs/superpowers/assessments/2026-09-28-llm-report-generation-feasibility.md`
**Branch:** `claude/fmea-llm-reporting-feasibility-jtm6w1`

One section per phase, filled in when the phase's four-tier gate (unit,
regression, integration, end-to-end QA) is green. Counts are what the
command printed, not estimates.

## §0 — Spike (WP0), 2026-09-28

**Delivered.** `services/reports/{formatting,default_template,figures,docx_writer}.py`,
chat tool `export_eh_report_docx`, `python-docx==1.2.0` pinned for the desktop
build, spec collects its template data. Commit `93fe9f5`.

| Tier | Command | Result |
|---|---|---|
| Unit | `pytest tests/test_report_docx_writer.py tests/test_report_figures.py tests/test_chat_report_export_tools.py` | 23 passed |
| Regression | chat chunk (`tests/test_chat_*.py` + manifest + packaging) | passed, 0 failures (2 skips pre-existing) |
| Regression | EH chunk (`tests/test_energy_hub_*.py tests/test_report_*.py tests/test_study_report.py tests/test_adequacy_worksheet.py`) | passed |
| Regression | projects/desktop/uploads chunk | passed (1 skip pre-existing) |
| Integration | — (the tool is an in-process `_SERVICE_CALL`; its dispatch is covered by the parity tests) | n/a |
| End-to-end QA | `tests/qa_reports_phase0.py` (real EH study over HTTP → tool → blob → python-docx re-open) | _(run recorded under §1)_ |

**Visual check.** The FMEA Pareto PNG was rendered and inspected: one axis,
one hue, ink labels, grouped ticks, cumulative share annotated in ink.

**Owed from a workstation.** Open the downloaded `.docx` from the macOS
desktop app in Word; a page-layout check (LibreOffice in the cloud container
cannot load any `.docx`, a plain python-docx control file fails identically).

## §1 — Foundation (WP1, WP2, WP4, WP5)

_(pending)_

## §2 — Generation (WP3, WP6)

_(pending)_

## §3 — Reading and export in the app (WP7)

_(pending)_
