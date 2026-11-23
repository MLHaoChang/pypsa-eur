<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-04c: wire the campus bundle's PSS/E pack to the GC-01 plant and model set

Status: done
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

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Acceptance is the 'Done when' section above. Still true: `gridspine/handoff/campus_bundle.py:223` calls `write_psse_pack` with no `plant`, `model_set` or `variant`. Depends on GC-01's freeze: merged (#1595). Gated: GK A2. Blocked by: none.

## Comments

**2026-11-23 GK A2 plan GO, on conditions P1–P6 (verbatim).** "Receive, don't build" is right: a guessed PoC branch would be invented data.

P1. Literal expectations (M6a). Test 1 states the REPCA1 .dyr record, the PCC and PV bus numbers, and the list of run_*.py names, each as hand-written literals. Never derive them from test_battery's case list or the pack's constants. The manifest's model_set is checked two ways:
- against literal fields (the variant id and the model-set id or hash);
- by whole-dict equality with the manifest that a direct write_psse_pack call writes with the same plant/model_set/variant (the writer→bundle contract).
The bundle manifest's psse_pack entry is compared as a whole dict, not field by field.

P2. Byte-identity for existing callers. With plant/model_set/variant omitted, every file the bundle writes (pack subdir included) is byte-equal to master 8a5b1296. Compare against literal sha256 digests you record from master, or master's function; never against the same code run with None (#1773 W1). Test 2 (bundle files identical with and without plant) stays as well.

P3. Pass-through mutants. Drop each of plant=, model_set= and variant= from the write_psse_pack call, one at a time, and swap in a fixed variant. Show the test that kills each in the PR body. A run of scripts/coord/mutants.py does not replace these.

P4. Refusals pass through. Through export_campus_bundle, test both:
- model_set without variant raises ContractError;
- a plant whose unit is not a grid-following converter is refused with the pack's own error class and reason.
The bundle has no try/except around the pack call. If you add one, the refusal must not be swallowed. State in the body what a refusal leaves on disk: the bundle writes its own files before the pack call.

P5. Callers. Name in the body every caller of export_campus_bundle (I see only campus_study.py:663 in gridspine/pypsa-gui) and confirm each is unchanged. File the "campus_study passes a plant" follow-up as an issue and link it.

P6. Nothing user-visible changes until P5's follow-up lands. Say so in the body; no E2E is needed. Add a release note under doc/release-notes.d/.

Follow-up (P5): `.scratch/grid-compliance/issues/04d-campus-study-passes-plant.md`.
