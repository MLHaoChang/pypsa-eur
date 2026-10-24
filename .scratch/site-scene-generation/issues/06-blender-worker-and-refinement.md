<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-05 — optional integrated Blender generation and reviewed return

Status: needs-triage — specification proposed; not implemented
Type: task
Dependencies: SC-04; SC-00 worker/protocol/resource evidence
Baseline: master aba252d2384f55116bbd15516a5b025951ba265e

## Scope and acceptance

Write readiness/job/apply tests before implementation. A configured pinned Blender executable/worker receives only normalized owned input and a trusted script in a bounded isolated job. Native generation remains available when it is absent. Reuse the agreed job persistence/cancellation contract; do not infer safe resource limits or add a kind that can execute the solve dispatcher accidentally.

A worker's completion produces a proposal. Geometry/placement returns must reference the exact export base and current scene/native target identity. Validate units/transforms/content limits and all IDs, show a diff and named unknowns, then require explicit Apply. Missing metadata yields reference-only geometry, not an automatic rebind. Extras cannot change numerical model inputs or results. Preserve ordinary project locks and recheck access at Apply.

Test cancellation, timeout, worker death, late/stale output, concurrent edit, duplicate Apply, unknown apply outcome/read-back and revoked access. Never execute untrusted uploaded .blend scripts. Optional Open in Blender is a separate desktop convenience using a configured executable; it is not a web capability claim.

Done: V32–38, genuine worker evidence and no change to the model from imported visual data. Only the validated adapter is exposed; no hosted paid worker is enabled.

## Validation

V32–38, V45–50, V52; definitions in ../validation-matrix.md. Tests before implementation at the frozen public seams; no passing evidence is claimed by this ticket.

## Coordination

One bounded implementation PR after applicable plan review; current master and open-PR overlap check first. Follow AGENTS and steward, add a release fragment for shipped behavior, never change compiled release notes or another task branch. Record exact tests, genuine worker evidence and unimplemented parts in the PR and handoff.

## Cross-cutting AI guidance

Deliver this slice's in-place explanation, typed proposed action or truthful UI fallback, native read-back and resume context under [ai-guided-project-flow.md](../ai-guided-project-flow.md), alongside SC-07. Server flow state and current approvals remain authoritative. Include the relevant V61–V72 assertions; optional engine absence cannot block the native/manual flow.
