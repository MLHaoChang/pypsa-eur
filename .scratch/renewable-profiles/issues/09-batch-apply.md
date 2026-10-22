<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-09 — batch apply as one undo step

Status: done (#605, 65ba5cad)
Type: task
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`
Blocked by: the profile stack landed; gatekeeper review (data write)
Requirements: R3, R7 in [the spec](../spec.md)

`POST /api/projects/{name}/profiles/apply` accepts `components: [...]` or `all_from_job: true`. It validates every member first (all or nothing), pushes one undo entry, then writes each through `assign_profile`.

Acceptance:
- 3 generators, one with a carrier mismatch → 422, and nothing is written.
- 3 valid generators → all written; one undo restores all 3, including the network-native `p_max_pu`.
- The chat tool shows one confirmation card listing all members.
