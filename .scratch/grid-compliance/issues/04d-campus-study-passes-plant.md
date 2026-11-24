<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-04d: the campus study passes the aggregated plant and model set to its bundles

Status: done
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

## Comments

Plan (builder session_01G7qCum626VAVHdtF89LuGH, 2026-11-24), GO'd as option A by GK A2:

- P1. `psse_campus(run_dir, hours=None, *, plant=None, model_set=None, variant=None)`: keyword-only, passed unchanged to every `export_campus_bundle` call.
- P2. The plant comes from the caller ("receive, don't build", GC-04c); no campus-YAML key, no inference from the PCC transformer.
- P3. The model set comes from the caller; before any bundle is written, `model_hash` of the run's campus spec must equal `model_set.model_hashes[variant]`, else ContractError.
- P4. A refusal leaves no partial `psse/` tree.
- P5. Tests: REPCA1, scripts and citation with a plant; byte-identity without; each refusal writes nothing.
- P6. Release note; 04e filed for "a campus declares its plant".

GK A2 GO conditions, verbatim:

Q1. Literals (M6a). Test (a) states the REPCA1 record, the bus numbers and the script names as hand-written literals, as in #1922. The manifest's model_set entry is compared as a whole dict against what a direct write_psse_pack call writes with the same arguments. Run at least two hours, and assert every bundle carries the plant, not only the first.

Q2. The P3 guard.
- model_hash comes from the same function freeze() uses (import it; never a second copy). It runs on the exact campus spec load_run_campus reads for that run.
- Each failure is a ContractError naming the model-set id and variant, never a KeyError or a raw IO error:
  - a variant missing from model_set.model_hashes;
  - a run dir whose campus.yaml is missing or unreadable;
  - a hash mismatch.
- Test each. Show that the mutants comparing against the wrong variant's hash, and skipping the check, die.

Q3. One rule, one place. The plant-unit pre-check calls psse_pack's own rule. If it's inline there, factor it into a function both call, and don't copy it. If psse_pack already refuses on the first hour before it writes anything, drop the pre-check, and show by test that a refusal leaves no psse/ tree.

Q4. Atomicity. Each refusal in (c) asserts that no psse/ directory, or nothing under it, exists after the call. Say in the body what happens to an earlier psse/ tree from a previous call: kept, replaced, or mixed. If "mixed" is possible, refuse or clear it, with a test.

Q5. Byte-identity (b). With no plant/model_set/variant, every file psse_campus writes equals literal sha256 digests recorded from master 0fba5e80 (#1773 W1). Compare the whole set of written paths with the digest keys, so no new file escapes.

Q6. Pass-through mutants. Drop each of plant=, model_set= and variant= at the export_campus_bundle call, one at a time, and swap in a fixed variant. Name each killer in the body.

Q7. Body and docs. Name every caller of psse_campus (tests only today) and say nothing user-visible changes, so no E2E is needed. File 04e ("a campus declares its plant": the pcc.plant key resolved at ingest to a PocBranch, the GUI wiring and a model-set store) and link it. Add a release note.
