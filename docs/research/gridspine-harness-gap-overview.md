<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Making the GridSpine harness more capable

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

Current inventory/matrix checks report **275 tools, 17 result tabs, 20 panels and 170 creation controls**. The matrix still marks all 14 family-journey links pending and full coverage not established. Passing subset journeys exist; the ledger is not complete proof of all-domain behavior.

Newly merged LC-05 already continues the chat after a long solve, holds for unsent text/cards and supports dismissing the continuation. The remaining work is broader admitted job/evidence coverage, not rebuilding that feature.

Read the [detailed assessment and roadmap](../../.scratch/high-value-ai-study/07-HARNESS-GAPS-AND-ROADMAP.md), [source and validation records](../../.scratch/high-value-ai-study/harness-assessment.json), and [handoff](../../.scratch/high-value-ai-study/HANDOFF.md). There are 24 planned foundation checks, distinct from the earlier 48 capability cases. This is specification work only; actual checks run and remaining evaluation limits are recorded separately. No runtime change, paid model calls or domain-approval claim.
