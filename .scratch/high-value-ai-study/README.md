<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Higher-value AI assistance for GridSpine

Status: research-only specification; implementation and product-value measurements pending.
Assessed master: `87546ef5d184a9b20c80a65e2164f7fac0930f15` (2026-10-21).

The original three ideas improve understanding cheaply. The next three aim to shorten the whole study, improve the decision, and remove input preparation work. They are capabilities of the existing companion and native workspaces, not three new agents or top-level modules.

1. [Value assessment and code gaps](01-VALUE-AND-CODE-ASSESSMENT.md)
2. [Goal-to-study copilot](02-STUDY-COPILOT-SPEC.md)
3. [Decision and robustness advisor](03-DECISION-ROBUSTNESS-SPEC.md)
4. [Data-to-model onboarding](04-DATA-TO-MODEL-SPEC.md)
5. [Delivery sequence, measurement and validation matrix](05-IMPLEMENTATION-AND-TESTS.md)
6. [Contracts and example journeys](06-CONTRACTS-AND-JOURNEYS.md)
7. [Harness comparison, gaps and capability roadmap](07-HARNESS-GAPS-AND-ROADMAP.md)
8. [Owner disposition and amendments](OWNER-DISPOSITION.md)
9. [Session handoff](HANDOFF.md)

The harness supplement assesses newer master `322df0cf495260442eb35bb9c0c5d8e11a274ffe` against Claude Code's published behavior. Its eight findings and 24 planned foundation checks are recorded in [harness-assessment.json](harness-assessment.json), separately from the original 48 capability cases. It recognizes newly merged browser continuation and preserves the earlier test records as historical evidence.

Machine-readable records: [implementation-plan.json](implementation-plan.json), [native-evidence.json](native-evidence.json), [VALIDATION.json](VALIDATION.json). Run `python .scratch/high-value-ai-study/validate_specs.py` from the repository root. It checks this specification's integrity, not implementation readiness or engineering correctness.

This PR adds documents and their offline validator only. It does not change runtime, approvals, tools, card selections, model settings, or solver behavior. The owner requested a separate research/spec PR. It does not promote the research in #558 into implementation approval or supersede the delivery order of existing packages. No paid calls, reviewer contact, or domain-approval claim.
