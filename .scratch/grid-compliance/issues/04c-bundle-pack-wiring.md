<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-04c: wire the campus bundle's PSS/E pack to the GC-01 plant and model set

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/issues/04-psse-pack.md`
Gated: yes (GK A2, gridspine)
Depends on: GC-01's freeze (#1595)

## Why

`campus_bundle.py` calls `write_psse_pack` once, with no `plant`, `model_set` or `variant`.
A pack written by the campus bundle today therefore has no plant controller (REPCA1), no
psspy scripts and no GC-01 citation. The pack's README and manifest say so
(`plant_controller_not_written`, `scripts_not_written`), but nothing fills the gap.

## What to build

1. Once GC-01's freeze lands (#1595), have the bundle build or receive the aggregated plant
   (`Plant`: its converter's unit id and PoC branch) and the frozen model set with its
   variant.
2. Pass them to the bundle's one `write_psse_pack` call, so a bundle-written pack carries
   REPCA1, the psspy scripts and the model-set citation.

## Done when

- A pack written by the campus bundle for a campus with an aggregated plant holds one
  REPCA1 on the PoC branch, one psspy script per test-battery case, and the manifest's
  `model_set` citation; `plant_controller_not_written` and `scripts_not_written` are null.
- The bundle's own `.raw`, `.dyr`, README and GFM table stay byte-identical.
