<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-09 Compliance workflow: deliverable tracker, data list, TSO revision log, report pack

Status: ready-for-agent; audit 2026-11-25: backend (#1577) and Grid compliance UI section (#1854) merged; left: item 6 (DGS export download and background-harmonics upload wiring)
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none (tracker, data list and revision log start at once; pack rows light up as 01–08 land)
Gated: **yes** (GK B + UX)
Model tier: opus
Size: L (backend PR, then UI PR)
Owner decision: GC-SCOPE-1, GC-TEXT-1 (2026-11-17)
TRGC: all 01–25 (tracking); generates 09, 10, 11
Likely files: new `pypsa-gui/backend/services/grid_compliance/` (tracker, data list, revision log, pack assembly), new `pypsa-gui/backend/routers/grid_compliance.py`, `pypsa-gui/backend/services/campus_electrical_service.py` (shared; read-only use of outputs, plus the GC-05 entry points in item 6), `pypsa-gui/backend/services/reports/` (reuse docx/pdf writers and `revisions.py`), new `pypsa-gui/frontend/src/components/GridCompliance*.tsx` + tests, backend tests under `pypsa-gui/backend/tests/`

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
6. **PowerFactory pack (GC-05 output)**: GC-09 wires the DGS export download
   (`campus_study.powerfactory_campus`) and the background-harmonics upload
   (`campus_study.import_background_campus`) into `campus_electrical_service.py`; GC-05 (#1580)
   left no app entry point.

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

## Comments

- 2026-11-17, GK B2 plan GO for the backend slice, with conditions:
  - G1. The interim freeze hash covers the network's LP inputs (`_network_input_fingerprint`),
    not only the campus files.
  - G2. A freeze made by another method reads `refreeze_needed`. One that can't be checked reads
    `unreadable`. Neither ever reads fresh.
  - G3. Moving to submitted or beyond also needs a fresh freeze (`gc_freeze_not_fresh`).
  - G4. Statuses are user-declared (`status_basis: declared`). A pending source never reads as
    judged.
  - G5. Both guards check and write under the tracker's file lock. Each guard has a race test.
  - G6. An unreadable tracker gets a typed refusal and is never overwritten.
  - G7. Comment ids are assigned under the lock. A concurrency test covers it.
  - G8. The manifest records each source's state and any PDF skip. The no-identity check also
    covers member names and the download name. Free-text fields are capped.
  - G9. Route-policy tests at the trial merge. Every write route has a foreign-lock test. Every
    `{code}` route has an unknown-deliverable 404 test.
- Revision log: reuses `RevisionRef` from `services/reports/revisions.py` to tie a response to
  its pack. It does not reuse the report anchors.
- Chat disposition: UI_INTERNAL group, cap 11 to 12, with follow-up GC-10
  (`10-assistant-tools.md`), owner decision GC09-CHAT-1.
- 09/10/11 user guides and datasheets: their own skeleton sections now. Content arrives from
  GC-01/04/06 through `sources.SOURCES`.

### GK B2 plan GO, slice (b) UI (2026-11-17), with conditions

- U1. The types follow the server's return shapes (rule #1060). Fixtures are built from a real #1577 response, kept in sync by a backend test that dumps the view, never hand-made shapes (rule #1427). This covers `since_submission`, `submissions`, `feeds[].state`, `status_basis` and the `gc_*` error bodies.
- U2. A 409 `gc_tracker_unreadable` read shows that state with every write control disabled and a reason. It never shows an empty table or a "start" action. Tested.
- U3. Every write refetches from the server, with no optimistic updates. A refused status move keeps the old status and shows the server's text. Tested.
- U4. Every refusal kind reaches the user as text, never swallowed by `quiet`. The lock-held and `gc_invalid` (with its field) cases are tested.
- U5. The pack download uses the server's filename and bytes. The button is disabled while the pack builds, and a failed build shows the server's text.
- U6. The screen shows no hash ids or file paths. The test covers `model_set_id` and `data_hash`.
- A targeted mutant runs on each guard's test, and the PR body lists them. The PR opens after #1577 merges.

- 2026-11-23 status sweep: landed in #1577 (0578ffac); #1854 (6cf22fbb): backend and Grid compliance UI section. Remains: item 6 (DGS export download and background-harmonics upload wiring).

## Audit (2026-11-25)

- 2026-11-25 audit: #1577, #1854 in master log; campus_study.powerfactory_campus and import_background_campus have no caller in campus_electrical_service.py or routers.

## Item 6 plan GO conditions (GK A2 (8), 2026-11-27)

GC-22's "Upload EMT results" UI stays out of the item 6 PR (its own PR after #2094).

DGS part:
- D1. The PSS/E listing and zip behaviour stay unchanged; the existing PSS/E tests pass without edits; one test checks that the PSS/E zip's file list and bytes on the fixture equal master's.
- D2. A failed PowerFactory step lists no bundle (staged then swapped in, or removed on error). The listing reads `powerfactory_status.json` first: on error it returns `{error}` and no bundles; with no status file (an older run) it returns `{has_powerfactory: false}`, never a raw error. All three are tested.
- D3. A bundle whose manifest has `not_written` > 0 or `pending_verification` > 0 never reads as complete. The listing and the UI show the counts beside the download in a plain line ("N elements not written; see the manifest"). The listing is tested with a nonzero count.
- D7. A viewer gets 200 on the GETs and 403 on any write route. Not-via-chat entries cover the downloads. Run the route-parity, chat-disposition and project-ACL semantic tests before reporting.
- D8. The DGS section and its text go to UX (32). User-facing text carries no GK, ruling or issue refs.

Background upload (only if the owner rules it is built here):
- D4. Bytes are stored under a fixed name; the client filename is metadata only. A named size cap, tested. `measured_on` is an ISO date; `source` is non-empty and length-capped; bad input gets 422. The write goes to a per-call temp file, then a rename. The whole read-modify-write runs inside the sidecar lock, and DELETE takes the same lock.
- D5. If the stored background no longer parses against the campus at run time (e.g. a renamed PCC bus), the run never silently scans without it: the "with background" row reads unknown with the reason. Tested with a PCC rename after upload. This needs `harmonics_campus` wired into the run.
- D6. The stale check pins a hash of the whole sidecar record (bytes sha256, source, measured_on, filename). Change and removal are both tested.
- D7 also: a viewer gets 403 on PUT and DELETE.
