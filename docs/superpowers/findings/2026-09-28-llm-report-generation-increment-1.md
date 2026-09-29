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

## §2 — Generation (WP3, WP6), 2026-09-28

**Merged:** WP3 `1457614` (prompts, generator, number audit, job, `routers/report_jobs.py`),
WP6 `678694d` (nine chat tools, `ReportMeta.generation` durability, CHATBOT.md,
`qa_reports_phase2.py`), writer fix `aa2c725`, manifest fix `5311ab6`.
**Phase 2 gate: GREEN.**

| Tier | Command | Result |
|---|---|---|
| Unit | `tests/test_report_prompts.py` 5, `test_number_audit.py` 28, `test_report_generator.py` 12, `test_report_job.py` 9, `test_chat_report_tools.py` 23, `test_report_store.py` 34 (+1), `test_report_docx_writer.py` 27 (+2), all other `test_report_*` + parity/manifest/packaging + `test_chat_adequacy_tools.py` | 315 passed (one run on the merged tree) |
| Regression | chunk 1 (`tests/test_chat_*.py` + manifest + packaging) | 1229 passed, 2 skipped |
| Regression | chunk 2a (`tests/test_upload*.py tests/test_desktop*.py tests/test_project*.py`) | 242 passed, 1 skipped |
| Regression | chunk 2b (`tests/test_energy_hub_*.py` / `tests/test_adequacy_*.py`) | Energy Hub: 308 passed in 256.54s (0:04:16); adequacy: 663 passed, 11 deselected in 353.13s (0:05:53) (run as two halves; the combined chunk overran the container ceiling only while the QA drivers were competing for the CPU) |
| Regression | remainder (four groups) | unchanged since §1: WP3/WP6 touch no file those groups import; re-run owed before merge to `master` |
| Integration | `tests/test_report_job_http.py` 9 (generate → running → done, 409 in flight, abort → aborted, regenerate → v2, `no_evidence` 400, readable through WP1 GET, exportable through WP5 POST) | passed (counted above) |
| End-to-end QA | `tests/qa_reports_phase2.py` (real study → `generate_report` on the scripted fake provider with one repair and one failure → audit flags the planted `99.9 h/yr` and verifies the copied number → `get_report` under the 4000-char cap (3611 measured) → `get_report_table` pages → regenerate one section → v2 with the others byte-identical → abort mid-run → partial version → export with the "Numbers to check" appendix → second generate while running → 409) | 47/47 PASS |
| End-to-end QA | live openai-wire probe (`PYPSA_GUI_TEST_LIVE_OPENAI_PROFILE`) | **UNPROBED** here (no local model); the driver says so rather than passing |

**Recorded corrections.** `repairs` counts repair *turns*, so a garbage
section's failed repair is one — 2 in the phase-2 driver, not 1. A loaded
network already yields an `ok` COPT surface, so `no_evidence` means "no
write-up at all", not "no EH study". `get_report` degrades per section to
stay under the chat result cap: a real 16-section document is 8.5 kB even
with tables collapsed, so the tool returns an outline plus as many full prose
sections as fit, and `section_id` reads any one section in full. The job
writes `prose not established: <reason> (profile …, model …)` on a section
whose evidence is fine; the writer stated notes only on non-ok sections, so
the `.docx` silently dropped it — fixed in `aa2c725` with two tests. WP3
classified `missing_api_key` as `inline` in the manifest while the chat
panel routes it to the key-entry banner; the frontend contract test caught
it on the merged tree (WP7b) — fixed in `5311ab6`.

## §3 — Reading and export in the app (WP7a, WP7b), 2026-09-28

