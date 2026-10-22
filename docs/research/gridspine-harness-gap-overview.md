<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Making the GridSpine harness more capable

**Latest review:** [current-master reconciliation and residual scope](../../.scratch/high-value-ai-study/11-CURRENT-MASTER-AND-REVIEW.md). Master now contains LC-23 S1/S2 and other foundations. Use the amended 08/09/10 specs and 53-case plan; preserve shipped work. The independent verdict is separate from runtime/CI/merge approval.

**Implementation specs are now included:** [functional behavior](../../.scratch/high-value-ai-study/08-HARNESS-IMPLEMENTATION-SPEC.md), [data/state contracts](../../.scratch/high-value-ai-study/09-HARNESS-DATA-AND-STATE-CONTRACTS.md), [code delivery and test matrix](../../.scratch/high-value-ai-study/10-HARNESS-DELIVERY-AND-TESTS.md). They define seven delivery units in existing packages and 53 planned acceptance cases, using the merged LC-23 design on master `089aea276c75b098c8d349cb5fa0eb4cb7267d64`. Runtime implementation is not part of this PR.

GridSpine already has an agent loop, typed tools, permission/approval checks, skills, MCP, durable records, replay, job tracking and bounded checked answers. The main opportunity is to complete the native workflow coverage and verification around those foundations.

Assessed master: `322df0cf495260442eb35bb9c0c5d8e11a274ffe`. The comparison uses selected official Claude Code documentation, not its private internals or a live product benchmark.

| Priority | Improvement | User benefit |
|---|---|---|
| Foundation | Map native parameters/actions/results and their actual support/evidence | More requests work consistently; unavailable features have a precise reason and next step |
| Foundation | Separate manually acknowledged completion from independently verified outcomes | “Done” reflects the exact native evidence needed for the claim |
| Next | Assemble compact, source-revalidated working context | Better continuity across long tasks without resending irrelevant history or losing important constraints |
| Next | Extend checked metrics, source navigation and plausibility adapters | More useful explanations with clear scope, units, assumptions and missing assessments |
| Next | Extend reviewed study phases across admitted job families | Less manual coordination, with honest failure, cancellation and recovery |

Journey tests and existing diagnostics support every step. Add trusted domain procedures as native coverage grows. General specialist teams and code-analysis sandboxes are conditional later options, not required for the first gains. Broader access alone does not establish better engineering conclusions.

At the historical322df0c source, inventory/matrix checks reported **275 tools,17 result tabs,20 panels and170 creation controls**;11 records newer source-bound checks. The matrix still marks all 14 family-journey links pending and full coverage not established. Passing subset journeys exist; the ledger is not complete proof of all-domain behavior.

Newly merged LC-05 already continues the chat after a long solve, holds for unsent text/cards and supports dismissing the continuation. The remaining work is broader admitted job/evidence coverage, not rebuilding that feature.

Read the [detailed assessment and roadmap](../../.scratch/high-value-ai-study/07-HARNESS-GAPS-AND-ROADMAP.md), [source and validation records](../../.scratch/high-value-ai-study/harness-assessment.json), and [handoff](../../.scratch/high-value-ai-study/HANDOFF.md). There are 24 planned foundation checks, distinct from the earlier 48 capability cases. This is specification work only; actual checks run and remaining evaluation limits are recorded separately. No runtime change, paid model calls or domain-approval claim.
