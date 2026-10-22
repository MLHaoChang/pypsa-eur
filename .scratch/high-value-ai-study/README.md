<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Higher-value AI assistance for GridSpine

**Active source reassessment — independent design GO:** [13 — current master and next build](13-MASTER-REASSESSMENT-4b57.md), [active JSON](active-master-plan.json) and [14 — current review](14-ACTIVE-PLAN-REVIEW.md) supersede earlier delivered-status/priority claims for snapshot`4b57cb3ccf310b151e53f4344015e642db0ca0cb`. PDF asset/commercial writes, COPT, checked charts, display joins and scope switches now ship. HS-01a remains the first missing bounded build; earlier880GO and test counts retain their original source.

**Independent verdict: GO for implementation-spec readiness**, frozen on master`880496ef679af1efc51a6dd3d62f9e41a75f2937`. [Final separate-agent review](12-INDEPENDENT-REVIEW.md) binds seven normative file hashes and records all four blockers resolved. The53 acceptance cases remain planned; runtime/domain/CI/merge approval is separate. Next residual unit is HS-01 in its existing owning package, under normal gates.

**Latest review:** [current-master reconciliation and residual scope](11-CURRENT-MASTER-AND-REVIEW.md). Master now contains LC-23 S1/S2 and other foundations. Use the amended 08/09/10 specs and 53-case plan; preserve shipped work. The independent verdict is separate from runtime/CI/merge approval.

Status: implementation specifications for review; runtime implementation and product-value measurements pending. Historical research and test evidence are retained separately.
Assessed master: `87546ef5d184a9b20c80a65e2164f7fac0930f15` (2026-10-21).

The original three ideas improve understanding cheaply. The next three aim to shorten the whole study, improve the decision, and remove input preparation work. They are capabilities of the existing companion and native workspaces, not three new agents or top-level modules.

1. [Value assessment and code gaps](01-VALUE-AND-CODE-ASSESSMENT.md)
2. [Goal-to-study copilot](02-STUDY-COPILOT-SPEC.md)
3. [Decision study explanations and robustness](03-DECISION-ROBUSTNESS-SPEC.md)
4. [Data-to-model onboarding](04-DATA-TO-MODEL-SPEC.md)
5. [Delivery sequence, measurement and validation matrix](05-IMPLEMENTATION-AND-TESTS.md)
6. [Contracts and example journeys](06-CONTRACTS-AND-JOURNEYS.md)
7. [Harness comparison, gaps and capability roadmap](07-HARNESS-GAPS-AND-ROADMAP.md)
8. [Owner disposition and amendments](OWNER-DISPOSITION.md)
9. [Session handoff](HANDOFF.md)

**Start here for implementation:** [08 — functional specification](08-HARNESS-IMPLEMENTATION-SPEC.md), [09 — data/state contracts](09-HARNESS-DATA-AND-STATE-CONTRACTS.md), [10 — code delivery and required tests](10-HARNESS-DELIVERY-AND-TESTS.md), and [harness-implementation-plan.json](harness-implementation-plan.json). These define seven implementation units inside existing packages, exact task/discovery/context/result contracts, migration and rollback behavior, code targets and 53 planned acceptance cases. They adopt the merged LC-23 design on master `089aea276c75b098c8d349cb5fa0eb4cb7267d64`; they are not recommendations alone.

The harness supplement assesses newer master `322df0cf495260442eb35bb9c0c5d8e11a274ffe` against Claude Code's published behavior. Its eight findings and 24 planned foundation checks are recorded in [harness-assessment.json](harness-assessment.json), separately from the original 48 capability cases. It recognizes newly merged browser continuation and preserves the earlier test records as historical evidence.

Machine-readable records: [implementation-plan.json](implementation-plan.json), [native-evidence.json](native-evidence.json), [VALIDATION.json](VALIDATION.json). Run `python .scratch/high-value-ai-study/validate_specs.py` from the repository root. It checks this specification's integrity, not implementation readiness or engineering correctness.

This PR adds implementation specifications and their offline validator only. It does not change runtime, approvals, tools, card selections, model settings, or solver behavior. The owner's latest request explicitly requires implementation specs in the PR. Build scheduling and applicable reviews still follow existing packages; neither this documentation nor historical #558 research is a deployment or runtime approval. No paid calls, reviewer contact, or domain-approval claim.
