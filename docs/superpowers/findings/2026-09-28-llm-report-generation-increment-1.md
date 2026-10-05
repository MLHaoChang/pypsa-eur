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
| Regression | chunk 2b (`tests/test_energy_hub_*.py` / `tests/test_adequacy_*.py`) | Energy Hub: 308 passed; adequacy: 663 passed, 11 deselected in 304.03s (0:05:04) |
| Regression | remainder (four groups) | unchanged since §1: phase 5 touches no file those groups import; re-run owed before merge to `master` |
| Regression | frontend `npx vitest run` + `npx tsc -b` | 190 files, 2140 tests passed; tsc clean |
| Integration | `test_report_roundtrip_routes.py` (roundtrip POST every status, diff on stored versions, capabilities via `shutil.which`, export pdf 501/200/500 with `subprocess.run` faked, regenerate uses and clears `pending_instruction`, upload kind) | 20 passed (counted above) |
| End-to-end QA | `tests/qa_reports_phase5.py` (real study → generate on the fake provider → export → edit with python-docx: a rewritten `fmea_top` paragraph, a comment "shorten this" on a `certification` run, a tracked deletion+insertion in `target` → upload as `report_roundtrip` → merge: `fmea_top` is `user_edit` with the edited text, `certification.pending_instruction == "shorten this"`, `accepted_tracked_changes == 2` → diff marks `fmea_top` changed, the rest unchanged → regenerate `certification` with no instruction: the provider saw "shorten this", the new version's `pending_instruction` is null → capabilities `pdf: true` (soffice on PATH) → export pdf → 500 `pdf_conversion_failed` here because LibreOffice in the container writes no PDF → chat `list_report_roundtrips`, `diff_report_versions`) | 37/37 PASS, 0 skipped |
| End-to-end QA | drivers 0–4 re-run on this tree | phase 0 18/18, phase 1 38/38, phase 2 47/47, phase 4 47/47 — all PASS |

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

## §6 — Pre-merge gate on the `master`-merged tree, 2026-09-29

`origin/master` (29 commits ahead of the branch base) merged in at `df8642e`
(one conflict, two imports in `App.tsx`, both kept). Follow-up: the
evidence-hash route (`4555600`). Ported: `gridspine.drivers.year_study`
into the spec's hidden imports (`50f32bb`; the packaging test was red on
`master` itself since #58). PR #64.

| Tier | Command | Result |
|---|---|---|
| Unit + integration | report, chat-tool, parity, manifest, packaging files | 481 passed (after the spec fix) |
| Regression | chat chunk | 1284 passed, 2 skipped |
| Regression | uploads / desktop / projects | 242 passed, 1 skipped |
| Regression | Energy Hub | 308 passed (re-run; 2 one-time failures on the first concurrent run, both pass in isolation; `master` alone 308) |
| Regression | adequacy | 663 passed (re-run; 3 one-time failures on the first concurrent run, each passes in isolation; `master` alone 663) |
| Regression | remainder, four groups | 620, 785 (+7 skipped), 1079 (+21 skipped), 1224 — all green |
| Regression | frontend `vitest` + `tsc` | 191 files, 2168 tests passed; tsc clean |
| End-to-end QA | drivers 0, 1, 2, 4, 5 | 18/18, 38/38, 47/47, 47/47, 37/37 |

**Recorded.** The one-time failures were all source-inspection tests
(`claim wipe includes … keys`, `abortable studies match the routes`, `loops
read the condition`) during a run where three chunks started at the same
moment; the repo's conftest documents that these misreport under concurrent
activity. Each passed in isolation, on a full re-run of its chunk, and on a
worktree of `origin/master`. One frontend test
(`BottomPanel.test.tsx › select-all past the cap`) sits at the edge of the
5 s default timeout on this box under full-suite load; it passes alone and
with `--testTimeout=20000`, which the gate run used.

## §6b — Second `master` merge before the PR merge, 2026-09-29

`master` moved again after PR #64 opened (128 commits: #60, #61, #62 —
the semantic Energy Hub merge with per-class FMEA ranks, guided mode,
gridspine dynamics, macOS CI). Merged at `70fd1d2`; four conflicts
(`route_inventory_phase0.txt`, `App.tsx`, `Sidebar.tsx`, `uiStore.ts`),
each resolved as the union of both sides. The gate below ran on that tree
plus the three fixes it found.

