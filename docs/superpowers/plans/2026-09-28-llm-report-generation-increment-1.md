# LLM-assisted study reports — spike + increment 1 (generate, read, export)

> **For agentic workers:** Implement work-package by work-package, TDD per package. Prefer reusing the evidence assembly that already exists (`build_study_report`, `assemble_reference_design_report`, the `fmea_top` stage payload) over any new summariser; the model writes prose only, never a table cell or a figure. Every LLM call goes through `services/llm_provider.LLMProvider.stream` — no provider name above that seam. Numbers that cannot be established render as "not established", never as 0 (ADR-0001).
>
> **Companion assessment:** `docs/superpowers/assessments/2026-09-28-llm-report-generation-feasibility.md` (§7 pins the twelve product decisions; §1 is the verified inventory of what exists).
> **Parent specs:** `docs/superpowers/specs/2026-09-14-eh-reference-design.md` (decision 16: one report builder; §4 completeness enum), `docs/superpowers/specs/2026-08-27-solution-fmea-adequacy-design.md` (§10 provenance contract), `docs/superpowers/specs/2026-08-05-llm-provider-seam-design.md`.
> **Findings:** to be written at the end as `docs/superpowers/findings/2026-09-28-llm-report-generation-increment-1.md` with before/after test counts and desktop e2e evidence.

**Goal.** "Write me the client report" produces a Word document from what the session's studies actually established: the Energy Hub `ReferenceDesignReport` and the adequacy `build_study_report` payload, with the FMEA top-N ranking and the frontier as code-rendered tables and figures, model-written prose between them, every prose number audited against the evidence, every unestablished section stated as such. The user reads it in the app, regenerates one section with an instruction, and downloads the `.docx`. Templates (increment 2) and the Word round trip (increment 3) build on the same `ReportDocument`.

**Pinned decisions (assessment §7).** Both studies, EH first (1). Audit-and-flag prose numbers; tables and figures from code (2). Template modes both, tagged first — increment 2 (3). Viewer + per-section regenerate + Word round trip; no in-app editor (4). `.docx` only, PDF opportunistic (5). **Charts from increment 1** (6). Template language, English fallback (7). Chat tool and panel button (8). **All LLM profiles equally** (9). Templates per project (10). Standard conventions, no sample deliverable (11).

**Already shipped (do not rebuild).**
- Evidence: `services/adequacy/study_report.py::build_study_report` (`required_disclosures`, `not_established`, `evidence_gaps`, per-section `engine`/`fidelity`); `services/adequacy/eh_report.py::assemble_reference_design_report` + `export_reference_design` + `load_eh_report`; `eh_stages.run_fmea_top_stage` payload (`top`, `classes_included`, `class_b`, `note`); `services/adequacy/worksheet.py` (class-D rows + overlays).
- LLM seam: `services/llm_provider.py` (`LLMRequest`, `LLMEvent`, `LLMProvider.stream`), providers `llm_anthropic.py`, `llm_openai_compat.py`, `llm_fake.py`; profile resolution `chat_service._provider_for_profile`; the `<untrusted_data>` fence and `_neutralise_untrusted_delimiters`.
- Files: `services/upload_service.py` (per-project store, 25 MiB cap, `.docx` allowlisted, bundle inclusion), `chat_tools._save_agent_export` (agent-export chip), desktop download handling (`backend/desktop/downloads.py`).
- Long-job shape: `eh_study_runner.start_eh_study` + `_publish_study` + poll-by-kind on `get_adequacy_results` + `abort_adequacy_study`.
- Tool plumbing: `chat_tools_schema.TOOLS` / `TOOL_ROUTES` / `DISPATCHERS` parity tests; `tool-error-kinds.json` manifest; `test_packaging_requirements.py` pin check.
- Frontend: `EhReferenceDesignPanel.tsx` (structured EH view), `ChatMarkdown.tsx` (react-markdown + gfm), `UploadChipStrip` (export chips with `<a download>`), `PageKit` primitives.

**Honest scope (increment 1).**
- The report covers what the evidence collector finds in the **current session's result state** for the **active project**. It does not re-run anything; a missing study is a `not_established` section with the same hint `get_adequacy_results` gives.
- Prose is generated **per section** with a small schema (`{"section_id", "paragraphs": [str], "bullets": [str]}`), on every wire as JSON text validated by pydantic with one repair retry; the Anthropic wire may additionally use a strict-schema tool, but nothing depends on it. A section the model cannot produce is `not_established` with the reason and the profile name.
- The number audit is a **check, not a rewrite**: unmatched numbers are flagged, never edited. Matching normalises thousands separators, unit suffixes and rounding to the evidence's own precision.
- Figures are matplotlib PNGs rendered on the backend (Agg), embedded in the `.docx` and shown in the viewer: frontier curve, FMEA Pareto bar, capacity mix. A figure whose data is missing is omitted with a one-line statement, never a blank axis.
- Default template only. The bundled default is **built in code** (`services/reports/default_template.py`), not a binary in git, so there is no `datas` entry to forget.
- Storage: `<project_dir>/reports/<report_id>/v<N>.json` + `meta.json`; `reports/` joins `_BUNDLE_DIRS` so it travels with save-as, copy and snapshots exactly like `uploads/`.
- Multi-user: report routes take `ProjectAccessDep` like the worksheet; writes require the edit lock; ids match `\A[0-9a-f]{16}\Z`.
- Not in this increment: user templates, round trip, PDF, rich-text editing, PowerPoint, a template library.

**Tech stack.** Backend: python-docx 1.2.0 (already in pixi; **must be pinned in `gui-requirements.txt`**, WP0), matplotlib (pinned, `gui-requirements.txt:35`), pydantic, FastAPI, threading (same as EH runner). Frontend: React 19 + TS, TanStack Query, react-markdown; no new dependency in increment 1 (`docx-preview` arrives with increment 2).

---

## Phases, and the test gate every phase must pass

The work packages below are grouped into phases. A phase is **complete only
when all four test tiers are green and recorded** in the findings file; a
phase that is green on unit tests alone is not complete.

