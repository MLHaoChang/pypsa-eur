<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-09 Compliance workflow: deliverable tracker, data list, TSO revision log, report pack

Status: ready-for-agent
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none (tracker, data list and revision log start at once; pack rows light up as 01–08 land)
Gated: **yes** (GK B + UX)
Model tier: opus
Size: L (backend PR, then UI PR)
Owner decision: GC-SCOPE-1, GC-TEXT-1 (2026-11-17)
TRGC: all 01–25 (tracking); generates 09, 10, 11
Likely files: new `pypsa-gui/backend/services/grid_compliance/` (tracker, data list, revision log, pack assembly), new `pypsa-gui/backend/routers/grid_compliance.py`, `pypsa-gui/backend/services/campus_electrical_service.py` (shared; read-only use of outputs), `pypsa-gui/backend/services/reports/` (reuse docx/pdf writers and `revisions.py`), new `pypsa-gui/frontend/src/components/GridCompliance*.tsx` + tests, backend tests under `pypsa-gui/backend/tests/`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering. TRGC numbers and titles may be named. Never name a project, customer, consultant, EPC or OEM, here, in code, tests, fixtures, UI text or PR bodies.

## Why

The owner wants a workflow that carries a connection through the TRGC chain: what each study
needs, where its inputs come from, which tool runs it, what came back, and what the TSO said.

## What to build

1. **Deliverable tracker**: the 25 TRGC deliverables with phase, dependencies (e.g. 06/07 after
   04/05; 08 after 06 and 07), tool, status (not started / data / exported / results in /
   judged / submitted / TSO comments / accepted), and the package output that feeds each.
2. **Data list per deliverable** with supplier, due date, status; "freeze" calls GC-01's
   `freeze` and stamps the model-set id on the deliverable.
3. **TSO comment / revision log**: per deliverable, comments with id, date, clause, response,
   revision; reuse `services/reports/revisions.py`.
4. **Report pack**: per deliverable a skeleton (DOCX/PDF via the existing report writers) with the
   judged tables (GC-03, 07, 08 outputs), datasheets (11) and model user guides (09/10) generated
   from the model set and pack manifests.
5. **UI**: one Grid compliance section in the campus electrical panel; phase columns, status
   chips, next step visible; follows `pypsa-gui/frontend/src/index.css`, `docs/agents/review-ux.md`
   and DENSITY-1.

## Done when

- A dependency cannot be marked "submitted" while a prerequisite is not "judged" (guard test
  both ways).
- Freeze through the API stores the model-set id; editing input afterwards marks the deliverable
  "stale".
- Revision log round-trips through the API; comment ids are unique per deliverable.
- A generated pack for a fixture lists every file with its hash and contains no project,
  customer, consultant, EPC or OEM name field (GC-TEXT-1).
- Frontend tests cover status rendering, the blocked-by message, and keyboard access.

Release note: one new file under `doc/release-notes.d/` (see its README).
