<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 29 Campus run: harmonic emission screen, with the background harmonics at the PCC

Status: ready-for-agent
Type: task
Parent: `.scratch/grid-compliance/spec.md` (GC-05 output; split from 09 item 6)
Gated: yes (GK A2 (8) conditions below, + UX)
Source: owner decision GC09-HARMONICS-1 (2026-11-27)
Likely files: `pypsa-gui/backend/services/campus_electrical_service.py`, `pypsa-gui/backend/routers/campus_electrical.py`, `pypsa-gui/backend/services/_project_write_rules.py`, a `not_via_chat.d/` and a `tool-error-kinds.d/` fragment, `pypsa-gui/frontend/src/components/gridReadiness/ConnectionLimits.tsx` + tests, backend tests under `pypsa-gui/backend/tests/`

## Why

GC-05 built the harmonic emission screen (`gridspine.drivers.campus_study.harmonics_campus`)
and the import of a field measurement of the background voltage harmonics at the PCC
(`campus_study.import_background_campus`). Nothing in the app runs `harmonics_campus`: the
campus run has no harmonic emission step and the results show no emission rows. A background
upload built now would have no consumer. The owner ruled (GC09-HARMONICS-1) that 09 item 6
ships only the PowerFactory DGS export, and that the background upload lands here, together
with the screen that reads it.

## What to build

1. **The harmonic emission screen in the campus run.** Call `harmonics_campus` from the campus
   run (`campus_electrical_service.run`), record its output in the run directory, and show its
   rows in the campus results. A run without a background measurement still screens, and says
   it had none.
2. **The background-harmonics upload, landing with item 1.** Store one field measurement per
   campus (CSV of harmonic order and % of the fundamental, with `source` and `measured_on`),
   checked at upload against the campus PCC; the next run takes it into the screen through
   `import_background_campus`, and the results show the "with background" row. GET, PUT and
   DELETE routes, a not-via-chat entry (chat cannot take a file), the write-rule rows and the
   error kinds.
3. **Placement (UX 32).** The background upload goes in the connection inputs
   (`pypsa-gui/frontend/src/components/gridReadiness/ConnectionLimits.tsx`), not in Results.

## GK A2 (8) conditions for the upload (recorded in 09, 2026-11-27)

- D4. Bytes are stored under a fixed name; the client filename is metadata only. A named size cap, tested. `measured_on` is an ISO date; `source` is non-empty and length-capped; bad input gets 422. The write goes to a per-call temp file, then a rename. The whole read-modify-write runs inside the sidecar lock, and DELETE takes the same lock.
- D5. If the stored background no longer parses against the campus at run time (e.g. a renamed PCC bus), the run never silently scans without it: the "with background" row reads unknown with the reason. Tested with a PCC rename after upload. This needs `harmonics_campus` wired into the run.
- D6. The stale check pins a hash of the whole sidecar record (bytes sha256, source, measured_on, filename). Change and removal are both tested.
- D7. A viewer gets 200 on the GETs and 403 on any write route (PUT and DELETE). Not-via-chat entries cover the upload. Run the route-parity, chat-disposition and project-ACL semantic tests before reporting.

## Prior draft

A drafted implementation of the upload lived on branch `claude/gc09-powerfactory-pack` at
3d825b61 and was removed from that branch per GC09-HARMONICS-1: `get_background`,
`put_background`, `delete_background`, `_take_background`, `_background_pins` in
`campus_electrical_service.py`, the `GET/PUT/DELETE /{name}/background-harmonics` routes in
`routers/campus_electrical.py`, their write-rule rows, a not-via-chat entry and four
`campus_background_*` error kinds. Recover it with `git show 3d825b61:<path>`. It was not
reviewed against D4 to D6 (its stale pin, for one, covered the bytes sha256 only), so check it
before reuse.

## Done when

- A campus run screens harmonic emission and its rows show in the results, with and without a
  stored background measurement.
- The upload meets D4 to D7, and sits in the connection inputs per UX 32.

## Comments