| Tier | What it is | Where it lives | Run with |
|---|---|---|---|
| **Unit (TDD)** | Red → green per step inside a work package; pure functions and single modules; `llm_fake` for anything that would call a model | `tests/test_report_*.py`, `tests/test_chat_report_*.py` | `python -m pytest tests/test_report_*.py tests/test_chat_report_*.py -q` |
| **Regression** | The suites the phase could break, unchanged and green: chat tools + schema/manifest/packaging parity, uploads, Energy Hub + adequacy, projects/tenancy/desktop; frontend `vitest` for phase 3+ | existing files | the four chunks in §"Regression chunks" below (the whole backend suite exceeds a 10-minute run in the cloud container, so it is chunked, never skipped) |
| **Integration** | The HTTP journey through `TestClient` with the solver stubbed the way `test_energy_hub_study_http.py` stubs `run_eh_study`: routes, auth/ACL, edit lock, bundle transitions, job start/poll/abort, chat-tool dispatch through `chat_service._dispatch_real_tool_call` | `tests/test_report_routes.py`, `tests/test_report_job_http.py`, `tests/test_chat_report_dispatch.py` | `python -m pytest tests/test_report_routes.py tests/test_report_job_http.py tests/test_chat_report_dispatch.py -q` |
| **End-to-end QA** | One standalone `qa_reports_<phase>.py` driver per phase (PASS/FAIL script, exit code, runs under `tests/run_qa_drivers.py`): a signed-in client, a real network, the real EH study on a small fixture where the phase needs real numbers, the fake LLM provider where it needs prose, the produced `.docx` re-opened and its content asserted; plus, from phase 3, a Playwright smoke (`frontend/scripts/smoke-reports.mjs`, Chromium) against uvicorn + the built SPA; plus the manual desktop check recorded in the findings file | `tests/qa_reports_*.py`, `frontend/scripts/smoke-reports.mjs` | `python tests/run_qa_drivers.py` (all drivers) / `node scripts/smoke-reports.mjs` |

**Regression chunks** (each fits the container's per-command ceiling; together
they are the backend suite minus `slow`):

```
cd pypsa-gui/backend
python -m pytest tests/test_chat_*.py tests/test_tool_error_kind_manifest.py tests/test_packaging_requirements.py -m "not slow" -q
python -m pytest tests/test_energy_hub_*.py tests/test_report_*.py tests/test_study_report.py tests/test_adequacy_*.py -m "not slow" -q
python -m pytest tests/test_upload*.py tests/test_desktop*.py tests/test_project*.py -m "not slow" -q
# the remainder (~180 files) does NOT fit one run: list it and split into groups of ≤45 files
ls tests/test_*.py | grep -v -E 'tests/test_(chat_|energy_hub_|report_|upload|desktop|project|adequacy_)' > /tmp/rest.txt
split -n l/4 /tmp/rest.txt /tmp/rest_   # then one run per group, in parallel on a 4-core box:
python -m pytest $(cat /tmp/rest_aa) -m "not slow" -q --durations=15
```

| Phase | Work packages | Deliverable the e2e driver proves | Findings file |
|---|---|---|---|
| **0 — Spike** (done 2026-09-28) | WP0 | `export_eh_report_docx` turns a stored EH report into a `.docx` chip; owed: `qa_reports_phase0.py` (HTTP: stubbed EH study → tool → blob → python-docx re-open) — delivered with phase 1 | `findings/2026-09-28-llm-report-generation-increment-1.md` §0 |
| **1 — Foundation** (WP1/2/4/5 done 2026-09-28; e2e `qa_reports_phase1.py` 38/38, `qa_reports_phase0.py` 18/18) | WP1 model/store/routes, WP2 evidence collector, WP4 figures, WP5 writer-from-`ReportDocument` + an **evidence-only** report (`POST /{name}/reports` with `mode: "evidence_only"`: tables, figures, disclosures and `not_established` sentences, no prose) | `qa_reports_phase1.py`: real small EH study → evidence-only report created → listed → fetched → exported `.docx` re-opened with every section present → project saved-as carries `reports/` (snapshot restore replacing it is the WP1 route test `test_snapshot_carries_reports_and_restore_replaces_them`) | §1 |
| **2 — Generation** | WP3 generator + job + number audit, WP6 chat tools/manifest/docs | `qa_reports_phase2.py`: same study → `generate_report` on the fake provider (scripted valid JSON, one repair, one failure) → every section `ok` or `not_established` with reason → audit flags a planted wrong number → regenerate one section with an instruction → new version → abort mid-run leaves a partial version; optional live probe on a local OpenAI-wire model behind `PYPSA_GUI_TEST_LIVE_OPENAI_PROFILE` (same gate as the wire probe runbook) | §2 |
| **3 — Reading and export in the app** | WP7 Reports panel, viewer, regenerate, panel buttons | `smoke-reports.mjs`: sign in → open project → Generate → progress → viewer shows sections, flagged numbers, figures → Regenerate → Export → the download lands; plus the macOS desktop download-and-open-in-Word check | §3 |
| **4 — Templates** (increment 2) | template upload kind, `docx_reader`, tagged + untagged modes, `docx-preview`, language detection | `qa_reports_phase4.py`: a tagged template and an untagged corporate template (fixtures under `tests/fixtures/report_templates/`) → both render with cover/footer intact, TOC refresh flag set, every section mapped or reported unmapped | §4 |
| **5 — Round trip** (increment 3) | edited-`.docx` upload, tracked changes, bookmark matching, comments as instructions, versions diff, opportunistic PDF | `qa_reports_phase5.py`: export → edit paragraphs + add a comment via python-docx → re-upload → merged version marks `user_edit`, comment becomes an instruction, unmatched content reported | §5 |

## Agent-based TDD protocol (how each work package is executed)

One agent per work package, in its own git worktree on a branch named
`wp/<n>-<slug>` off this branch, files disjoint from every other agent in
the same phase. Each agent:

1. reads this plan's package, the assessment §1 inventory and the files the
   package names — nothing else is assumed;
2. writes the failing tests first (the package's **Acceptance** list, one
   test per bullet at minimum), runs them, records the red output;
3. writes the minimum code to green, runs the package's unit tests, then
   the regression chunk(s) the package touches, then `ruff check` on its
   files;
4. ticks the plan's checkboxes, fills the package's **TDD evidence** line
   with real counts and the red→green corrections worth keeping;
