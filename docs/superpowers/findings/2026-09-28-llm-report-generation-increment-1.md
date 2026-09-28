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

**Merged (2026-09-28):** WP4 `2fa85a6`, WP2 `6406427`, WP1 `6703060`,
phase-0 gate `3492795`, WP5 `e279f43`. **Phase 1 gate: GREEN.**

| Tier | Command | Result |
|---|---|---|
| Unit | `tests/test_report_model.py` 8, `test_report_store.py` 33, `test_report_routes.py` 28, `test_report_evidence.py` 18, `test_report_figures.py` 14, `test_report_docx_writer.py` 25, `test_report_assemble.py` 14, `test_chat_report_export_tools.py` 5, + parity/manifest/packaging/bundle guards | 185 passed (one run, after the WP5 merge) |
| Regression | chunk 1 (`tests/test_chat_*.py` + manifest + packaging) | exit 0 |
| Regression | chunk 2 (`tests/test_upload*.py tests/test_desktop*.py tests/test_project*.py tests/test_energy_hub_*.py tests/test_adequacy_*.py`) | exit 0 |
| Regression | remainder, 180 files in four groups of 43–47 run in parallel (`--durations=15`; slowest single test 18.7 s, `test_solve_queue.py::test_abort_running_solve_is_fast_and_next_job_starts`) | exit 0 ×4 |
| Integration | `test_report_routes.py` (TestClient: list/get/versions/figures/delete, `%2e%2e` ids, other-org 404, lock refusal, save-as / copy / scenario / snapshot / bundle carry `reports/`; POST evidence_only → v1 + figure files, `generated` → 400, lock refusal, export → docx chip served by the blob route, unknown id → 404) | 28 passed (counted above) |
| End-to-end QA | `tests/qa_reports_phase0.py` (real study → chat tool → blob → python-docx) | 18/18 PASS |
| End-to-end QA | `tests/qa_reports_phase1.py` (real study → evidence-only report → list/get/figure → export → bookmarks → save-as carries it → delete) | 38/38 PASS |

**Recorded corrections.** The whole backend suite cannot finish inside the
cloud container's 10-minute per-command ceiling and the "everything else"
chunk cannot either at half size; four groups of ≤47 files each finish in
well under it and together are the suite minus `slow`. The plan's chunk
recipe now says so. Agent worktrees are created from `master`, not the
plan branch: WP1 and WP4 fast-forwarded first; WP2 could not (its
`reset`/`ff-merge` were refused by the permission classifier) and shipped a
two-file branch on `master`'s base that merged cleanly — later agents are
told to fast-forward first. WP5 folded WP0's per-section renderers into one
writer path (`collect_evidence` → `evidence_only_document` →
`render_document_docx`), so an unestablished section now reads
"Not established: this section was …" — two assertions reworded, nothing
weakened. Section bookmarks are `sec:<id>`; Word's dialog hides names with a
colon but the OOXML is valid and python-docx reads them, so the increment-3
round trip must match on the XML, not the dialog.

**Owed from a workstation (unchanged).** Desktop download-and-open-in-Word;
page-layout check of the generated `.docx`.

## §2 — Generation (WP3, WP6)

_(pending)_

## §3 — Reading and export in the app (WP7)

_(pending)_
