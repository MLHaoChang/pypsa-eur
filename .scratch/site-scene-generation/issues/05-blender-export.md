<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-04 — export an editable site snapshot for Blender

Status: needs-triage — specification proposed; not implemented
Type: task
Dependencies: SC-00 N/SC-01 for native export; SC-02 for bindings; SC-00 B before claiming tested Blender compatibility
Baseline: master 9988f9899a3bf8e80f51272529f18ea70599b90c

## Implementation assignment

Follow [execution-guide.md](../execution-guide.md) and the matching units in [execution-manifest.yaml](../execution-manifest.yaml). Freeze this assignment's actual-master file allowlist and public contracts; satisfy [decision gates](../decision-register.md) before dependent code, and record test-first evidence/unfinished work using [implementation-record-template.md](../implementation-record-template.md). Prototype N/B/P units close their own decisions through evidence; they do not require accepted experimental outcomes before starting. Primary V ownership does not remove cross-cutting obligations or authorize changes outside the frozen slice.

## Scope and acceptance

Write artifact round-trip tests first. Export a bounded GLB and canonical manifest carrying Site origin, axes/metres, scene/model revision, feature IDs, component bindings, source classifications and content hashes. This works without Blender installed. Source-verified equipment retains native scale, and supported library instances are not silently collapsed into ambiguous component identities.

Offer Export for Blender in the existing Site action area. The download is a snapshot, not an active model link or certification. Respect the selected source-media policy: no implicit inclusion of original photos or imagery without derivative/export permission. Missing optional mesh exports proxies or a named omission.

Use a genuine pinned Blender import/export fixture to demonstrate expected transforms/IDs and an independent metric oracle. Output-only .blend creation requires a separately configured trusted worker; browser users can always download the neutral bundle.

Done: V29–31 plus artifact ownership/limits/privacy and replayable evidence. No inbound arbitrary .blend execution, external app launch or silent model mutation.

## Validation

V29–31, V05–06, V18, V45, V49; definitions in ../validation-matrix.md. Tests before implementation at the frozen public seams; no passing evidence is claimed by this ticket.

## Coordination

One bounded implementation PR after applicable plan review; current master and open-PR overlap check first. Follow AGENTS and steward, add a release fragment for shipped behavior, never change compiled release notes or another task branch. Record exact tests, genuine worker evidence and unimplemented parts in the PR and handoff.

## Cross-cutting AI guidance

Deliver this slice's in-place explanation, typed proposed action or truthful UI fallback, native read-back and resume context under [ai-guided-project-flow.md](../ai-guided-project-flow.md), alongside SC-07. Server flow state and current approvals remain authoritative. Include the relevant V61–V72 assertions; optional engine absence cannot block the native/manual flow.
