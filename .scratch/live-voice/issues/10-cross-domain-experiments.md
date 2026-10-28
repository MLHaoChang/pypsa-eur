<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Typed cross-domain experimentation and resumable task advancement

Status: needs-triage (owner decision SH-FOLD-1, 2026-10-28): the resumable task advancement (plan step 6) folds into the study-harness runner, `.scratch/study-harness/issues/02-server-runner.md`; the typed cross-domain adapters (plan step 4) are re-triaged here after that issue lands.
Previous status: ready-for-agent
Progress: Network experiment persistence, prepare, compare, two-case admission shipped; commercial, reliability, GridSpine execution adapters and resume absent
Type: task
Blocked by: 02, 08, 09, 11

Implement steps 4 and 6 of [the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md)
using [coverage contract](../application-coverage.md). Extend numeric network
sensitivity with typed adapters for scalar/bool/enum/list/nested/unit/file/topology
changes where actual schemas permit. Preview changes/affected results and
revalidate fingerprints at commit.

Persist isolated baseline/version, cases, settings/seeds/objectives, resource/
time/cost limits, stop conditions, dependencies, job IDs and artifacts.
Advance steps through shared admission/ACL/approval/lock/budget gates; existing
start_task persists plans without executing them. No hidden framework agent or
arbitrary model code. Reconcile uncertain outcomes before retry.

Preserve baseline, expose partial/all failures, compare compatible evidence,
support verified cancellation/resume and export provenance. Refinement cannot
silently expand approval scope. Done when real small fixtures cover network,
commercial/settings, reliability/studies and GridSpine as supported by inventory,
plus typed parameters, approval, isolation, partial failure and interruption/export.
Unsupported required domains block a full experiment claim.

## Comments

2026-10-11: First slice — `services/experiments.py` persists typed experiment
plans (`start_experiment` / `list_experiments` / `get_experiment`) under
`<project>/experiments/` with domain adapters (network/commercial/reliability/
gridspine), typed changes (number/boolean/string/enum + optional unit),
request_id dedupe, owner/ACL fail-closed. `executed=false`; no hidden solves.
Catalogue/dispatcher/route/toolset/inventory. Tests:
`tests/test_experiments_persist.py`. Still open: advance cases through ordinary
tools/queues, partial failure, compare, cancel/resume, commercial/reliability/
GridSpine execution adapters.

2026-10-13: Bounded prerequisite continuation adds normalized case/attempt/time/
cost caps, owner-scoped canonical request deduplication, bounded fail-closed
checkpoint reads and explicit `prepare_experiment`. The network adapter validates
actual static targets/units/ranges and private candidates, binds authorized run
evidence and exact current/candidate input digests, and leaves execution unavailable.
Legacy plans remain readable; unsupported domains remain persistence-only.
See [the contract](../experiment-prerequisites.md) and
[remaining implementation plan](../../../docs/superpowers/plans/2026-10-13-assistant-remaining-work.md).
Case advancement, effect reconciliation, comparison/cancel/resume and additional
domain recipes remain open. This is input preflight, not domain or release approval.

2026-10-13: First single-network-case adapter adds ordinary `advance_experiment`,
`refresh_experiment` and `cancel_experiment` controls and a durable dispatch ledger.
It uses the existing confirmed one-case sensitivity/queue path; no activation,
hidden tool loop or task-rebinding exemption. Reservations survive interruption,
duplicate calls cannot resubmit, attempt/time caps stop advancement, and partial
child IDs survive. Completion requires actual terminal success, exact saved/solved
inputs and intact owner/job/run evidence. Explicit native evidence export remains
an ordinary case-project action. See [the contract](../experiment-case-advancement.md).
Two/multiple cases, compatible comparisons, explicit uncertain-effect retry
reconciliation and commercial/reliability/GridSpine adapters remain open. This
slice does not close issue 10 or establish whole-application checked coverage.
