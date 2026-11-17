<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-13 A running genset in the strength fault level (S_k''min)

Status: needs-triage
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
