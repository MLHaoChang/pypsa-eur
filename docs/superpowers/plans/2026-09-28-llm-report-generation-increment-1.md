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

## Phase QA gate + TDD protocol (mandatory)

**red** (a failing test encodes the acceptance) → **green** (minimum code) → **verify** (re-run the package's test files, then `python -m pytest -m "not slow"` for the backend and `npx vitest run` for the frontend) → record the evidence under the package. Every new `error_kind` is classified in `pypsa-gui/tool-error-kinds.json` in the same commit. The packaging check (`test_packaging_requirements.py`) and the tool parity tests (`test_chat_tools_schema_match.py`, `test_chat_tools_endpoint_map.py`, `test_tool_error_kind_manifest.py`) must stay green at every commit.

---

## WP0 — Spike: a no-LLM `.docx` of the EH report, downloadable from the chat strip

**Why first.** Proves the three things this feature cannot ship without and that no test in pixi can observe: the writer produces a file Word opens, a matplotlib PNG embeds, and the desktop shell downloads the chip. Everything later reuses this writer.

**Files.** `services/reports/__init__.py`, `services/reports/docx_writer.py`, `services/reports/default_template.py`, `services/reports/figures.py`, `services/reports/formatting.py`, `services/chat_tools.py` (`export_eh_report_docx`), `services/chat_tools_schema.py` (tool + route), `pypsa-gui/tool-error-kinds.json`, `pypsa-gui/gui-requirements.txt` (`python-docx==1.2.0`), `pypsa-gui/pypsa-gui.spec` (`collect_data_files("docx")` for its bundled `templates/`), `pypsa-gui/CHATBOT.md` (tool row), `tests/test_report_docx_writer.py`, `tests/test_report_figures.py`, `tests/test_chat_report_export_tools.py`.

**Steps**
- [ ] `formatting.py`: `fmt_number(value, unit=None, digits=None) -> str` renders `None`/NaN as `"not established"`; `fmt_status(status)` maps the completeness enum to prose (`ok` → established, `not_established` → "not established", `skipped` → "not run in this study").
- [ ] `default_template.py::default_template_document() -> docx.Document`: a Document with the styles the writer uses (`Title`, `Heading 1..3`, `Normal`, `List Bullet`, `Table Grid`, a `Caption` and a `Disclosure` paragraph style), a footer with "Generated by PyPSA Studio — evidence hash {…}". Built from python-docx's own default so no binary is committed.
- [ ] `docx_writer.py::render_reference_design_docx(report: dict, *, fmea_worksheet: dict | None = None, template=None, figures: dict[str, bytes] | None = None) -> bytes`. Section order = `REPORT_SECTIONS`. Headline table (target vs achieved: ENS cap ‱, achieved ‱, shed hours, MC LOLE, cost at target + `period_basis` + "excludes shed cost", LCOE, LCOH with its flag). Completeness table. Per section: an `ok` section renders its table(s) and the stage note; a `not_established` / `skipped` section renders one sentence with the note — **never omitted**. Tables: certification (metric, target, LOLE with CI, EUE, draws, converged, verdict, warning as a Disclosure paragraph), frontier (target ‱ / status / cost / ENS MWh / shed h, knee marked), sizing (carrier / MW), fmea_top (rank / class / component / name / occurrence per yr / severity € / criticality €/yr / ΔEUE MWh) with `note` and `class_b.status` as Disclosure paragraphs, redundancy / levers / dtc as key-value tables from their payloads, tea, gates, pipeline (stage / status / solves / note). Figures inserted after their table when present in `figures`.
- [ ] `figures.py`: `fmea_pareto_png(top)`, `frontier_png(points, knee_index)`, `capacity_mix_png(by_carrier)` → `bytes | None` (None when the input is empty). Agg backend set locally; 150 dpi; palette and axis rules per the `dataviz` skill; every axis labelled with its unit; the "excludes shed cost" caveat in the frontier caption, not the title.
- [ ] Chat tool `export_eh_report_docx(filename: str | None)` — "Safety: write" like `export_chat_summary`. Reads the stored report through `routers.results.get_eh_reference_design` (204 → `HTTPException(404, {"error_kind": "eh_report_not_found", …})` with the `eh_reference_design` no-data hint), the worksheet through the active project, renders, saves via `_save_agent_export(bytes, name, DOCX_MIME)`. `TOOL_ROUTES` entry `_SERVICE_CALL`; `DISPATCHERS` entry; manifest row `eh_report_not_found: inline`.
- [ ] Pin `python-docx==1.2.0` in `gui-requirements.txt` with the one-line reason the file's header demands; add `datas += collect_data_files("docx", includes=["templates/*"])` to the spec next to the pypsa line.

**Acceptance**
- [ ] Golden skeleton (`tests/fixtures/eh_archetypes/mvp_a_report_skeleton.json`, everything `not_established`) renders; the document contains one "not established" sentence per `REPORT_SECTIONS` entry; no cell contains `0` where the payload had `None`.
- [ ] A populated report (fixture built in the test: target/cost/sizing/certification/frontier/fmea_top ok) renders the six tables with the payload's numbers formatted by `fmt_number`; the FMEA rows appear in payload order; the Link-primary note and the MC warning appear as Disclosure paragraphs.
- [ ] With `figures={"fmea_top": png}` the document has one inline picture; without figures it has none.
- [ ] `fmea_pareto_png([])` is `None`; with three rows it returns PNG bytes (magic `\x89PNG`).
- [ ] Tool: no stored report → `error_kind == "eh_report_not_found"`; stored report → result `{file_id, filename, mime == DOCX_MIME, kind == "agent_export"}` and the blob on disk opens with `docx.Document`.
- [ ] `test_packaging_requirements.py`, the three tool parity tests and the manifest test green.
- [ ] Manual (recorded in the findings file): download the chip in the macOS desktop shell and open the file in Word; the PNG shows; headings appear in the navigation pane.

**TDD evidence:** _(fill in)_

---

## WP1 — `ReportDocument` model, store and routes

**Files.** `models/report.py`, `services/reports/store.py`, `routers/reports.py` (mounted under `/api/projects`, before the `/{name}` catch-all, like `adequacy_worksheet.py`), `routers/projects.py` (`_BUNDLE_DIRS += ("reports",)`), `main.py` (router include), `tests/test_report_model.py`, `tests/test_report_store.py`, `tests/test_report_routes.py`.

**Steps**
- [ ] `models/report.py`: `Block = Paragraph{md} | Bullets{items} | TableRef{table_id, caption} | FigureRef{figure_id, caption} | Callout{kind: disclosure|gap|not_established, text} | Field{key, value}`; `Section{section_id, heading, source: llm|user_edit|code, status: ok|not_established|skipped, blocks, note, audit: {unverified: [str]}}`; `ReportDocument{schema_version: 1, report_id, version, title, language, created_at, evidence_hash, profile_id, model, template_file_id: None, sections, tables: dict[table_id, Table{columns, rows, caption, source_path}], figures: dict[figure_id, Figure{png_file, caption, source_path}]}`. `extra="ignore"` for forward compatibility, as `UploadMeta` does.
- [ ] `store.py`: `create_report(project_dir, doc) -> meta`, `save_version(project_dir, report_id, doc) -> int`, `load_report(project_dir, report_id, version=None)`, `list_reports(project_dir)`. Layout `reports/<report_id>/{meta.json, v1.json, v2.json, figures/<figure_id>.png}`. Atomic writes via `services/atomic_io.py`; ids `secrets.token_hex(8)`; `_REPORT_ID_RE = r"\A[0-9a-f]{16}\Z"` anchored like upload ids.
- [ ] Routes: `GET /{name}/reports`, `GET /{name}/reports/{report_id}`, `GET /{name}/reports/{report_id}/versions/{v}`, `GET /{name}/reports/{report_id}/figures/{figure_id}` (FileResponse, inline), `DELETE /{name}/reports/{report_id}` (edit lock). `POST` arrives with WP3 (generate) and WP6 (export). `ProjectAccessDep` on every route; path traversal impossible by construction (ids validated, paths joined from `AuthorizedProject.directory`).
- [ ] Bundle inclusion: `reports/` copied on save-as / copy / snapshot create / restore, extracted on bundle import (the seven transitions in `docs/CHATBOT_UPLOADS_WORKFLOW.md`, reusing `_copy_bundle_dirs`).

**Acceptance**
- [ ] Round-trip `ReportDocument` → JSON → `ReportDocument` is identity; unknown keys ignored.
- [ ] `save_version` bumps `version` and never overwrites an earlier file; `load_report(version=None)` returns the latest.
- [ ] A report id with a `/` or `..` is refused with `invalid_report_id` (400); another org's project answers 404 (tenancy convention).
- [ ] Save-as of a project carries `reports/`; a snapshot restore replaces it.

---

## WP2 — Evidence collector

**Files.** `services/reports/evidence.py`, `tests/test_report_evidence.py`.

**Steps**
- [ ] `collect_evidence(*, read, eh_report: dict | None, worksheet: dict | None, campaign, health) -> Evidence`. Unions `build_study_report(...)` (adequacy sections with `engine`/`fidelity`, `required_disclosures`, `not_established`, `evidence_gaps`) with the EH report's `sections` + `completeness` + headline fields + `tea` + `gates` + `pipeline`, and the worksheet's class-D rows and overlays. Every EH section carries its `status` and `note` the way adequacy sections carry `engine`/`fidelity`.
- [ ] `evidence_hash`: sha256 of the canonical JSON (sorted keys, floats rounded to 6 significant digits) — what `ReportDocument.evidence_hash` and the viewer's staleness banner compare.
- [ ] Tables: `evidence.tables` built here, once, by code: `headline`, `completeness`, `certification`, `frontier`, `sizing`, `fmea_top`, `fmea_expert_rows`, `redundancy`, `levers`, `dtc`, `tea`, `gates`, `pipeline`, `adequacy_sections` (the study_report sections as engine/fidelity/status rows). Each with `source_path` (JSON pointer into the evidence) so the audit and the viewer can cite it.
- [ ] `flatten_numbers(evidence) -> list[NumberFact{path, value, unit}]` for the audit (WP3).
- [ ] Per-section slices: `slice_for(section_id) -> dict` returns only the fields that section's prompt needs (assessment §7, decision 9 consequence).

**Acceptance**
- [ ] With no EH report and no adequacy surfaces: every section `not_established` with the `get_adequacy_results` hint; `required_disclosures` still non-empty (the "nothing was measured" disclosure).
- [ ] With the P5 MVP-A fixture: `tables["fmea_top"].rows` equal the payload's `top` in order; `tables["headline"]` shows `cost_at_target_eur` with `period_basis` and `excludes_shed_cost`.
- [ ] Two collections over the same state produce the same `evidence_hash`; changing one number changes it.
- [ ] `flatten_numbers` contains every numeric leaf of `fmea_top.top[*]` with its path.

---

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
- [ ] `frontier_png(points, knee_index)`: cost vs achieved ENS ‱, knee marked, non-ok points hollow; caption states `period_basis` and "excludes shed cost".
- [ ] `capacity_mix_png(by_carrier)`: horizontal bars, MW.
- [ ] `fmea_pareto_png(top)`: criticality €/yr bars with cumulative share line; class letter in the tick label.
- [ ] Deterministic output for a fixed input (same bytes, `matplotlib.rcParams` pinned in the function, no timestamps in metadata: `metadata={"Software": None}`).

**Acceptance**
- [ ] Each function returns `None` on empty input and PNG bytes otherwise; rendering the same input twice gives identical bytes.
- [ ] Figures are written to `reports/<id>/figures/` by the job and referenced by `FigureRef` blocks; the viewer route serves them inline.

---

## WP5 — `.docx` writer from `ReportDocument`

**Files.** `services/reports/docx_writer.py` (extend WP0), `tests/test_report_docx_writer.py`.

**Steps**
- [ ] `render_document_docx(doc: ReportDocument, *, template=None, figure_bytes: dict[str, bytes]) -> bytes`: title, generated-from line (project, evidence hash, profile, version), executive summary, then sections in order. `Paragraph` blocks render the markdown inline subset (bold, italic, code, links) as runs; `Bullets` → `List Bullet`; `TableRef` → the table from `doc.tables` in `Table Grid` with a caption; `FigureRef` → inline picture + caption; `Callout` → `Disclosure` style (disclosure), `Gap` style (evidence gap), or a "Not established:" sentence; `Field` → bold label + value.
- [ ] Each section heading carries a bookmark named `sec:<section_id>` (the round-trip anchor for increment 3).
- [ ] Unverified numbers appendix: "Numbers to check" listing `audit.unverified` per section when non-empty.
- [ ] WP0's `render_reference_design_docx` becomes a thin adapter: build a code-only `ReportDocument` from the evidence tables and render it — one writer, decision 16's spirit.

**Acceptance**
- [ ] A `ReportDocument` with every block type renders; the docx re-opened with python-docx has the headings in order, the bookmarks present, the tables with the right row counts, one picture per `FigureRef`.
- [ ] A section with `audit.unverified` non-empty produces the appendix; with all sections clean there is no appendix.
- [ ] WP0's golden and populated tests still pass through the adapter.

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

**Steps**
- [ ] `api/reports.ts`: list, get, status (poll 1.5 s while running, like `GridspinePanel`), generate, regenerate, abort, export (`POST …/export` returning the agent-export meta; the chip appears in the chat strip and a direct `<a download>` is offered in the panel using the upload blob URL).
- [ ] `ReportsPanel`: list with version, profile, evidence status ("evidence changed since v3" when `evidence_hash` ≠ current collector hash from `GET …/evidence_hash`), Generate button with sections + language, progress bar while running, abort.
- [ ] `ReportViewer`: sections in order; `Paragraph`/`Bullets` through `ChatMarkdown`; `TableRef` as a real table; `FigureRef` as `<img>` from the figure route; `Callout` as tagged boxes; unverified numbers highlighted with a tooltip "not found in the evidence"; per-section "Regenerate…" opens a one-line instruction input; `not_established` sections render their note.
- [ ] Buttons on the EH panel and the Adequacy tab navigate to the Reports panel with generation pre-armed.
- [ ] Desktop: the download uses the anchor-download pattern that `backend/desktop/downloads.py` documents.

**Acceptance**
- [ ] Viewer renders a fixture document with every block type; unverified numbers are visibly marked; a `not_established` section shows its note.
- [ ] Generate → running → done transitions drive the list and the viewer without a reload.
- [ ] The Regenerate flow posts the instruction and shows the new version.
- [ ] `npx vitest run` green; `tsc -b` clean.

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
