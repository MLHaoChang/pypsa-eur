<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Required effect guards and independent completion verification

Status: needs-triage
Type: task
Blocked by: reliability contract review; 01 for guard/receipt schemas; 04 for outcomes

## Scope

Implement [reliability contract](../reliability-contract.md) section 4 across
P0/P3/P6/P7. Add mandatory precondition guards separate from existing courtesy
validators. Enforce offered schemas, request scope, domain bounds, confirmed
arguments and revisions. Read back effects against authorized changed paths and
bind receipts to operation/arguments/before-after revisions. Extend existing
task/job idempotency to untasked effects; reconcile timeout workers before retry.
Require effect evidence for task resolution, never model-authored completion alone.

## Test-first acceptance

Required groups: **A3.5, A3.6, A6.6, A7.7** in [test matrix](../test-plan.md).
Missing/raising mandatory guard must result in zero handler calls. Test unchanged
ordinary confirmations, altered payloads after approval, post-wait revision change,
partial/extra writes, successful return with wrong read-back, conflicting operation
IDs, late timeout completion and model-supplied reconciliation. Real isolated
service/state fixtures exercise effects and preserve no-replay guarantees.

## Completion and handoff

Done only when every released effectful operation (including legacy read-tier
exports) has guard/postcondition mappings and required cases pass across ordinary
dispatch, tasks and supported providers. Reuse current locks/stores, run relevant
auth/confirmation/task/job regressions and update [ledger](../coverage-ledger.md)
and [handoff](../session-handoff.md). Added guard requirements are currently pending.

## Comments

Existing advisory validator exceptions do not establish unsafe handler behavior;
handlers retain their own checks. This ticket adds explicit required policy gates
and outcome verification without promoting a courtesy check into the authority.

### 2026-10-11 — shared harness implementation

The strict allowlist now admits only an exact enabled static-input update contract, with argument snapshot, confirmation, project/input revision checks, pre-handler durable reservation and independently prepared/read-back postconditions. Late-worker tests prohibit hidden replay and publication while an effect is pending; completed-ID retries avoid extra dirty/undo work. All other unmapped strict mutations are refused. General per-tool guard maps, durable jobs/outboxes and distributed runtime remain pending.

See [implementation status](../implementation-status.md), [contracts](../harness-contracts.md) and [coverage ledger](../coverage-ledger.md). The whole ticket remains open; no independent review verdict is inherited by this extension.