| Tier | Command | Result |
|---|---|---|
| Unit + integration | report, chat-tool, parity, manifest, packaging files | green after the fixes below (exit 0, no FAILED) |
| Regression | chat chunk | green except `test_openpyxl_parses_uploads_with_defusedxml_in_this_environment` — the container's venv lacked the `defusedxml` pin after a restart; installed, file green |
| Regression | uploads / desktop / projects | green |
| Regression | Energy Hub | green |
| Regression | adequacy | green |
| Regression | remainder, four groups (now 209 files incl. the report tests) | green after one fixture re-record (item 4 below); groups 0, 2, 3 green first time |
| Regression | frontend `vitest` + `tsc` | 246 files, 2796 tests passed after one snapshot re-record (below); tsc clean |
| End-to-end QA | drivers 0, 1, 2, 4, 5 | 18/18, 38/38, 47/47, 47/47, 37/37 after the `fmea_top` fix |
| CI on the PR head | CodeQL, GUI frontend, Unit, Integration, Gridspine | green on `c62da83`; the GUI backend job re-runs on this push (item 4 below was red there) |

**Found by this gate, fixed on the branch.**

1. **`fmea_top` payload shape** (`e68776d`). Every driver failed at
   "fmea_top is established with at least one ranked mode — status=ok
   rows=0" and the Word export carried no top mode and no Pareto figure.
   `master`'s per-class ranks (`19e9460`) store the Class-B sweep under
   `rows` and the class-A screening under `class_a.rows`; the branch base
   merged both under `top`, which is all the evidence collector, the two
   Pareto callers and the drivers read. `evidence.fmea_top_modes` now reads
   a report as it was written (`top` if present, else class-B then class-A
   rows, ranked within class — the EH panel's order); unit test added.
2. **CodeQL `py/path-injection`, eight alerts** (`8ff90aa`, `c62da83`).
   Six were the route's `report_id`/`figure_id` returned unchanged by the
   validators and joined onto the project directory: the validators now
   re-spell an accepted id from a constant alphabet (the
   `routers/snapshots._slug` pattern; identity for every accepted id,
   pinned by a test). The remaining two were `load_report` spelling
   `v<version>.json` from the route's int: the version file is now found
   by matching directory entries, the caller's number used only in an
   equality comparison. The CodeQL check is green on `c62da83`.
3. **Expert sidebar snapshot** (`8ff90aa`). `master`'s guided-mode work
   pins the Expert sidebar DOM; this branch adds the Reports entry. The
   snapshot is re-recorded (diff: exactly that entry) and the test's
   comment says when a re-record is the right response.
4. **Pre-P25 system-prompt fixture** (this commit).
   `test_build_system_prompt_matches_the_pre_p25_snapshot[True-tools]`
   pins the system prompt so guided mode cannot touch it; this branch's
   report-tool sentence in the study guidance is the only difference
   (one paragraph). The tools fixture is re-recorded and the test's
   docstring says so; the no-tools variant was unaffected.

## §6c — Browser end-to-end on `master` after the merge, 2026-09-30

PR #64 merged at `9b65250`; `master` is at `a9a1f5b` (#67). The phase-3
browser smoke had last run before the second `master` merge, so it was run
again on `master` itself: uvicorn in local desktop mode on :8765 serving the
built SPA, `node scripts/smoke-reports.mjs --base http://127.0.0.1:8765 --browser`.

| Leg | Result |
|---|---|
| API journey: health → fixture network → save → `strong_grid` study → evidence-only report → generate refused `missing_api_key` (no key here) → list / get / figure / export → `.docx` blob | 19/19 |
| Browser journey: Reports row → panel lists the report → Generate button → viewer shows 16 sections → per-section Regenerate → no page errors | 8/8 after one fix (below); the first run failed at the Reports row |

**Found and fixed (this commit).** A fresh Playwright profile is a first run,
which `master`'s guided mode (spec §3.2, G4) starts in Guided — and Guided
hides the Reports row, an Expert-only sidebar entry (§3.5). The journey is
the Expert one, so the script now chooses Expert before the SPA boots,
through the same two `localStorage` keys the mode switcher writes. Open
product question, not changed here: whether a Guided-mode user should reach
Reports at all (today only through the Energy Hub panel's report request).

**Still not covered by a browser leg.** The generate and regenerate legs
(need an LLM key), the template picker / mapping-plan editor / `docx-preview`
pane (phase 4) and the round-trip upload / version diff (phase 5): those
are exercised by the backend QA drivers and the component tests only.

## §7 — Live probe, browser legs for phases 4 and 5, Guided-mode reach, 2026-09-30

Branch `claude/reports-live-probe-and-browser-e2e` on PR #69's head
(`3584133`, `master` + the Expert-mode smoke fix). Backend: uvicorn in local
desktop mode on :8765 serving the built SPA; the venv from the session hook.

### 7.1 Live LLM probe — still UNPROBED, and why

*(Superseded by §7.5: the probe ran on 2026-10-05.)*

The probe was to run on the anthropic profile with the environment's key.
**This container has no key**: `GET /api/chat/health` on the local backend
answered `anthropic_api_key_present: false` (`active_profile: anthropic-sonnet`,
`default_model: claude-sonnet-5`, `chat_ready: false`), and the smoke's
generate leg was refused 400 `missing_api_key` as in §3 and §6c. The backend
reads `ANTHROPIC_API_KEY` from the launching shell (`services/app_secrets`
precedence: shell > `user.env` > `backend/.env`); the variable is not set in
this session's shell. So **no report was written by a model here, and no
model can be named.** The probe is not skipped silently:

* `tests/qa_reports_live_anthropic.py` (new) is the probe as a driver. It
  runs the real study, then `generate_report` → `get_report_status` →
  `get_report` → `export_report_docx` → `regenerate_report_section`
  **through the chat tools** on the active profile (no fake at the seam;
  `PYPSA_GUI_TEST_LIVE_PROFILE` picks another configured profile), and
  inspects the audit on the real prose: every numeric token the audit's own
  tokeniser finds in each model-written section is either in
  `audit.verified` or in `audit.unverified` (none silently accepted), the
  audit lists nothing that is not in the prose, and the exported
  "Numbers to check" appendix carries every unverified number under its
  section's heading and nothing else; then one regenerate under an
  instruction leaves every other section byte-identical. Without the key it
  prints `UNPROBED` and exits 0 (so `run_qa_drivers.py` stays green in CI,
  where no key exists); `--require-key` exits 2 instead.
* Its legs 2–5 were exercised on the scripted fake provider through an
  ad-hoc harness (a planted `99.9 h/yr` and a copied cost figure): 23/23
  PASS, the planted number flagged and listed in the appendix, the copied
  one verified. One driver bug found by that run and fixed: the appendix
  parser took the writer's introductory Disclosure sentence for a row (it
  contains `: `); rows are now recognised by their bold heading run.

**To run the probe** (a workstation, or a session whose environment sets the
variable): `cd pypsa-gui/backend && ANTHROPIC_API_KEY=… python
tests/qa_reports_live_anthropic.py --require-key`, and
`node scripts/smoke-reports.mjs --base … --browser` against a backend
started from a shell that has it — the smoke's sections 3 and 6 then run
the generate, regenerate and "Propose with the assistant" legs instead of
reporting them skipped. Record the `model` the job record names.

### 7.2 Phase 4 and phase 5 in the browser

`scripts/smoke-reports.mjs --browser` now continues after the viewer leg
(the sections are numbered 6 and 7; cleanup is 8):

| Leg | What it does | Result |
|---|---|---|
| 6 templates | builds both fixtures with `tests/fixtures/report_templates/_build.py` (SMOKE_PYTHON) → uploads the TAGGED one through the picker's file input → outline says `tagged`, the export button names it → Export .docx → the export is of the latest version → docx-preview renders it: the report title where `{{ meta.title }}` was, the looped table header, no tag left → uploads the CORPORATE one → `untagged`, the mapping-plan editor lists its 7 headings → review by hand (heading 1 ← executive summary; heading 4 renamed + ← fmea_top; heading 5 dropped) → unsaved tag → Save plan → `GET …/template` carries it → Export with this template → preview: renamed heading present, "Lorem ipsum" gone, cover and "Confidential" footer intact → "Built-in default" unbinds | 25/25 |
| 7 round trip | API export of the open report → `scripts/smoke_reports_edit_docx.py` (python-docx: a new paragraph after the `fmea_top` heading, a Word comment on a `certification` run) → uploaded through the round-trip file input → result panel "Merged as v5", the edited section listed as edited by you, the comment as a pending instruction → the section card shows the `section-edited` tag, the commented card the pending instruction → API: `source user_edit`, `pending_instruction` set, version base + 1 → Compare… → diff view: "1 changed · 0 added · 0 removed · 15 unchanged", the edited row `changed`, the commented row carries the instruction | 14/14 |
| whole run | sections 0–8, `GENERATION PATH: skipped` | **62/62 PASS** |

"Propose with the assistant" runs in leg 6 only when the generation path
ran (it needs the LLM); without a key the plan is reviewed by hand and the
script says so.

**Found by leg 6, fixed on this branch.** The first run exported
`report_…_v1.docx` under a button reading "· tagged_minimal.docx", and
the preview showed the built-in writer's document (no looped table). A
bind writes a NEW version (v2) carrying `template_file_id`; the viewer's
document query still held v1 and `exportReport` sends `version:
shownVersion`, so the export route rendered v1 — which has no template —
with the default writer. The picker's bind mutation now invalidates the
document (`REPORT_DOC_PREFIX`) and the list (`REPORTS_LIST_KEY`) as the
round trip's `onMerged` already did; `TemplatePicker.test.tsx` +1 pins it,
and the smoke asserts the exported filename's version equals the latest.
The backend driver could not see this: it re-fetches the document after
every bind. Second run: 62/62.

### 7.3 Can a Guided-mode user reach Reports today?

**Yes, but only through Expert surfaces, and nothing in Guided points there.**
Guided hides the SIMULATION section (spec §3.5), which is where the Reports
row lives (`Sidebar.tsx`, "Reports" `SItem`), and the command palette has no
Reports action. The reachable path: hub-design **Results** card → "Open full
report" (`hub-results-open-report` → `openFullReport()`: `setSlidePanel('results')`
+ adequacy tab + `requestEhReport()`) → the Energy Hub reference-design panel
→ its "Reports" button (`eh-open-reports` → `setSlidePanel('reports')`); the
Adequacy tab's "Write report" button (`adequacy-open-reports`) does the same.
`App.fullPageContent` renders `ReportsPanel` for `'reports'` in either mode,
so once opened it works. The Assistant path (`generate_report`,
`export_report_docx`) writes and exports a report from chat in Guided too,
but hands back a chip, not the viewer. So the Word report — the deliverable
of the guided flow — is three hops away on pages Guided otherwise hides.

