<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-04e: a campus declares its plant

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/issues/04d-campus-study-passes-plant.md`
Gated: yes (GK A2, gridspine)
Depends on: GC-04d

## Why

GC-04d lets `psse_campus` take `plant`, `model_set` and `variant` and pass them to every
bundle, but no production code calls `psse_campus` with them (today only tests call it at
all). A real study run therefore still writes packs without REPCA1, the psspy scripts or the
GC-01 citation. Nothing user-visible changes until this lands.

## What to build

1. An explicit campus key, e.g. `pcc.plant: {unit, transformer}`, resolved at ingest to a
   `psse_pack.Plant` with its `dyr_writer.PocBranch`. Refused with a field path when the unit
   is not a grid-following converter or the transformer does not touch the PCC bus. The
   branch is declared, never inferred (a guessed branch would be invented data).
2. A model-set store: where a frozen GC-01 `ModelSet` lives for a project (today `freeze`
   is in memory only, and the GUI's `grid_compliance` freeze is an interim id, not a
   gridspine `ModelSet`), so the study can load it and its variant.
3. The GUI wiring: the backend stage that runs the PSS/E handoff calls `psse_campus` with
   the declared plant and the stored model set.

## Done when

- A study run from the app on a campus that declares its plant writes packs with REPCA1,
  one psspy script per test-battery case and the `model_set` citation.
- A campus without the key writes byte-identical bundles to today's.
