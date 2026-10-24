<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-00 — demonstrate the geometry and exchange contracts

Status: needs-triage — specification proposed; not implemented
Type: prototype
Dependencies: none
Baseline: master aba252d2384f55116bbd15516a5b025951ba265e

## Scope and acceptance

Read spec.md, implementation-plan.md and validation-matrix.md. Do not modify the solver or install mandatory base-app dependencies. Complete independent N (Native), B (Blender) and P (Photo) milestones, each with its own evidence/status. N covers model-free native generation, canonical writes, private-media policy and server /flow. B covers a genuine GLB/manifest round trip on a supported pinned Blender. P covers a small genuine reconstruction on supported hardware. Completion of N alone unblocks native implementation; the parent issue need not wait for B/P to enable SC-01.

The prototype settles: metric frames/origin and dimensional labels; exact public scene/proposal records and method/path contracts; source/asset redistribution posture; supported Blender version; native target rename/delete/recreate identity hooks; and measured file/pixel/mesh/texture/job budgets. Demonstrate background generation is separate from hourly playback. Inspect the existing solve queue before choosing any job adapter; no unknown kind may fall through to an electrical solve.

Use a reference 10 x 20 x 8 m building and two known camera/control/scale fixtures. Report expected versus observed geometry and identity; a visually attractive screenshot is not a scale/binding test. A worker not available is a named missing result, not a successful stub. Keep native/proxy generation unblocked by optional GPU availability.

Done for each milestone separately: exact reproducible evidence and its agreed downstream contracts, with each unsupported input/engine explicitly disabled. Record accepted N independently even while B/P remain unavailable or pending. Before SC-02, lifecycle identity must be demonstrably enforceable or its required scope amendment is recorded. No production worker or per-project arbitrary-library override is enabled here.

The owner also requires end-to-end AI guidance: freeze the bounded per-turn context, proposed scene tool tiers/DTOs, approval/receipt/recovery and per-section handoff in ../ai-guided-project-flow.md. Prototype a native journey with the existing scripted provider and real native records; no paid call. This is part of N and must not wait for optional engines.

## Validation

V05–08, V14, V29–30, V41–43, V49, V53–60, V61–72; definitions in ../validation-matrix.md. Tests before implementation at the frozen public seams; no passing evidence is claimed by this ticket.

## Coordination

One bounded implementation PR after applicable plan review; current master and open-PR overlap check first. Follow AGENTS and steward, add a release fragment for shipped behavior, never change compiled release notes or another task branch. Record exact tests, genuine worker evidence and unimplemented parts in the PR and handoff.

## Optional coordinate-first workflow clarification

Freeze the confirmed native site-anchor settings DTO/persistence/ACL seam, open-versus-generate action verdicts, saved buildings-only state, native graph/terminal projection and native cadence contract from optional-site-workflow.md. Prototype both geometry-first and already-configured-model entry orders. Unknown anchor persistence is an explicit prototype outcome, never permission to create a dummy Bus. V73–V90 are design cases to freeze, not all completed prototype evidence. Read [optional-site-workflow.md](../optional-site-workflow.md) and [workflow-assessment.md](../workflow-assessment.md).
