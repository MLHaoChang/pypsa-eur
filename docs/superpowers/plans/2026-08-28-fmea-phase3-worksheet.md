# FMEA Phase 3 — Worksheet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox (`- [ ]`) steps.

**Goal:** The formal deliverable (spec v4 §§4.2, 8.3): an IEC 60812-shaped FMEA worksheet — computed rows beside expert-entered ones, an editable mitigability column, persisted per project and **surviving re-solves**, provenance-badged, CSV-exportable. €/yr criticality is the ranking; **no RPN, no Action Priority** (decided v2).

**The architecture that retires the schedule risk.** The "editable results table with no codebase precedent" dissolves into three small, precedented pieces:

1. **Computed rows are never persisted.** They regenerate from `GET /results/copt` (foreground network) on every view — a re-solve simply changes what comes back.
2. **Manual state is a per-project JSON sidecar** (`adequacy_worksheet.json` in the project directory): class-D rows (fully user-authored) + **overlays** keyed by `mode_id` (mitigability/notes) that re-attach to computed rows after regeneration. Plain JSON via `services/atomic_io.atomic_write_text` — no pickle, human-diffable; routes under `/api/projects/{name}/worksheet` with `ProjectAccessDep` (the `compare-state` authorization pattern).
3. **The merge is client-side**: computed (already foreground) + sidecar (named project = the active one on the Results tab). The server never mixes foreground network state with on-disk project state.

**Contract addition:** expert-entered rows carry provenance too — `Engine` gains `"expert"`, `Fidelity` gains `"expert_judgement"` (models/adequacy.py). Honest labelling, not a loophole: the UI badges them exactly like engine rows.

## Global Constraints

Phase 0–2 constraints apply (branch, staging, test-first with demonstrated red, pixi note, the two environmental failures, the two PR #4 xfail gates). Sidecar hygiene: schema-versioned envelope `{"__schema__": 1, ...}`, size-capped fields (mitigability/notes ≤ 2000 chars, ≤ 200 manual rows), reject-don't-truncate.

### Task 1: the sidecar service + routes

- [x] **Failing tests first** (`tests/test_adequacy_worksheet.py`): round-trip save/load through the routes; overlays re-attach by `mode_id` (save overlay → "re-solve" simulated by nothing at all, since computed rows aren't stored → GET returns the overlay untouched); manual rows validate as `FailureModeResult` (engine `expert`, fidelity `expert_judgement`, class `D`) and invalid rows 422; size caps enforced; missing sidecar → empty state, not 404; atomic write (no partial file on a writer exception).
- [x] Add the two contract literals; implement `services/adequacy/worksheet.py` (load/save/validate) + `routers/` routes `GET`/`PUT /api/projects/{name}/worksheet` (PUT replaces the whole manual state — payloads are small; echo a monotonically increasing `version` for the UI's last-write-wins awareness).
- [x] Commit: `feat(gui): per-project FMEA worksheet sidecar (manual rows + overlays)`.

### Task 2: the worksheet tab

- [x] **Vitest first** (`fmea.test.tsx` on extracted pure pieces): `mergeWorksheet(copt, sidecar)` — computed rows get their overlay's mitigability, manual rows append with an `editable` flag, ranking by criticality with computed/manual interleaved; `worksheetCsv(rows)` — column order per IEC 60812 shape (mode, class, occurrence+basis, severity €, criticality €/yr, mitigability, engine/fidelity), values escaped; badge variants per engine.
- [x] Implement `pages/results/FmeaTab.tsx`: merged table via `useFilterableTable` (sort/search), the mitigability cell as the ONE editable cell on computed rows (debounced PUT), an "add manual failure mode" form (name, description, occurrence/yr, severity € — criticality computed as the product, per the f×S identity), per-row delete for manual rows, `downloadCSV` export, provenance badge per row, the fidelity disclaimer line.
- [x] Register the tab: the five coupled edits in `pages/Results.tsx` (union, `VALID_TABS`, `TABS`, render switch, the exhaustive `Record<ResultsTab, CompareTab>` → alias `'overview'` like `asset`); `api/simulation.ts` `getWorksheet`/`putWorksheet`.
- [x] `tsc -b` + full vitest; commit: `feat(gui): the FMEA worksheet tab`.

## Done criteria

A user can open Results → FMEA, see the ranked computed class-A rows with provenance badges, annotate any row's mitigability, add and delete class-D expert rows, export the sheet as CSV — and everything manual survives a re-solve and a project reload. Backend + frontend suites at baseline or better.
