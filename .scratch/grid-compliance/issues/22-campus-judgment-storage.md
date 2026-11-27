<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-22 Campus service: store and serve the pack read-back judgment

Status: done (#2094, 0a4f7e26)
Type: task
Parent: `.scratch/grid-compliance/spec.md` (follow-up of GC-07 part 5, pack read-back)
Gated: yes (GK B, backend)
Owner: GC-09 (compliance workflow) or a new session; read-only use of GC-07's output

## Why

GC-07 part 5 says read-back results "land in the run directory and the campus service". The
pack read-back PR does the run directory only: `gridspine.drivers.readback.judge_pack_results`
writes `<run_dir>/compliance/judgment.json` and `judgment.csv`. The campus service
(`pypsa-gui/backend`) neither stores nor serves the judgment, so the app's run → results →
report journey for grid-compliance studies stops at a file on disk, and GC-09's tracker cannot
mark a deliverable "judged" from it.

## What to build

0. **The trigger is this issue's.** Nothing calls `judge_pack_results` yet: no route, job or
   UI action runs the read-back. This issue adds the call (when a pack's results are added to
   a campus run, or on an explicit "judge pack" action) as well as the storage below.
1. Backend: after a pack read-back, record the judgment against the campus run it was judged
   for (the pack manifest's `data_hash` and contract version, the ratings and their source, the
   per-case status and rows), reading `judgment.json` as written by GC-07, not re-judging.
2. Serve it on the campus run's results (case summary per J8 and the `status_counts` totals),
   so the report and GC-09's tracker can read it.
3. A re-run of the read-back replaces the stored judgment, as it replaces the files.

## Done when

- The read-back runs from the app on a campus run's pack; a test drives it end to end through
  the trigger, not by calling `judge_pack_results` directly.
- A judged pack's judgment is stored against its campus run and served, with the data hash,
  contract version and rating sources it carries; a test reads back exactly what
  `judgment.json` holds.
- A re-run replaces it; an unknown case stays unknown (no status is upgraded on the way).
- GC-09 can mark a deliverable "judged" from the stored judgment.

## Comments

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Acceptance is the 'Done when' section above. Still true: `gridspine.drivers.readback.judge_pack_results` writes only to the run directory and the backend does not store or serve the judgment. GC-07 is done. Overlaps GC-09 (09-compliance-workflow-ui, backend open in #1577): check that PR first and reuse its route; if it already serves the judgment, set this done. Gated: GK B. Blocked by: none.

## Follow-ups (2026-11-27, PR #2094)

- An "Upload EMT results" UI for each PSS/E bundle, as its own PR after #2094
  merges (GK ruling). UX 32's rules for it: the counts read
  "2 pass · 1 fail · 3 not judged", each not-judged row shows its reason as
  visible text, and each chip has a visible key.
- Set this issue's Status to done when #2094 merges.