5. commits on its branch with a message naming the package and the counts,
   and reports: branch, commit, files, test counts, anything it could not
   do and why. It never pushes, never merges, never edits another package's
   files, and never skips, disables or marks a failing test.

The orchestrator merges the phase's branches into this branch, runs the
phase gate (all four tiers), writes the findings section, and only then
starts the next phase. A red gate goes back to the owning package as a
fix task before anything new starts.

## Phase QA gate + TDD protocol (mandatory)

## WP0 — Spike: a no-LLM `.docx` of the EH report, downloadable from the chat strip

**Why first.** Proves the three things this feature cannot ship without and that no test in pixi can observe: the writer produces a file Word opens, a matplotlib PNG embeds, and the desktop shell downloads the chip. Everything later reuses this writer.

**Files.** `services/reports/__init__.py`, `services/reports/docx_writer.py`, `services/reports/default_template.py`, `services/reports/figures.py`, `services/reports/formatting.py`, `services/chat_tools.py` (`export_eh_report_docx`), `services/chat_tools_schema.py` (tool + route), `pypsa-gui/tool-error-kinds.json`, `pypsa-gui/gui-requirements.txt` (`python-docx==1.2.0`), `pypsa-gui/pypsa-gui.spec` (`collect_data_files("docx")` for its bundled `templates/`), `pypsa-gui/CHATBOT.md` (tool row), `tests/test_report_docx_writer.py`, `tests/test_report_figures.py`, `tests/test_chat_report_export_tools.py`.

**Steps**
- [x] `formatting.py`: `fmt_number(value, unit=None, digits=None) -> str` renders `None`/NaN as `"not established"`; `fmt_status(status)` maps the completeness enum to prose (`ok` → established, `not_established` → "not established", `skipped` → "not run in this study").
- [x] `default_template.py::default_template_document() -> docx.Document`: a Document with the styles the writer uses (`Title`, `Heading 1..3`, `Normal`, `List Bullet`, `Table Grid`, a `Caption` and a `Disclosure` paragraph style), a footer with "Generated by PyPSA Studio — evidence hash {…}". Built from python-docx's own default so no binary is committed.
- [x] `docx_writer.py::render_reference_design_docx(report: dict, *, fmea_worksheet: dict | None = None, template=None, figures: dict[str, bytes] | None = None) -> bytes`. Section order = `REPORT_SECTIONS`. Headline table (target vs achieved: ENS cap ‱, achieved ‱, shed hours, MC LOLE, cost at target + `period_basis` + "excludes shed cost", LCOE, LCOH with its flag). Completeness table. Per section: an `ok` section renders its table(s) and the stage note; a `not_established` / `skipped` section renders one sentence with the note — **never omitted**. Tables: certification (metric, target, LOLE with CI, EUE, draws, converged, verdict, warning as a Disclosure paragraph), frontier (target ‱ / status / cost / ENS MWh / shed h, knee marked), sizing (carrier / MW), fmea_top (rank / class / component / name / occurrence per yr / severity € / criticality €/yr / ΔEUE MWh) with `note` and `class_b.status` as Disclosure paragraphs, redundancy / levers / dtc as key-value tables from their payloads, tea, gates, pipeline (stage / status / solves / note). Figures inserted after their table when present in `figures`.
- [x] `figures.py`: `fmea_pareto_png(top)` → `bytes | None` (None when the input is empty); `frontier_png` and `capacity_mix_png` land in WP4. Agg backend set locally; 150 dpi; palette and axis rules per the `dataviz` skill; every axis labelled with its unit; the "excludes shed cost" caveat in the frontier caption, not the title.
- [x] Chat tool `export_eh_report_docx(filename: str | None)` — "Safety: write" like `export_chat_summary`. Reads the stored report through `routers.results.get_eh_reference_design` (204 → `HTTPException(404, {"error_kind": "eh_report_not_found", …})` with the `eh_reference_design` no-data hint), the worksheet through the active project, renders, saves via `_save_agent_export(bytes, name, DOCX_MIME)`. `TOOL_ROUTES` entry `_SERVICE_CALL`; `DISPATCHERS` entry; manifest row `eh_report_not_found: inline`.
- [x] Pin `python-docx==1.2.0` in `gui-requirements.txt` with the one-line reason the file's header demands; add `datas += collect_data_files("docx", includes=["templates/*"])` to the spec next to the pypsa line.

**Acceptance**
- [x] Golden skeleton (`tests/fixtures/eh_archetypes/mvp_a_report_skeleton.json`, everything `not_established`) renders; the document contains one "not established" sentence per `REPORT_SECTIONS` entry; no cell contains `0` where the payload had `None`.
- [x] A populated report (fixture built in the test: target/cost/sizing/certification/frontier/fmea_top ok) renders the six tables with the payload's numbers formatted by `fmt_number`; the FMEA rows appear in payload order; the Link-primary note and the MC warning appear as Disclosure paragraphs.
- [x] With `figures={"fmea_top": png}` the document has one inline picture; without figures it has none.
- [x] `fmea_pareto_png([])` is `None`; with three rows it returns PNG bytes (magic `\x89PNG`).
- [x] Tool: no stored report → `error_kind == "eh_report_not_found"`; stored report → result `{file_id, filename, mime == DOCX_MIME, kind == "agent_export"}` and the blob on disk opens with `docx.Document`.
- [x] `test_packaging_requirements.py`, the three tool parity tests and the manifest test green.
- [ ] Manual (recorded in the findings file): download the chip in the macOS desktop shell and open the file in Word; the PNG shows; headings appear in the navigation pane.

**TDD evidence (2026-09-28, branch `claude/fmea-llm-reporting-feasibility-jtm6w1`):**
red — `ModuleNotFoundError: services.reports` / `AttributeError: chat_tools has no attribute export_eh_report_docx` across the three new files → green — `test_report_docx_writer.py` 14, `test_report_figures.py` 4, `test_chat_report_export_tools.py` 5 (23 passed); `test_chat_tools_schema_match.py`, `test_chat_tools_endpoint_map.py`, `test_tool_error_kind_manifest.py`, `test_packaging_requirements.py`, `test_chat_upload_tools.py` unchanged and green; `ruff check` clean on the new files. Two red→green corrections worth recording: a hand-typed 1×1 PNG fixture failed python-docx's IHDR parser (the fixture is now built from chunks with real CRCs), and `safety_tier_for` takes the tool *name*, not the tool dict. The FMEA Pareto figure was rendered and inspected: one axis, one hue, ink-coloured labels, thousands-grouped ticks, cumulative share annotated in ink (no second y-scale). Open: the macOS desktop download-and-open-in-Word check, and a LibreOffice PDF of the sample — LibreOffice in the cloud container cannot load *any* `.docx` (a plain python-docx control file fails identically), so the visual page check is owed from a workstation.