**Smallest change, if wanted (not made here):** a second button on the
hub-design Results card, "Reports" (`hub-results-open-reports` →
`setSlidePanel('reports')`), next to "Open full report" — one card file,
one test, no sidebar or spec §3.5 table change, and the Reports panel's own
"Generate report…" then does the rest. The alternative — a Guided sidebar
row like `sidebar-hub-design` — adds a second Guided-only nav entry and
touches the §3.5 table and the Guided sidebar tests. Whether Reports should
be a first-class Guided step at all is the product question to decide.

### 7.4 Gate on this branch

| Tier | Command | Result |
|---|---|---|
| Frontend | `npx vitest run --testTimeout=20000` | 251 files, 2843 tests passed (TemplatePicker +1) |
| Frontend | `npx tsc -b` | clean |
| End-to-end QA | drivers 0, 1, 2, 4, 5 | 18/18, 38/38, 47/47, 47/47, 37/37 |
| End-to-end QA | `qa_reports_live_anthropic.py` | UNPROBED (no key), exit 0; `--require-key` exit 2 — 23/23 with a key (§7.5) |
| End-to-end QA | `smoke-reports.mjs --browser` | 62/62 after the picker fix — 73/73 with a key and the race fixes (§7.5) |

### 7.5 Live probe with a key, 2026-10-05

