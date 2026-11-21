<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-04b: plant controller (REPCA1) on a detailed plant

Status: ready-for-agent
Model tier: opus
Blocked by: 12 (package); #1607–#1609
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

## Comments

**2026-11-17 triage (SCALE-1; GK A2 4 priority view).** GK A2 pick: option 1. One REPCA1 per machine, all measuring the same PoC branch, each machine's gains (Kp, Ki, the frequency-droop terms, Kc) scaled by its share MBASE_i/ΣMBASE, so the summed gain equals the aggregate's single loop gain, not N×. Test = "Done when": the PoC step matches the aggregated model within a stated tolerance, plus N=1 byte-equal to today's aggregate record. Option 2 refused for now (record layout unconfirmed; GC04-DEFAULTS-1 admits public generic models only); all values stay tagged assumed. The pick came from GK A2 4; tell GK A. Gated GK A.
