<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 Model import pipeline: one route, a preview in plain names, then a new project (PSS/E, DGS, PLEXOS)

Status: ready-for-agent
Blocked by: none (follows the pattern of #1798's `routers/workbook.py`; if #1798 has not merged, rebase on it before the UI slice)
Owner decisions: IMPORTS-1, IMPORT-FLOW-1 (preview first, never an overwrite), HOLISTIC-1, RAW-NAMES-1, PR-SIZE-1, PLEXOS-SCOPE-1
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W3; `project-io/spec.md` item 4, never filed until now.
Model: opus. Review: GK A (importer seam) + GK B (routes, locks) + UX (preview).

## Problem

The PSS/E RAW (`psse-import/01`, done), PowerFactory DGS (`dgs-import/01`, in progress) and PLEXOS (`project-io/plexos-mapping.md`, slices 1–3a merged) readers exist only in `gridspine/ingest/`. Each returns an `ImportResult` and is listed in the registry in `gridspine/ingest/import_result.py` (`importers()`, `for_file()`, `read()`). Nothing in `pypsa-gui/backend` calls them, and no screen offers them. In the walk, dropping `case14.raw` on the Project Data modal of a blank project showed no message at all. New project › Import file accepts only `.pypsaproj.zip` and `.nc` (`NewProjectWizard.tsx` ~480).

## User-visible impact

A user with a PSS/E, PowerFactory or PLEXOS model cannot bring it into PyPSA Studio, although three importers are built and tested. Every merged importer slice stays invisible.

## What

**Slice A, backend** (one PR, no UI):
- `GET /api/model-import/formats`: the registry's formats (id, plain label, extensions), read from `importers()`, so a new importer appears without editing a list.
- `POST /api/model-import/preview` (files; `options` such as the PLEXOS Model choice): runs `import_result.read()` in a worker and writes nothing. It returns `{sha256, format, suggested_name, counts, unmapped[], assumed[], suggestions[], options_needed[]}`, all in plain names (RAW-NAMES-1). An unknown extension or a reader refusal is a 422 with the reader's plain reason.
- `POST /api/model-import/import` (files + `expected_sha256` + `name?` + the same `options`): runs every check again and creates a **new** project, all or nothing, the same way `services/excel_workbook.create_project()` does in #1798. It writes `network.nc`, the sidecars and `import_report.json`, and is then subject to `project-io/01`'s contents table. It never overwrites a project (IMPORT-FLOW-1).
- Fragments: write-surface lock policy, route chat disposition (`not_via_chat.d` until 05 or a chat tool lands), tool-error kinds, golden route surfaces. Add a viewer-role row to match `project-viewer-role/04` (#1798): creating a project needs a workspace where the caller may create.
- Caps: a stated upload size cap and a reader time-out. A worker crash is a plain error, never a 500 or a half-made project.

**Slice B, UI** (one PR):
- New project › Import file accepts every registry extension, plus what the Project Data modal already takes (`.xlsx`, `.m`). The Blank tab's "switch to the From file tab" matches the tab's real name.
- A preview step before the project is created: counts, then "Not carried" (unmapped, with reasons) and "Assumed" (with why) as full lists, plus the PLEXOS Model picker when `options_needed` asks for it. "Create project" uses `expected_sha256`.
- After a raw `.xlsx`/`.nc` import into the Project Data modal (`ImportExport.tsx` ~133), the sidebar no longer reads "No project loaded" with the Guided template picker. It says the network is not saved yet and offers "Save as project". The Excel export file is named after the project, not `unnamed_network.xlsx` (`Sidebar.tsx` IoModal ~106).

## Acceptance criteria

- [ ] `GET /api/model-import/formats` lists psse, dgs and plexos from the registry. A test registers a fake importer and sees it appear with no route edit.
- [ ] Preview of the `psse-import` fixture (`case14.raw` or the tests' fixture), a DGS fixture and a PLEXOS fixture returns counts, unmapped and assumed in plain names, and creates no project and no folder (tested).
- [ ] Import with a stale `expected_sha256` is a 409. A reader failure after staging leaves no row and no folder. Two concurrent imports with one name: one wins, the other gets a clean 409.
- [ ] The UI preview shows the unmapped and assumed lists in full, never as a count only. No raw field code appears (RAW-NAMES-1 guard).
- [ ] A raw import shows "Save as project", not "No project loaded". The export file name is the project's.
- [ ] End to end in the real app: New project › Import file › pick `case14.raw` › preview › Create project. The project opens, its buses and lines show in Expert, Run LOPF completes, and the import report is reachable from the project. Repeat with one DGS and one PLEXOS fixture. Shots in the PR.

## Likely files

- new `pypsa-gui/backend/routers/model_import.py`, new `pypsa-gui/backend/services/model_import_service.py`, `pypsa-gui/backend/main.py` (mount)
- `gridspine/ingest/import_result.py` (read only, unless the registry needs a helper)
- `pypsa-gui/frontend/src/layout/NewProjectWizard.tsx`, `pypsa-gui/frontend/src/pages/ImportExport.tsx`, `pypsa-gui/frontend/src/layout/Sidebar.tsx` (IoModal), a new `pypsa-gui/frontend/src/api/modelImport.ts`
- fragments under `pypsa-gui/backend/tests/write_surface_lock_policy.d/`, `services/not_via_chat.d/`, `tool-error-kinds.d/`, `tests/golden_route_surfaces.d/`

## Comments