A later session on this branch (`2320ee4`) had a personal Anthropic key in
its environment. It was passed only on the commands that needed it, as
`ANTHROPIC_API_KEY`, with the session's `ANTHROPIC_BASE_URL` unset so the
SDK reached `https://api.anthropic.com`. A one-call check answered 200 from
`claude-sonnet-5`.

**The report was written by profile `anthropic-sonnet`, model
`claude-sonnet-5`** (the job record's `profile`/`model`, in both the probe
and the smoke).

| Run | Result |
|---|---|
| `tests/qa_reports_live_anthropic.py --require-key` | **23/23 PASS**. 7 target sections; 6 model-written (`executive_summary`, `target`, `cost`, `sizing`, `tea`, `copt`), `fmea_top` fell back with a "prose not established" note (`repairs=1`, `prose_failures=['fmea_top']`). 75 numeric tokens in the prose, none silently accepted; one flagged as not in the evidence (Executive summary: `4`), and the exported "Numbers to check" appendix lists exactly that one. The regenerate under an instruction wrote v2 with 15/15 other sections byte-identical, still model prose. |
| `smoke-reports.mjs --browser` (uvicorn local mode with the key, built SPA) | first run **2 FAIL**, after the fixes below **73/73 PASS**, `GENERATION PATH: generated`: generate, regenerate and "Propose with the assistant" (mapping job `done`, `mode: mapping`) ran for real. |

