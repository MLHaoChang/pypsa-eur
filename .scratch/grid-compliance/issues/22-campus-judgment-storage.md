<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-22 Campus service: store and serve the pack read-back judgment

Status: needs-triage
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

1. Backend: after a pack read-back, record the judgment against the campus run it was judged
   for (the pack manifest's `data_hash` and contract version, the ratings and their source, the
   per-case status and rows), reading `judgment.json` as written by GC-07, not re-judging.
2. Serve it on the campus run's results (case summary per J8 and the `status_counts` totals),
   so the report and GC-09's tracker can read it.
3. A re-run of the read-back replaces the stored judgment, as it replaces the files.

## Done when

- A judged pack's judgment is stored against its campus run and served, with the data hash,
  contract version and rating sources it carries; a test reads back exactly what
  `judgment.json` holds.
- A re-run replaces it; an unknown case stays unknown (no status is upgraded on the way).
- GC-09 can mark a deliverable "judged" from the stored judgment.

## Comments