---

## WP1 — `ReportDocument` model, store and routes

**Files.** `models/report.py`, `services/reports/store.py`, `routers/reports.py` (mounted under `/api/projects`, before the `/{name}` catch-all, like `adequacy_worksheet.py`), `routers/projects.py` (`_BUNDLE_DIRS += ("reports",)`), `main.py` (router include), `tests/test_report_model.py`, `tests/test_report_store.py`, `tests/test_report_routes.py`.

**Steps**
- [x] `models/report.py`: `Block = Paragraph{md} | Bullets{items} | TableRef{table_id, caption} | FigureRef{figure_id, caption} | Callout{kind: disclosure|gap|not_established, text} | Field{key, value}`; `Section{section_id, heading, source: llm|user_edit|code, status: ok|not_established|skipped, blocks, note, audit: {unverified: [str]}}`; `ReportDocument{schema_version: 1, report_id, version, title, language, created_at, evidence_hash, profile_id, model, template_file_id: None, sections, tables: dict[table_id, Table{columns, rows, caption, source_path}], figures: dict[figure_id, Figure{png_file, caption, source_path}]}`. `extra="ignore"` for forward compatibility, as `UploadMeta` does.
- [x] `store.py`: `create_report(project_dir, doc) -> meta`, `save_version(project_dir, doc) -> int` (the id is on the document; the store assigns `latest + 1`, never the caller), `load_report(project_dir, report_id, version=None)`, `list_reports(project_dir)`, plus `load_meta`, `delete_report`, `write_figure`, `figure_path`. Layout `reports/<report_id>/{meta.json, v1.json, v2.json, figures/<figure_id>.png}`. Atomic writes via `services/atomic_io.py`; ids `secrets.token_hex(8)`; `REPORT_ID_RE = r"\A[0-9a-f]{16}\Z"` anchored like upload ids, checked at the service boundary so the chat path (no route converter) is covered too.
- [x] Routes: `GET /{name}/reports`, `GET /{name}/reports/{report_id}` (`?version=N` optional), `GET /{name}/reports/{report_id}/versions/{v}`, `GET /{name}/reports/{report_id}/figures/{figure_id}` (FileResponse, inline), `DELETE /{name}/reports/{report_id}` (edit lock, check-only like the worksheet PUT). `POST` arrives with WP3 (generate) and WP6 (export). `ProjectAccessDep` on every route; path traversal impossible by construction (ids validated, paths joined from `AuthorizedProject.directory`). Manifest: `invalid_report_id`, `report_not_found`, `figure_not_found` (all `inline`).
- [x] Bundle inclusion: `reports/` copied on save-as / copy / snapshot create / restore, extracted on bundle import (the seven transitions in `docs/CHATBOT_UPLOADS_WORKFLOW.md`, reusing `_copy_bundle_dirs`). Every call site loops over `_BUNDLE_DIRS`, so the one-tuple change covers all seven; each is driven through `TestClient` in `test_report_routes.py`.

**Acceptance**
- [x] Round-trip `ReportDocument` → JSON → `ReportDocument` is identity; unknown keys ignored.
- [x] `save_version` bumps `version` and never overwrites an earlier file; `load_report(version=None)` returns the latest.
- [x] A report id with a `/` or `..` is refused with `invalid_report_id` (400); another org's project answers 404 (tenancy convention).
- [x] Save-as of a project carries `reports/`; a snapshot restore replaces it.

**TDD evidence (2026-09-28, branch `worktree-agent-a214bee0f11dedaee`, fast-forwarded onto `claude/fmea-llm-reporting-feasibility-jtm6w1` @ 506fac6):**
red — the three new files fail collection with `ModuleNotFoundError: No module named 'models.report'` (3 errors) → green — `test_report_model.py` 8, `test_report_store.py` 33, `test_report_routes.py` 22 (63 passed); regression chunks `test_upload*.py test_desktop*.py test_project*.py` 242 passed 1 skipped, `test_chat_tools_endpoint_map.py test_tool_error_kind_manifest.py test_packaging_requirements.py` 28 passed, and the neighbours `test_report_docx_writer.py test_report_figures.py test_chat_report_export_tools.py test_worksheet_foreign_lock.py test_bundle_sidecars.py test_snapshot_id_containment.py test_qa_step0a.py test_adequacy_swap_guard_callsites.py test_chat_uploads.py` 135 passed; `ruff check` clean on the new files. One red→green correction worth keeping: an HTTP probe with a literal `..` segment never reaches the handler — httpx collapses it client-side (so `/reports/..` hit the list route and returned 200) and a `%2F` decodes to a slash no route matches (it fell through to the SPA catch-all, 503) — so the route test sends `%2e%2e`, which the server unquotes into the path parameter, and the `/` shape is asserted at the store boundary where the chat path (no converter) converges. Decision recorded in the router: a malformed figure id is a 404 `figure_not_found`, not a fourth error kind, because an id outside the slug charset cannot name a figure that exists.

---

## WP2 — Evidence collector

**Files.** `services/reports/evidence.py`, `tests/test_report_evidence.py`.

