<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-13 A running genset in the strength fault level (S_k''min)

Status: done (branch `claude/gc13-genset-sk`: a genset producing in every hour of the period feeds S_k''min as a synchronous `gen` with K_G; GK A ruled A)
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: GC-03 PR2 (`claude/gc03-campus-steps`)
Gated: **yes** (GK A, gridspine)
Size: S

## Why

GC-03's strength step (`campus_study.strength_campus`) takes S_k''min from pandapower's IEC 60909
minimum case. That case adds no current from a static generator, and the campus net carries a genset
as one (k = 1/x''d), so a running genset adds nothing to S_k''min. SCR is then grid-only, which is
conservative; `GENSET_SK_NOTE` states it. GK A2 ruled this acceptable for GC-03 (T2 ruling A) and
asked for this follow-up.

## What to build

In the strength fault net only, carry each genset built in the stage as a synchronous `gen` with its
x''d, R/X and the IEC 60909-0 generator correction K_G, so it contributes to the minimum case when the
study says it runs. Decide (with GK A) whether the minimum case assumes the genset runs at all.

## Done when

- A hand-calculated S_k at a PoC with one running genset matches the step within 0.5 %.
- `GENSET_SK_NOTE` is replaced by a note naming the genset's contribution.
- A genset not built in the stage still adds nothing.

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Acceptance is the 'Done when' section above. Still true: `GENSET_SK_NOTE` still states the grid-only SCR. Blocked by GC-03 PR2: merged (#1589), so unblocked. Gated: GK A. Blocked by: none.

## Comments

**GK A (39), plan GO (2026-11-27), conditions G1–G7.** The minimum case assumes a genset runs only
when it produces in every hour of the period (answer A): S_k''min is the weakest credible state. Counting
a standby set is a follow-up if a user asks for it.

- G1 (running): a genset counts in a period only when p_mw > 0 in every snapshot of that period; a
  missing, NaN or ≤ 0 hour means not running (fail closed); no rows for the period, not counted. Decided
  per period.
- G2 (refactor): `machine_net` stays behaviour-identical after the lift into `genset_machine`; the
  island_sc tests pass unchanged, plus a pin of the gen, trafo and sgen tables built from master.
- G3 (N-1): an outage that cuts the genset's feed carries no genset contribution; an outage elsewhere
  keeps it.
- G4 (hand calculation): S_k'' computed without pandapower; the fixture makes K_G, K_T and the genset
  each move the result by more than 0.5 %. Mutants to kill: K_G dropped, "every hour" to "any hour",
  the running filter removed, the kind-"gen" row dropped.
- G5 (templates): the GENROU row round-trips through `set_sc_params` to the spec's values.
- G6 (notes): a campus with no genset keeps byte-equal strength output, notes included; the note names
  each counted genset; a built genset not counted gets the not-running reason.
- G7 (scope): `campus_fault_levels` and `psspy_scripts.py` unchanged; a release-note fragment.
