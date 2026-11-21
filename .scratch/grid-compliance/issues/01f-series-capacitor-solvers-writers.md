<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-01f Series capacitors in the fault solvers, screens and writers

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none (follows GC-01b)
Gated: **yes** (GK A2, gridspine)

## Why

Under GK A2's condition S3, every path that walks only `net.line` / `net.trafo` refuses a campus with a
series capacitor and names it (`static/series_caps.refuse`):

- `loadflow.branch_keys`, which every contingency, LODF and planning screen uses;
- `island_sc._branches` (island fault levels);
- `island_sc_seq.sequence_net` (unbalanced faults, earthing);
- the PSS/E RAW, sequence and PowerFactory DGS writers;
- the PyPSA nodal export;
- the EMT pack (`write_emt_pack`; its workbook has no series-capacitor sheet).

Not guarded today, so GK A2 (#1656) added them here:

- the cable-flow reports that read only `net.line`;
- `dyr_writer`, which is reached only after the RAW writer, so the RAW refusal covers it today.

## What to build

One slice per path:

- Model the capacitor in the solver. In fault paths a `bypassed` capacitor is a short (IEC 60909-0).
  An N-1 outage of a capacitor means bypass, never opening.
- Write it in each export format (RAW: series-compensation branch; DGS: ElmScap; SEQ: matching
  record).
- Then lift that path's refusal.