**Steps**
- [x] `collect_evidence(*, study_report: dict | None, eh_report: dict | None, worksheet: dict | None) -> Evidence` — shipped pure: the caller runs `build_study_report` and passes its dict, so the module needs no network. Unions the study report (adequacy sections with `engine`/`fidelity`, `required_disclosures`, `not_established`, `evidence_gaps`) with the EH report's `sections` + `completeness` + headline fields + `tea` + `gates` + `pipeline`, and the worksheet's class-D rows and overlays. Every EH section carries its `status` and `note` the way adequacy sections carry `engine`/`fidelity`.
- [x] `evidence_hash`: sha256 of the canonical JSON (sorted keys, floats rounded to 6 significant digits) — what `ReportDocument.evidence_hash` and the viewer's staleness banner compare.
- [x] Tables: `evidence.tables` built here, once, by code: `headline`, `completeness`, `certification`, `frontier`, `sizing`, `fmea_top`, `fmea_expert_rows`, `redundancy`, `levers`, `dtc`, `tea`, `gates`, `pipeline`, `adequacy_sections` (the study_report sections as engine/fidelity/status rows). Each with `source_path` (JSON pointer into the evidence) so the audit and the viewer can cite it.
- [x] `flatten_numbers(evidence) -> list[NumberFact{path, value, unit}]` for the audit (WP3).
- [x] Per-section slices: `slice_for(section_id) -> dict` returns only the fields that section's prompt needs (assessment §7, decision 9 consequence).

**Acceptance**
- [x] With no EH report and no adequacy surfaces: every section `not_established` with the `get_adequacy_results` hint; `required_disclosures` still non-empty (the "nothing was measured" disclosure).
- [x] With the P5 MVP-A fixture: `tables["fmea_top"].rows` equal the payload's `top` in order; `tables["headline"]` shows `cost_at_target_eur` with `period_basis` and `excludes_shed_cost`.
- [x] Two collections over the same state produce the same `evidence_hash`; changing one number changes it.
- [x] `flatten_numbers` contains every numeric leaf of `fmea_top.top[*]` with its path.

---

**TDD evidence (2026-09-28):** `tests/test_report_evidence.py` — red: 1 collection error (module absent, 18 tests blocked); green: 18 passed. Regression chunk (`test_energy_hub_report_p5`, `test_study_report`, `test_adequacy_worksheet`, `test_report_docx_writer`): 65 passed. ruff clean. Corrections kept: `frontier` is a section id in both `REPORT_SECTIONS` (EH) and `SECTION_ORDER` (adequacy) — the adequacy one is keyed `adequacy_frontier` (`ADEQUACY_ALIASES`, `adequacy_section_id()`) and every adequacy section carries `study_id` + `source`; `lole_ci`/`eue_ci` elements take the bounded metric's unit; the pipeline's `solves_charged: 0` is a measured zero and is excluded from the None-never-0 scan. The agent's worktree was based on `master`, so its branch carried only the two new files; merged onto the plan branch conflict-free.

## WP3 — Generation job (provider seam, per section, number audit)

**Files.** `services/reports/generator.py`, `services/reports/prompts.py`, `services/reports/number_audit.py`, `services/reports/report_job.py` (runner, `_publish_study`-style slot), `routers/reports.py` (`POST /{name}/reports` → `{status: "running", report_id}`, `GET /{name}/reports/{report_id}/status`, `POST /{name}/reports/{report_id}/abort`, `POST /{name}/reports/{report_id}/sections/{section_id}/regenerate`), `tests/test_report_generator.py`, `tests/test_number_audit.py`, `tests/test_report_job.py`.

**Steps**
- [ ] `prompts.py`: one **stable** system block (writing guide: narrate only from the evidence slice, carry every required disclosure verbatim, state what was not established, no number that is not in the slice, the language to write in) + a per-section user message: the section's purpose sentence, the evidence slice inside the `<untrusted_data>` fence (through `_neutralise_untrusted_delimiters`), the exact JSON shape to return. Short enough for a 7B-class model; no chain-of-thought instructions.
- [ ] `generator.py::generate_section(provider, request_base, section_id, slice, *, language) -> SectionDraft | SectionFailure`: streams through `LLMProvider.stream`, collects `text_delta`s, extracts the first JSON object, validates `SectionDraft` with pydantic; on failure sends one repair message ("return only the JSON object; error: …"); on second failure returns `SectionFailure(reason, raw_head)`. Optional fast path: when the provider is Anthropic, offer the same schema as a strict tool and accept a `tool_use_start`/`message_done` block; never required.
- [ ] `number_audit.py::audit(paragraphs, number_facts) -> AuditResult{verified: [{text, path}], unverified: [str]}`: tokenises numbers (incl. `1 234,5`, `1,234.5`, `12 %`, `€ 3.2M`, `‱`), normalises to floats with unit hints, matches against facts with tolerance = the fact's own displayed precision. Years and section numbers are exempt by pattern.
- [ ] `report_job.py`: `start_report_job(project, evidence, profile, language, sections) -> report_id`; daemon thread; per section: draft → audit → `Section(source="llm", blocks=[Paragraph…, Callout(disclosure)…, TableRef/FigureRef where the section's tables/figures exist], audit=…)`; a `SectionFailure` becomes `Section(status="not_established", note=reason)`; `required_disclosures` are inserted by **code** as Callouts in the executive summary and repeated where their section is; `evidence_gaps` rendered by code **before** the first numbered section. Progress `{done, total, current_section}` on the status surface; `stop_event` honoured between sections; one report job at a time per process (same mesh blocker as the studies).
- [ ] Regenerate one section: same path with an optional `instruction` appended to the user message; result is a new document version; other sections copied unchanged.
- [ ] Figures (WP4) and tables are attached by the job, not the model; the model only sees table ids it may reference.

**Acceptance**
- [ ] With `llm_fake` scripted to return valid JSON: every section `ok`, document version 1 saved, `profile_id`/`model` recorded, `evidence_hash` equals the collector's.
- [ ] With `llm_fake` returning prose then valid JSON on repair: section `ok`, one repair counted in `meta.json`.
- [ ] With `llm_fake` returning garbage twice: section `not_established`, note names the profile; the job completes; the document still has every other section.
- [ ] Audit: a paragraph containing `3.2 h/yr` when the facts hold `mc_lole_h = 3.21` (displayed to 2 decimals) is verified; `4.0 h/yr` is unverified; `2030` is exempt.
- [ ] `required_disclosures` appear as Callout blocks even when the model's paragraphs omit them.
- [ ] Abort between sections leaves a saved partial version with the remaining sections `not_established: aborted`.
- [ ] The evidence slice sent for `fmea_top` contains no key from `frontier` (slice isolation).

---

## WP4 — Figures

**Files.** `services/reports/figures.py` (from WP0), `tests/test_report_figures.py`.

