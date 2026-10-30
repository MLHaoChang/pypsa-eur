<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# GridSpine workflow harness — implementation specification package

Start with [spec.md](spec.md) for ownership/shared contracts and [workflow-strategy.md](workflow-strategy.md) for the product flow. [user-requirements.md](user-requirements.md) is the review source; [baseline.md](baseline.md) distinguishes shipped behavior from proposed integration. [test-matrix.md](test-matrix.md) defines qualification and [rollout.md](rollout.md) activation/recovery. Implementers follow [handoff.md](handoff.md).

Planning base:60d8332d64b2edef1b204fb5980311e6102229a8. The [research package](../harness-research-2026/assessment.md) retains its own earlier source dates/SHAs and does not substitute for these implementation contracts.

## What improves for users

- Safer model actions: stale approvals cannot authorize a changed native input.
- Continuity: confirmed requirements, exact results and unfinished work survive long chats and restarts.
- Bounded background studies: native checks drive justified new candidates, with visible history and honest stop reasons.
- Sourced setup: research proposes parameters with units, sources and conflicts; applying a researched value still needs confirmation.
- Iterative deliverables: every report or Excel feedback item stays tracked until its checks and required decisions are satisfied.
- Parallel help: one coordinator can use two read-only workers while native software retains action, evidence and budget authority.

These are proposed capabilities. The first unattended numerical adapter is intentionally small; unsupported domain/template steps remain visible. The detailed contract distinguishes that initial release from later expansion.

## Features

| ID | Detailed specification |
|---|---|
| HH-01 | [Atomic mutation admission](issues/01-atomic-mutation-admission.md) |
| HH-02 | [Persistent confirmed requirements](issues/02-persistent-requirements.md) |
| HH-03 | [Durable physical-call accounting](issues/03-durable-call-budgets.md) |
| HH-04 | [Typed native references](issues/04-typed-domain-references.md) |
| HH-05 | [Trajectory/counterfactual evaluation](issues/05-trajectory-evaluation.md) |
| HH-06 | [Bounded study correction cycles](issues/06-study-correction-cycles.md) |
| HH-07 | [Research and parameter proposals](issues/07-research-parameter-proposals.md) |
| HH-08 | [Report/workbook revision cycles](issues/08-report-workbook-revisions.md) |
| HH-09 | [Coordinator, shared acceptance and delegation](issues/09-coordinator-delegation.md) |
| HH-10 | [Immutable isolated solve admission](issues/10-immutable-solve-admission.md) |

## Review and status

review-target.json lists exact normative file digests and their aggregate. reviews/ records findings, resolution and independent re-review; final GO must match that payload. Research source notes and historical papers are supporting evidence, not new implementation instructions or agent authority. A technical plan GO does not mean app code is implemented, runtime tests passed, current-head official reviewers approved, all template/domain adapters exist or real-use calibration was completed.

Each implementation ticket keeps the repository Status vocabulary; needs-triage marks unassigned/official gating until the coordinator commissions the bounded slice. Technical readiness is conveyed by the exact final review verdict, not by silently changing a ticket to done.

The current task writes/reviews specs only. Default-safe rollout, existing confirmations, native source/effect checks and independent domain-review limits remain explicit. No paid/model/media call, deployment or implementation merge follows from this document.
