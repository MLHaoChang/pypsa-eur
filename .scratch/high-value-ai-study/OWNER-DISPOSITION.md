<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Owner disposition of this research

Status: research only; implementation follows existing packages and their normal gates.

**Current-source amendment:** [11](11-CURRENT-MASTER-AND-REVIEW.md) reconciles these decisions with master `7a14d27038de424d07a832b16e2465297c7b8b7d`; [12](12-INDEPENDENT-REVIEW.md) records the owner-requested separate design review. Historical statements about unimplemented limits/reviewer contact below describe the earlier research session, not this subsequent review.

This record supersedes conflicting suggestions elsewhere in the original draft. The detailed specs and planned cases have been adjusted accordingly. Source: coordinator-relayed owner decisions in [PR #559](https://github.com/MLHaoChang/pypsa-eur/pull/559), exact head `e36d1e714382aa00086c08b80ef97ad4e8dbf657`, [pinned owner log](https://github.com/MLHaoChang/pypsa-eur/blob/e36d1e714382aa00086c08b80ef97ad4e8dbf657/docs/agents/owner-decisions.md). Those rows were not yet in the original assessed master; #559 is now merged at current specification baseline 089aea276c75b098c8d349cb5fa0eb4cb7267d64. This research session did not contact reviewers or obtain a separate approval.

| Decision | Consequence for #567 |
|---|---|
| AI-567 | Fold useful rules/tests into existing packages; no new modules or packages. Decision robustness belongs to S6/LC-18, existing decision study/adequacy, card-workspace H2 and S8 explanations; no separate evidence map. Profiles use profile upload/TS-01/TS-04, not Library items. Equipment-table mapping with cell lineage is inside LC-16 |
| AI-567b, amending AI-567 | Build the study copilot through start_task/LC-05: a cross-card brief shown **as the existing plan card**, native readback, per-step approval, resume, one companion entry, after #542. Raise the finite step bound in that owning slice. Run steps already exist at the assessed baseline; extend their admitted coverage, do not claim they are absent. Include PDF datasheets/tariffs/PPAs as slice 2 after spreadsheet mapping, each value beside page/quote and confirmed before native writes; validate against actual engine fields |
| COP-1 | Every write **and run** step needs its own card click, including when ordinary Expert write policy would otherwise be uncarded. Pause at every card boundary with **Continue to <card>** on the plan card. Reads proceed without an extra stop. Approving the brief grants no execution authority |
| RC1–RC4 / merged #566 | Six card workspaces, at most four result tabs each; Asset economics in Capacity › Costs & prices; off-card results visible greyed with Outside scope · Add, older results read-only; unsupported grid assets stay unavailable until their gated engine work. The design has owner go in its own slice order; this does not mean its runtime is complete |
| USE-1 | No new packages until running ones finish; this is an update to the existing research PR. Preserve a handoff and keep future implementation in the established owning packages |

AI-567b supersedes AI-567's earlier rejection of a cross-card brief and deferral of document extraction. It does **not** reinstate an evidence map, a second progress surface, a new planner runtime or new top-level modules. Relevant source/status/coverage limits stay inline in existing native result explanations. “Decision study” is the existing product concept; do not add a new “advisor” module.

Current master LC-23 S1 now implements 24 steps/32,000 serialized characters for a brief and 12/16,000 for a plain plan; preserve its 1,800-character step arguments and phase partitioning. This docs PR changes no runtime limit. Residual proof, diagnostic accounting, boundary hardening and migration handling extend that implementation. Do not simulate a larger plan through hidden nested controls or unlimited phases.

The [card-workspaces design](https://github.com/MLHaoChang/pypsa-eur/blob/fdcd35c1bd11eb8b10a19db82fa499e6794480b3/.scratch/project-workflow/card-workspaces.md) merged during this assessment as #566. The incoming diff since tested master `322df0cf495260442eb35bb9c0c5d8e11a274ffe` contains only that design and its release fragment; backend/frontend code is unchanged. The test records remain labeled with the source actually tested.

No runtime build, paid calls, production activation or standing product/engine/UX reviewer contact is performed by this research PR. The owner's later request explicitly authorizes the separate agent design review recorded in 12; independent domain review and real-use calibration remain pending.

The latest direct request asks for implementation specs in this PR. Documents 08–10 and harness-implementation-plan.json supply those contracts; the runtime remains unchanged and existing owning packages/reviews govern building.