**Steps**
- [x] `frontier_png(points, knee_index)`: cost (€) vs ENS **target** ‱ (the achieved ‱ is not on a frontier point; the table carries achieved MWh), knee ringed and labelled, non-ok points hollow on the baseline with their status word; the caption (`period_basis`, "excludes shed cost") is the writer's, not the figure's.
- [x] `capacity_mix_png(by_carrier)`: horizontal bars, MW, largest first; beyond 30 carriers the tail folds into "other" (`_capacity_rows` is the pure helper).
- [x] `fmea_pareto_png(top)`: criticality €/yr bars with cumulative share line; class letter in the tick label.
- [x] Deterministic output for a fixed input (same bytes, `matplotlib.rcParams` pinned in the function, no timestamps in metadata: `metadata={"Software": None}`).

**Acceptance**
- [x] Each function returns `None` on empty input and PNG bytes otherwise; rendering the same input twice gives identical bytes.
- [x] Figures are written to `reports/<id>/figures/` and referenced by `FigureRef` blocks; the viewer route serves them inline. *(Done by WP5's `POST /{name}/reports` for the evidence-only report — `store.write_figure` per produced PNG, `qa_reports_phase1.py` fetches `figures/fmea_pareto`; WP3's job takes the same path.)*

**TDD evidence (2026-09-28, branch `worktree-agent-ab2e60f44ff5debba`, off `506fac6`):**
red — 10 new tests failed with `AttributeError: module 'services.reports.figures' has no attribute 'frontier_png' / 'capacity_mix_png' / '_capacity_rows'` (4 Pareto tests still green) → green — `test_report_figures.py` 14, `test_report_docx_writer.py` 14 (28 passed); `ruff check` clean on the two files. The three figures now share `_style_axis` (left title, top/right spines off, grid on the magnitude axis only, thousands-grouped ticks); the Pareto was moved onto it with no behaviour change. Layout rcParams (`figure.dpi`, `savefig.dpi`, autolayout/constrained_layout off, title/label/tick sizes, marker size, hinting, path simplification) are pinned in `_RC` so a caller's style sheet cannot make two renders differ; a fresh-interpreter double render of each figure was byte-identical. All three PNGs were rendered and inspected: the knee ring and "knee" label are visible and clear of the line, infeasible points read as hollow markers on the baseline with "infeasible" above them (one in the middle of the frontier, one below the last ok point), ε and ‱ render in DejaVu Sans without glyph warnings, y ticks group thousands, and the capacity bars are sorted descending with grouped value labels.

---

## WP5 — `.docx` writer from `ReportDocument`

**Files.** `services/reports/docx_writer.py` (extend WP0), `services/reports/assemble.py` (the evidence-only document), `routers/reports.py` (the two POST routes), `tests/test_report_docx_writer.py`, `tests/test_report_assemble.py`, `tests/test_report_routes.py`, `tests/qa_reports_phase1.py`.

