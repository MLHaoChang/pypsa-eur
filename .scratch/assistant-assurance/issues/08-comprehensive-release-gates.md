<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P7: adversarial, full-family and performance release

Status: ready-for-agent
Type: task
Blocked by: 01–07 plus required retained-run/durable/job dependencies

Read the [spec](../spec.md), [test matrix](../test-plan.md),
[plan](../../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md)
and harness README. Design status/review is in the plan; readiness is subject to
the listed dependencies and reviewed contract, not implementation certification.

## Scope

Complete every family adapter/real journey and tab/panel/editor mapping, provider HTTP transport parity, prolonged mixed-domain conversations and failure/race tests. Benchmark cold/warm validation/reviewer/cost; calibrate qualitative review with independent domain judgments. Validate shadow/pilot/strict/rollback/client migration/retention/single-owner operation. No whole-application assurance label before all required gates pass.

## Test-first acceptance

Required case groups: **A7.1–A7.6** in the shared test matrix. Start with a failing
public-seam tracer, implement minimally, run green and relevant regressions.
Use independent numerical fixtures and isolated authorized projects; preserve
provider-neutral handlers, ordinary safety gates and cumulative budgets.
Record every required case as executable/passed/red/pending with a reason.
Opt-in skip or schema reachability is not behavior coverage. No paid APIs are
needed to start these deterministic slices.

## Completion and handoff

Done only when the required behavioral cases and dependencies pass, every
affected public channel uses the same approved contract, and regression/style/
schema/UI checks pass. Append commands, exact red/green counts, policy version,
remaining blockers and next runnable tracer to [handoff](../session-handoff.md).

## Comments

### Proposed reliability revision

P7 also requires A7.7: per-required-operation/metric independent fixtures, zero required deterministic false accepts, useful positive fulfillment, and versioned error/abstention denominators. A sample family journey is insufficient.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.

### 2026-10-11 — shared harness implementation

Implemented shared capabilities/contracts, profile rollback, separately disabled checked writes and bounded shadow observations that do not change ordinary text or store drafts. Deterministic publication/provider/UI/storage/timeout regressions are executable. Production false-acceptance calibration, load measurement, independent domain review, full real-browser journeys and release enablement remain pending. Deployment defaults stay disabled; GitHub Actions was disabled on the preceding PR.

See [implementation status](../implementation-status.md), [contracts](../harness-contracts.md) and [coverage ledger](../coverage-ledger.md). The whole ticket remains open; no independent review verdict is inherited by this extension.


### User-facing checked pilot (2026-10-11)

The user selected an explicit chat opt-in. The control reads current server
capabilities, requests the existing strict publication contract and retains
checking through retry/edit/clarification/queue/Live paths. It cannot disable
server-required strict mode or silently downgrade after rollback. Unavailable
checked history has a conservative retry requirement without approval; async
rewinds reject changed generations, including project round trips.

Final source integrates master `baa3a95e`. The selected shared backend gate
passes 1,097 / seven skips; complete frontend passes 3,686 / 313 files. Native
Chromium with mocked API responses checks keyboard/SSE/digest/rollback at a
380-pixel panel width. Build, 229-tool inventory and touched-file style pass.
See [validation](../checked-opt-in-validation.json), [coverage ledger](../coverage-ledger.md)
and [activation/rollback](../checked-opt-in-runbook.md). No paid calls, full-backend
fresh run, independent domain approval or full-device/calibration release claim.
Global deployment remains disabled and this whole phase issue stays open.
