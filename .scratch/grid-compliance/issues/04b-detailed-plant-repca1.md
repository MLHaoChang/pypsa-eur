<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-04b: plant controller (REPCA1) on a detailed plant

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/issues/04-psse-pack.md`
Gated: yes (GK A, gridspine)

## Why

GC-04 PR3 writes one REPCA1 per PoC branch, attached to the aggregated machine (GC-01), and
refuses two machines on one PoC branch: N REPCA1 records that each measure the whole plant's
flow on their own MBASE multiply the plant loop gain by N. A detailed plant (every converter
its own machine) therefore has no plant controller in the .dyr today.

## What to build (options to triage)

1. Per-machine REPCA1 with shared measurement and the gain scaled per machine (Kc / gain
   sharing), stated and tested against the aggregated response.
2. A plant-level master controller driving the machines' references (a WECC plant-level
   model), once its public record layout is confirmed.

## Done when

- A detailed plant with N machines writes plant control whose PoC step response matches the
  aggregated model's within a stated tolerance.