**Steps**
- [x] `render_document_docx(doc: ReportDocument, *, template=None, figure_bytes: dict[str, bytes]) -> bytes`: title, generated-from line (version, mode, evidence hash, generated-at, profile/model when set), then sections in order. `Paragraph` blocks render the markdown inline subset (bold, italic, code, links) as runs through a small tokenizer (`tokenize_inline`, no markdown dependency); `Bullets` → `List Bullet` (a template without the style gets `Normal` + "• "); `TableRef` → the table from `doc.tables` in `Table Grid` with a numbered caption, a missing id one `Disclosure` line; `FigureRef` → inline picture + numbered caption, or a `Disclosure` line "was not produced"; `Callout` → `Disclosure` style (disclosure), "Gap:"-prefixed `Disclosure` (evidence gap), or a "Not established:" sentence; `Field` → bold label + value.
- [x] Each section heading carries a bookmark named `sec:<section_id>` (the round-trip anchor for increment 3): `w:bookmarkStart`/`w:bookmarkEnd` around the heading run, unique ids per document.
- [x] Unverified numbers appendix: "Numbers to check" listing `audit.unverified` per section when non-empty.
- [x] WP0's `render_reference_design_docx` becomes a thin adapter: `collect_evidence` → `assemble.evidence_only_document` → `render_document_docx` — one writer, decision 16's spirit. The chat tool `export_eh_report_docx` needed no change; it now renders through the same path.
- [x] `assemble.evidence_only_document(evidence, *, title, report_id, figure_pngs) -> ReportDocument` (pure): executive summary (gaps → disclosures → headline → completeness), every `REPORT_SECTIONS` entry in order with its completeness status (an `ok` section: its evidence `TableRef`s, or `Field` blocks from its payload when the collector built no table for it, its figure when the PNG exists, its stage note and the engine's own notes as disclosures; a `not_established`/`skipped` section: exactly one `not_established` callout — never omitted), the adequacy surfaces table and one section per ESTABLISHED adequacy surface (engine/fidelity/source as `Field`s), the expert rows when any, the pipeline last.
- [x] Routes: `POST /{name}/reports` (`{mode: "evidence_only", title?}`; any other mode → 400 `report_mode_not_supported`, "generation lands in phase 2"; edit lock check-only like DELETE; evidence read from the session's current result state exactly as the chat tools read it — `get_eh_reference_design`, `chat_tools.build_study_report`, `load_worksheet` — then WP4's three figures, `store.create_report`, `store.write_figure`; returns the `ReportMeta` fields + `document`) and `POST /{name}/reports/{report_id}/export` (`{version?, filename?}` → `render_document_docx` → `upload_service.add_upload(kind="agent_export")` → the `UploadMeta` dict, so the file shows as a chip and downloads through the blob route; 404 `report_not_found`, 400 `invalid_report_id`). Manifest: `report_mode_not_supported` (`inline`).

**Acceptance**
- [x] A `ReportDocument` with every block type renders; the docx re-opened with python-docx has the headings in order, the bookmarks present, the tables with the right row counts, one picture per `FigureRef`.
- [x] A section with `audit.unverified` non-empty produces the appendix; with all sections clean there is no appendix.
- [x] WP0's golden and populated tests still pass through the adapter (one assertion moved with the wording: a skipped section is now the callout's "Not established: this section was …" sentence rather than WP0's "This section was …" line; `qa_reports_phase0.py` counts the same sentence).
- [x] `test_report_assemble.py`: gaps come before the headline table; every `REPORT_SECTIONS` entry present with the right status; a `None` payload never yields a "0" cell or field; `figure_id`s only when PNGs were passed; `evidence_hash` equals the collector's.
- [x] `test_report_routes.py`: POST evidence_only on a project with a stored EH report → 200, v1 + `figures/fmea_pareto.png` on disk; POST `mode: "generated"` → 400; POST under a foreign lock → 409 `project_locked`; export → upload meta with the docx MIME, the blob route serves it and it re-opens with the `sec:fmea_top` bookmark; export of an unknown id or version → 404.

**TDD evidence (2026-09-28, branch `worktree-agent-ae3b6835d3988255e`, fast-forwarded onto `claude/fmea-llm-reporting-feasibility-jtm6w1` @ dfc1952):**
red — `test_report_assemble.py` and `test_report_docx_writer.py` fail collection (`ImportError: cannot import name 'render_document_docx'`, no `services.reports.assemble` module; 2 errors), the six new route journeys in `test_report_routes.py` fail (no POST routes) → green — `test_report_assemble.py` 14, `test_report_docx_writer.py` 25 (14 WP0 + 11 WP5), `test_report_routes.py` 28 (22 WP1 + 6 WP5); regression `test_report_store.py` 33, `test_report_model.py` 8, `test_report_evidence.py` 18, `test_report_figures.py` 14, `test_chat_report_export_tools.py` 5, `test_chat_tools_endpoint_map.py` 15, `test_tool_error_kind_manifest.py` 4, `test_packaging_requirements.py` 9 — 173 passed in all; `ruff check` clean on every file touched. E2E: `qa_reports_phase1.py` 38/38 (real strong_grid study on `certifiable_weak_network`, budget 8 → POST evidence_only → every section present, fmea_top ok with table + figure, seven skipped stages each one `not_established` callout, headline MC LOLE "not established" → list/get/figure → export chip → blob → python-docx re-open with the top mode `import_poc`, the Link-primary disclosure, the `sec:` bookmarks, one picture per referenced figure → save-as carries `reports/` → DELETE on the original leaves the copy's); `qa_reports_phase0.py` 18/18 still passing through the adapter. Corrections worth keeping: the driver first asserted ONE embedded picture — the study also establishes `sizing`, so the capacity-mix figure is produced and embedded too; the step now asserts `pictures == len(document.figures)`. The evidence collector builds no table for `target`, `cost` or the adequacy payloads, so an established section with no table is rendered as `Field` blocks from its scalars (one level of nesting as `parent / child`, `None` → "not established") rather than an empty heading. Surfaces the adequacy campaign never ran are stated in the "Reliability surfaces of this session" table with their reason rather than as eight near-empty sections; every EH section stays a section because its titles are the document's outline. The snapshot-restore half of the phase-1 deliverable is covered by `test_report_routes.py::test_snapshot_carries_reports_and_restore_replaces_them` (WP1), not by the driver.

---

## WP6 — Chat tools, manifest, docs

**Files.** `services/chat_tools_schema.py`, `services/chat_tools.py`, `pypsa-gui/tool-error-kinds.json`, `pypsa-gui/CHATBOT.md`, `services/chat_service.py` (`_ADEQUACY_GUIDE_CHAINING`: "a client report → `generate_report`; `build_study_report` remains the in-chat summary"), `tests/test_chat_report_tools.py`.

**Steps**
- [ ] `generate_report(sections?: list, language?: str, instruction?: str)` — "Safety: execution" (minutes, spends LLM tokens, one at a time). Returns `{status: "running", report_id}`.
- [ ] `get_report(report_id?, version?)` — read; default the latest report; returns the `ReportDocument` with paragraphs (tables as row counts + ids to keep under the 4000-char result cap; `get_report_table(report_id, table_id)` paginated for the rows).
- [ ] `regenerate_report_section(report_id, section_id, instruction)` — execution; new version.
- [ ] `export_report_docx(report_id?, version?, filename?)` — write; saves through `_save_agent_export`; supersedes WP0's `export_eh_report_docx` (keep the name as an alias for one release or remove it in the same commit — decide in review).
- [ ] `abort_report_generation()` — destructive (matches `abort_adequacy_study` tier).
- [ ] Error kinds: `report_not_found`, `report_job_in_flight`, `report_generation_failed`, `invalid_report_id`, `no_evidence` — each classified in the manifest with a `why`.
- [ ] `CHATBOT.md`: tool table rows and a "Reports" subsection (what the model must and must not do with a report).

**Acceptance**
- [ ] Parity tests green; `TOOL_COUNT` unchanged by magic numbers (it is `len(TOOLS)`).
- [ ] `generate_report` twice while one runs → `report_job_in_flight` (409).
- [ ] `get_report` on a project with no reports → `report_not_found` with the hint to run `generate_report`.
- [ ] The manifest test derives every new kind.

---

## WP7 — Frontend: Reports panel, viewer, regenerate, buttons

**Files.** `frontend/src/api/reports.ts`, `frontend/src/pages/ReportsPanel.tsx`, `frontend/src/pages/reports/ReportViewer.tsx`, `frontend/src/pages/reports/SectionCard.tsx`, `frontend/src/pages/results/EhReferenceDesignPanel.tsx` (Generate report button), `frontend/src/pages/results/AdequacyTab.tsx` (same button), `frontend/src/App.tsx` (panel registration in `fullPageContent`), `frontend/src/store/uiStore.ts`, tests beside each component (vitest + testing-library, as the EH panel tests do).

> **Status (2026-09-28): WP7a shipped / WP7b pending.** WP7a is the phase-1 half — everything that codes against the routes that exist (`list`, `get`, `versions`, `figures`, `DELETE`, `POST` evidence-only, `POST …/export`). WP7b (generate / status poll / regenerate / abort, the evidence-hash comparison, the Adequacy-tab button, "generation pre-armed") lands after WP3's job exists on the backend. The half-done bullets below are annotated rather than ticked.

**Steps**
- [ ] `api/reports.ts`: list, get, status (poll 1.5 s while running, like `GridspinePanel`), generate, regenerate, abort, export (`POST …/export` returning the agent-export meta; the chip appears in the chat strip and a direct `<a download>` is offered in the panel using the upload blob URL). — *WP7a done:* types mirroring `models/report.py`, `listReports`, `getReport(…, version?)`, `getReportVersion`, `reportFigureUrl`, `deleteReport`, `createEvidenceOnlyReport`, `exportReport`, `ReportsError` + `isReportError`/`reportErrorMessage` over `{detail: {error_kind, message}}` (`report_not_found`, `invalid_report_id`, `figure_not_found`, `report_mode_not_supported`, `project_locked`). *WP7b:* status/generate/regenerate/abort.
- [ ] `ReportsPanel`: list with version, profile, evidence status ("evidence changed since v3" when `evidence_hash` ≠ current collector hash from `GET …/evidence_hash`), Generate button with sections + language, progress bar while running, abort. — *WP7a done:* list (title, version, mode, created, profile/model when set), "Create evidence report" (disabled with the reason when no Project is open), delete behind `ConfirmDialog`, selection opens the viewer, refusals as toasts. *WP7b:* evidence-hash comparison, Generate, progress, abort.
- [ ] `ReportViewer`: sections in order; `Paragraph`/`Bullets` through `ChatMarkdown`; `TableRef` as a real table; `FigureRef` as `<img>` from the figure route; `Callout` as tagged boxes; unverified numbers highlighted with a tooltip "not found in the evidence"; per-section "Regenerate…" opens a one-line instruction input; `not_established` sections render their note. — *WP7a done:* all of it except the per-section "Regenerate…" input (*WP7b*). The highlight is a rehype plugin (`pages/reports/highlightUnverified.ts`) passed through a new optional `rehypePlugins` prop on `ChatMarkdown`, so a number inside `**bold**` still gets its `<mark>`; a missing `table_id`/`figure_id` renders an inline note; a version switcher appears when `latest_version > 1`.
- [ ] Buttons on the EH panel and the Adequacy tab navigate to the Reports panel with generation pre-armed. — *WP7a done:* the EH panel's "Reports" button (`eh-open-reports`) opens the panel. *WP7b:* the Adequacy-tab button and pre-arming.
- [x] Desktop: the download uses the anchor-download pattern that `backend/desktop/downloads.py` documents. — `ReportViewer.downloadExport`: `POST …/export` first (so the status code is seen), then an `<a href={blob url} download={filename}>` click, as ChatPanel's export chip does. Note: `routers/uploads.py`'s blob docstring mentions `?download=1`, but the handler reads no such query parameter — the plain blob URL with the `download` attribute is what is used.

**Acceptance**
- [x] Viewer renders a fixture document with every block type; unverified numbers are visibly marked; a `not_established` section shows its note. (`ReportViewer.test.tsx`)
- [ ] Generate → running → done transitions drive the list and the viewer without a reload. (*WP7b*)
- [ ] The Regenerate flow posts the instruction and shows the new version. (*WP7b*)
- [x] `npx vitest run` green; `tsc -b` clean. (WP7a: 181 files / 2010 tests; `tsc -b` exit 0; no new dependency.)

**TDD evidence (2026-09-28, WP7a, branch `worktree-agent-a70aa0de6502c2b3c`, fast-forwarded onto `claude/fmea-llm-reporting-feasibility-jtm6w1` @ 54a23ef):** new files `src/api/reports.ts` + `reports.api.test.ts` (17), `src/pages/reports/highlightUnverified.ts` + test (8), `src/pages/reports/SectionCard.tsx`, `src/pages/reports/ReportViewer.tsx` + test (9), `src/pages/ReportsPanel.tsx` + test (9), plus one test on `EhReferenceDesignPanel.test.tsx` (1). Red: the four new test files failed to resolve their module (43 tests blocked) and the EH test failed on the missing `eh-open-reports` button — 4 files failed, 1 test failed. Green: 44 new tests pass (86 in the five touched files); whole frontend suite 181 files / 2010 tests green; `npx tsc -b` clean. Registration mirrors `GridspinePanel` exactly: `uiStore.SlidePanel` gains `'reports'`, `App.tsx` `PANEL_META` / `FULL_SCREEN_TABS` / `fullPageContent`, a Sidebar row after "Planning → dynamics". One test-fixture correction on the way: a single `Response` object reused across two fetch calls fails on the second read ("Body is unusable"), so the API test mints one per call.

---

## Increment 2 (outline) — user templates
Template upload kind `report_template` (upload allowlist unchanged; `kind` on `UploadMeta`); `docx_reader.py` (outline: heading tree with style names, bracketed placeholders, table shapes, header/footer text, Jinja tags); tagged mode (fields + row loops over `doc.tables`, via Jinja2 on run-merged paragraphs; `docxtpl` only if its sub-document support is needed); untagged mode (LLM mapping plan `{keep|rename|drop|insert}` per template heading, body rebuild after the first mapped heading, `w:updateFields` for the TOC); template language detection; `docx-preview` pane in the viewer; `set_report_template` tool.

## Increment 3 (outline) — round trip and versions
Upload an edited `.docx` bound to a report; accept tracked changes; match sections by the `sec:<id>` bookmarks (heading text fallback); paragraphs back to blocks; changed table cells turn a `TableRef` into a literal table and mark the section `user_edit`; comments (python-docx 1.2 `document.comments`) as per-section instructions; version diff view; the uploaded file becomes the template for the next export; PDF via `soffice --headless --convert-to pdf` only when found on PATH, with `pdf_not_available` otherwise.

---

## Verification (end of increment 1)
- Backend: `python -m pytest -m "not slow"` full pass; new files listed with counts in the findings doc.
- Frontend: `npx vitest run` and `tsc -b`.
- e2e (recorded, not automated): IEEE-39 project → `run_eh_study` (`strong_grid`) → `generate_report` on the Anthropic default profile **and** on a local Ollama profile (`qwen3` class) → open both `.docx` in Word → compare section counts, `not_established` statements and the "Numbers to check" appendix. The local run is expected to produce more `not_established` sections; the report must say so, not hide it.
- Packaging: a Linux onedir freeze with `python-docx` pinned; `check_bundle.py` extended with `import docx; docx.Document()` (the default template is package data).
