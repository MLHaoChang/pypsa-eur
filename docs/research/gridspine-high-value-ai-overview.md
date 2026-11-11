<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GridSpine: the next AI capabilities by user value

The original three suggestions—explain the next step, explain results, suggest useful scenarios—are sensible low-cost starting points. They are not a measured ranking of all possible value. The next three I would prioritize for greater overall impact are:

| Priority | Addition | What the user gains |
|---|---|---|
| 1 | **Goal-to-study copilot** | Describe a goal, review the study and progress through native setup, approved runs, recovery and evidence without manually coordinating every step |
| 2 | **Decision study explanations and robustness** | See which feasible option fits the objective, why, which assumptions could change it and what the evidence does not establish |
| 3 | **Data-to-model onboarding** | Map uploaded profiles/equipment data to actual model fields with visible units, repairs, provenance and reviewed application |

These extend the same companion and card workspaces. They do not add three bots, another Manage area, or a second execution system. Native tools still calculate, validate, request approval and verify effects. Better confidence means clearer evidence and limits, not more confident model prose.

The ranking is qualitative; onboarding can be first for users whose main delay is preparing data. Product-value and live-model measurements are pending. None of these capabilities is implemented by this research PR.

The [complete specification package](../../.scratch/high-value-ai-study/README.md) contains current-code gaps, detailed UX/backend requirements, example journeys for C&I/data centres/IPPs/H2/ammonia/mining/microgrids, nine acceptance slices mapped to existing packages, 48 planned acceptance cases, source pins, baseline test evidence and a session handoff.

The subsequent [harness assessment](gridspine-harness-gap-overview.md) compares newer master with Claude Code's documented capabilities. It prioritizes native coverage, completion proof, working context, checked analysis and broader admitted study phases, with 24 separate planned foundation checks.

Assessed master: `87546ef5d184a9b20c80a65e2164f7fac0930f15`. The plan respects the six-card model and the newer owner decisions relayed in [#559](https://github.com/MLHaoChang/pypsa-eur/pull/559). The [earlier #558 research](https://github.com/MLHaoChang/pypsa-eur/pull/558) remains research only. Runtime work follows the existing package order and review gates.

[Current owner disposition](../../.scratch/high-value-ai-study/OWNER-DISPOSITION.md): fold into existing packages, cross-card brief on the plan card, individual write/run approvals and card-boundary continuation; document extraction follows spreadsheet mapping; no separate evidence-map/advisor interface.
