<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-04d: the campus study passes the aggregated plant and model set to its bundles

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/issues/04c-bundle-pack-wiring.md`
Gated: yes (GK A2, gridspine)
Depends on: GC-04c

## Why

GC-04c lets `export_campus_bundle` take `plant`, `model_set` and `variant` and hand them to
its PSS/E pack. Its only production caller, `gridspine/drivers/campus_study.py`
(`export_campus_bundle(...)` in the island-hour loop), still passes none of them. A study run
therefore still writes packs without REPCA1, the psspy scripts or the GC-01 model-set
citation. Nothing user-visible changes until this lands.

## What to build

1. Decide where the study gets the aggregated plant from: its converter's unit id and its
   PoC branch (`psse_pack.Plant`, `dyr_writer.PocBranch`). The plant must come from
   campus data or the caller. A guessed branch would be invented data.
2. Decide where the study gets the frozen GC-01 model set and its variant from
   (`schema.model_set`).
3. Pass all three to each `export_campus_bundle` call.

## Done when

- A campus study run whose campus has an aggregated plant writes bundles whose packs hold
  REPCA1, one psspy script per test-battery case, and the `model_set` citation.
- A study run without a plant writes byte-identical bundles to today's.