**Two smoke-script races, fixed on this branch** (both only show on the
generation path, which no earlier run reached):

* After clicking "Propose with the assistant" the smoke polled
  `…/generate/status` before the propose `POST …/template/plan` had
  started the job, and read the previous (regenerate) record, already
  `done` — `status=done mode=regenerate`. It now waits for the POST and
  polls until the record is the mapping job's.
* `exportAndPreview` armed the blob wait after the export answered. On
  the second export the preview pane was already open and fetched the new
  blob immediately, so the wait missed it (60 s timeout). The wait is now
  armed before the click and matched once the `file_id` is known.

**Found by the first run, not fixed here: a mapping job can overwrite a
plan saved by hand.** Because of the first race, the smoke edited and saved
the plan while the mapping job was still waiting on the model; the job
finished after the save and export. `run_mapping_job` stores its proposal
unconditionally (`services/reports/report_job.py`,
`store.update_meta(…, mapping_plan=plan)`), and
`PUT …/template/plan` (`routers/reports.py`, `put_template_plan`) has no
`_job_slot_busy()` guard — the round-trip merge route has one (409
`report_job_in_flight`). In the editor only the Propose button is
disabled while a job runs (`MappingPlanEditor.tsx`, `disabled={jobBusy}`);
the rows and Save plan are not. So a user who edits during a proposal can
lose their saved plan to the job. Smallest fix: the same 409 guard on
`put_template_plan`, and Save plan disabled while `jobBusy`.

## Increment 1 — status

Phases 0–5 delivered on `claude/fmea-llm-reporting-feasibility-jtm6w1`
(increments 1, 2 and 3), gated twice against `master` (§6, §6b); phases 4
and 5 have a browser leg since §7. The live LLM probe ran on
2026-10-05 (§7.5): 23/23, report written by `claude-sonnet-5` on the
`anthropic-sonnet` profile, browser smoke 73/73 on the generation path.
Not done: the workstation checks listed per phase, and the mapping-job vs.
hand-saved-plan overwrite found in §7.5.
Open product question: Reports in Guided mode (§7.3). `export_eh_report_docx`
(WP0) remains alongside `export_report_docx`; keep as the no-LLM shortcut or
remove in review.
