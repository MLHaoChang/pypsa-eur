<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Design plan: the data-centre business case

Status: done (#1316; GK B and GK A plan GO, owner DCBC-SCOPE-1, 2026-11-10)
Type: task
Blocked by: —
Gated: yes (GK B finance plan GO; GK A for the DC side)
Model tier: opus
Size: S (docs only)

Read [the spec](../spec.md) first.

## What this PR holds

The spec, the research note and the slice files 02–07. No code.

## Done when

- GK B has given a plan GO on spec §3–§6 (bases B1–B8, the new streams, the P&L
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

## Comments

### 2026-11-10: GK B (39) plan GO, conditions BC1–BC10

Answers: (1) a separate run, not a flag on the IC run; (2) gross pass-through OK;
(3) working capital in the cash bridge only, not a `STREAM_CLASS` row. Slice 04's
TDD plan goes to GK B before it is built.

- BC1 The IC report, its staleness hash and every IC/manifest digest are byte-identical
  with the BC code present; the BC result has its own hash domain; a pin test that
  the other digest domains do not change.
- BC2 Pass-through margin 0 leaves EBITDA unchanged; EBITDA margin and revenue per MW
  IT are shown both gross and net of pass-through.
- BC3 Working capital never enters EBITDA, EBIT, tax or net income (pin); it is
  released at end of life, so with a zero discount rate the changes sum to 0 (test).
- BC4 Standalone mode clips per year (`engine.py:184`: op_inc revenue = max(inc_net, 0),
  costs = max(−inc_net, 0)). Build the P&L from the per-stream operating ledger (op by
  `STREAM_CLASS`), never from op_inc's revenue/costs; EBITDA ties to net_total per year.
- BC5 (B7) The P&L tax line is the engine's tax, with no second tax computation; each
  year ties to `project_post_tax` and `equity_post_tax` within a stated tolerance, with
  a mutant.
- BC6 (B4/L9) Revenue reads served IT energy only; unserved earns 0 and is a quantity;
  a test that unserved IT energy lowers revenue and that VoLL appears nowhere in the BC.
- BC7 (B5) A missing revenue price, GPU-hour revenue without IT-hardware capex, or
  colocation without facility capex is a typed refusal, never 0 or a default; each has
  a fail-closed test at the catching site. Unit tests use real units (kW-month vs
  kW-year, GPU-h).
- BC8 The break-even solve brackets its root; no root in the bracket is a typed refusal,
  never an extrapolation. With no debt, DSCR reads n/a, never inf or 0.
- BC9 New depreciation classes or cost defaults that change the defaults pack follow
  the AS-02 ruling: list the unpinned callers of `load_defaults_pack()` and show the
  previous version's rows byte-equal. Researched non-energy defaults carry a source
  and an `assumed` status.
- BC10 (slice 05) The route and chat tools run the route ACL, `route_chat_disposition`
  and write-surface lock tests, the error-kind fragment and the inventory,
  route-parity and control-matrix `--check`s; the route reads solver config through
  `_solver_config_from_dict` (#1009); the workbook sheet follows RW-KPI-1.

### 2026-11-10: GK A (19) plan GO for the DC side

Conditions BC3-1..8 are in [03](03-it-energy-series.md) ## Comments.