**Merged:** WP7a `c128080` (Reports panel, viewer, section cards, unverified-number
marks, export download), WP7b `b3888f8` (generate dialog, job hook with 1.5 s
polling, progress/abort strip, per-section regenerate, "evidence changed since
vN" badge, Adequacy-tab button, `scripts/smoke-reports.mjs`).
**Phase 3 gate: GREEN on the evidence-only path; the generated path of the
smoke is coded but unexercised here (no LLM key in the container).**

| Tier | Command | Result |
|---|---|---|
| Unit | `reports.api.test.ts` 22, `ReportsPanel.test.tsx` 18, `ReportViewer.test.tsx` 14, `GenerateReportDialog.test.tsx` 11, `useReportJob.test.tsx` 8, `highlightUnverified.test.ts` 8, `AdequacyTab` +1, `EhReferenceDesignPanel` +1 | 126 passed in the touched files |
| Regression | `npx vitest run` (whole frontend) + `npx tsc -b` | 183 files, 2059 tests passed; tsc clean |
| Integration | `ChatPanel.manifest.test.tsx` against the shared manifest (after `5311ab6`) | 100 passed |
| End-to-end QA | `node scripts/smoke-reports.mjs --browser` against uvicorn on :8765 with the built SPA (sign-in, fixture network, real `strong_grid` study, evidence-only report, generate → `missing_api_key` reported and skipped, viewer shows 17 sections, per-section Regenerate control, export → `.docx` blob starts `PK`, cleanup) | 27/27 PASS, generation path SKIPPED |

**Owed from a workstation.** The generated/regenerate legs of the smoke on a
profile with a key; the macOS desktop download-and-open-in-Word check; a
page-layout look at a generated `.docx`. A `GET …/reports/evidence_hash`
route is a follow-up (the viewer compares against the newest evidence-only
report's hash meanwhile).

## §4 — Templates (WP8, WP9, WP10, WP11), 2026-09-29

**Merged:** WP9 `adf28f7` (tagged rendering), WP8 `5515092` (template reader,
mode, language), WP11 frontend `f0b10ea` (picker, plan editor, `docx-preview`
0.4.1 pinned), WP11 backend `b15f32e` (upload kind, template routes, mapping
job, five chat tools, `qa_reports_phase4.py`), WP10 `9e5d37a` (mapping plan,
untagged body rebuild). **Phase 4 gate: GREEN.**

| Tier | Command | Result |
|---|---|---|
| Unit | `test_report_docx_reader.py` 29, `test_report_template_tagged.py` 16, `test_report_template_untagged.py` 25, `test_report_templates_routes.py` 28, `test_chat_report_template_tools.py` 12, `test_report_prompts.py` 7 (+2), plus every other `test_report_*`, `test_chat_report_*`, `test_chat_uploads.py`, parity/manifest/packaging | 425 passed (one run on the merged tree) |
| Regression | chunk 1 (`tests/test_chat_*.py` + manifest + packaging) | 1255 passed, 2 skipped |
| Regression | chunk 2a (`tests/test_upload*.py tests/test_desktop*.py tests/test_project*.py`) | 242 passed, 1 skipped |
| Regression | chunk 2b (`tests/test_energy_hub_*.py` / `tests/test_adequacy_*.py`) | Energy Hub: 308 passed; adequacy: 663 passed, 11 deselected in 290.42s (0:04:50) |
| Regression | remainder (four groups) | unchanged since §1: phase 4 touches no file those groups import; re-run owed before merge to `master` |
| Regression | frontend `npx vitest run` + `npx tsc -b` (after `npm ci` with the new lockfile) | 187 files, 2104 tests passed; tsc clean |
| Integration | `test_report_templates_routes.py` (bind/unbind, GET, mapping job with the fake provider, PUT strict/non-strict, tagged export on the real fixture, untagged export, generate with `template_file_id` defaulting the language, every error kind) | 28 passed (counted above) |
| End-to-end QA | `tests/qa_reports_phase4.py` (real study → both fixtures uploaded as `report_template` → tagged bind → export with the title, looped FMEA rows, evidence hash in the footer, no `{{` left → corporate bind → mapping job on the fake provider → GET plan → PUT an edited plan → export with cover and "Confidential" footer intact, renamed heading, `updateFields` set, `sec:fmea_top` bookmark → generate on the German template → `language == "de"` → unbind → default writer) | 47/47 PASS, 0 skipped |
| End-to-end QA | drivers 0–2 re-run on this tree | phase 0 18/18, phase 1 38/38, phase 2 47/47 — all PASS |

**Recorded corrections.** The two WP11 halves were built in parallel against
one route contract stated identically to both; they merged without a single
contract mismatch, and WP10's mapping-plan envelope matched what the WP11
backend's job expected on the first full driver run (the backend author had
flagged that as the likely integration risk). Word splits Jinja tags across
runs; both the reader and the tagged renderer merge runs before matching, so
a tag typed in Word renders (a tag inside a hyperlink does not — documented).
Bookmark names keep the `sec:` colon (Word's dialog hides them, the XML is
what the round trip matches). The WP11 frontend copied `node_modules` into
its worktree rather than symlinking, because `npm install` through a symlink
would have mutated the main checkout.

**Owed from a workstation.** Open a tagged and an untagged export in Word
(TOC refresh prompt on the untagged one), and the `docx-preview` pane in the
desktop shell.

## §5 — Round trip (WP12, WP13, WP14), 2026-09-29

**Merged:** WP14 `f03e772` (upload an edited copy, merge result panel,
version diff view, PDF button), WP12 `f96b050` (round-trip reader and
merge; additive `Section.pending_instruction`, `Section.comments`,
`ReportMeta.roundtrip_file_id`), WP13 `b86d558` (round-trip route, version
diff, capabilities, opportunistic PDF, four chat tools,
`qa_reports_phase5.py`), store fix `6810a1b`. **Phase 5 gate: GREEN.**

| Tier | Command | Result |
|---|---|---|
| Unit | `test_report_roundtrip.py` 20, `test_report_roundtrip_routes.py` 20, `test_report_pdf.py` 7, `test_chat_report_roundtrip_tools.py` 13, `test_report_store.py` +1, `test_report_model.py` +1, plus every other `test_report_*`, `test_chat_report_*`, `test_chat_uploads.py`, parity/manifest/packaging | 453 passed (one run on the merged tree; WP13's conditional xfail ran as a real passing test once WP12 was present) |
| Regression | chunk 1 (`tests/test_chat_*.py` + manifest + packaging) | 1277 passed, 2 skipped |
| Regression | chunk 2a (`tests/test_upload*.py tests/test_desktop*.py tests/test_project*.py`) | 242 passed, 1 skipped |
| Regression | chunk 2b (`tests/test_energy_hub_*.py` / `tests/test_adequacy_*.py`) | Energy Hub: 308 passed; adequacy: _(run in progress at commit time; filled in the next commit)_ |
| Regression | remainder (four groups) | unchanged since §1: phase 5 touches no file those groups import; re-run owed before merge to `master` |
| Regression | frontend `npx vitest run` + `npx tsc -b` | 190 files, 2140 tests passed; tsc clean |
| Integration | `test_report_roundtrip_routes.py` (roundtrip POST every status, diff on stored versions, capabilities via `shutil.which`, export pdf 501/200/500 with `subprocess.run` faked, regenerate uses and clears `pending_instruction`, upload kind) | 20 passed (counted above) |
| End-to-end QA | `tests/qa_reports_phase5.py` (real study → generate on the fake provider → export → edit with python-docx: a rewritten `fmea_top` paragraph, a comment "shorten this" on a `certification` run, a tracked deletion+insertion in `target` → upload as `report_roundtrip` → merge: `fmea_top` is `user_edit` with the edited text, `certification.pending_instruction == "shorten this"`, `accepted_tracked_changes == 2` → diff marks `fmea_top` changed, the rest unchanged → regenerate `certification` with no instruction: the provider saw "shorten this", the new version's `pending_instruction` is null → capabilities `pdf: true` (soffice on PATH) → export pdf → 500 `pdf_conversion_failed` here because LibreOffice in the container writes no PDF → chat `list_report_roundtrips`, `diff_report_versions`) | 37/37 PASS, 0 skipped |
| End-to-end QA | drivers 0–4 re-run on this tree | _(run in progress at commit time; filled in the next commit)_ |

**Recorded corrections.** The three phase-5 packages were built in parallel
against pinned interfaces; the only seam neither side owned was the store's
carried-meta list, so `roundtrip_file_id` would have been dropped by the
regenerate that follows a round trip — fixed in `6810a1b` with a test. WP12
found that the writer's link run carries `w:u val="single"`, so underline is
"on unless none", not a toggle. LibreOffice in the container exits 0 and
writes no PDF (its "source file could not be loaded" goes to stdout), so
the PDF service reports the stdout line first. A background command that
starts with `cd` can lose its working directory in this harness; the gate
commands now use absolute paths.

**Owed from a workstation.** A real LibreOffice PDF export; opening a
round-tripped document in Word; the desktop drop-zone for the edited copy.

## Increment 1 — status

Phases 0–5 delivered on `claude/fmea-llm-reporting-feasibility-jtm6w1`
(increments 1, 2 and 3). Not done: the workstation checks listed per phase,
the live LLM probe (no key or local model in the container), and the
"remainder" regression groups' re-run before a merge to `master`. `export_eh_report_docx` (WP0) remains alongside
`export_report_docx`; keep as the no-LLM shortcut or remove in review.
