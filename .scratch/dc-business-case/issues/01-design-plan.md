<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Design plan: the data-centre business case

Status: needs-info (plan out to GK B and GK A; owner scope answers pending)
Type: task
Blocked by: —
Gated: yes (GK B finance plan GO; GK A for the DC side)
Model tier: opus
Size: S (docs only)

Read [the spec](../spec.md) first.

## What this PR holds

The spec, the research note and the slice files 02–07. No code.

## Done when

- GK B has given a plan GO on spec §3–§6 (bases B1–B7, the new streams, the P&L
  reconciliation) and GK A on §2 facts 1–2 and §4's IT energy series.
- The owner's answers to spec §9 Q1–Q4 are recorded in the spec and the slices
  are adjusted to them.
- This PR merges.

## Questions for the reviewers

1. GK B: is a second `run_case` on `dataclasses.replace(ic_case, counterfactual=(), …)`
   the right reuse, or should the business case be a flag on the IC run (one
   job, two results)? The plan prefers a separate run so the IC hash never moves.
2. GK B: B6 pass-through presented gross (revenue and cost both shown). Net would
   hide the bill from the revenue line; gross matches how colocation operators
   report metered power (research §4).
3. GK B: working capital as a new stream versus a cash-only adjustment outside
   EBITDA. The plan puts it below EBITDA, in the cash bridge only.
4. GK A: revenue reads IT MW × u(t) from `dc_load_profile`, less the unserved
   share; is "unserved IT share" = involuntary slack energy × critical share on
   `it_bus` the right read, or should the whole facility shortfall scale IT?
5. GK A: does any DC engine path (DC 06 flex, DC 07 curtailment, workload shift)
   move IT energy in time such that the revenue must read the post-solve series
   rather than the spec? (`workload_shift_window_h` looks like one.)
