<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Request scope and complete key-results answers

Status: needs-triage
Type: task
Blocked by: reliability contract review; 01 for schema freeze; 02 for evidence

## Scope

Implement [reliability contract](../reliability-contract.md) section 1 across
P0/P2/P7. Define server-owned request scope/slot dispositions and versioned domain
summary profiles. Resolve authorized IDs and explicit application defaults;
clarify material ambiguity. Preserve user constraints across turns. Verify
required requested metrics and warnings; missing slots produce a limited answer.
Coordinate with [10](10-metric-calculation-and-validity.md) for metric definitions.

## Test-first acceptance

Required groups: **A0.4, A2.8, A6.6, A7.7** in [test matrix](../test-plan.md).
Use real selected projects and independently specified metrics. Include wrong
run/metric/window with numerically valid data, ambiguous objective versus cost,
omitted failed intervals, unsupported slots and a fully valid useful companion.
Bound profiles to the draft/claim limit with approved section/table behavior.
No golden request may pass by always returning unavailable.

## Completion and handoff

Done only when reviewed contracts, required cases and affected public consumers
pass, mappings cover every released domain profile and required slot dispositions
appear in receipts. Record exact commands/counts/dependencies in [handoff](../session-handoff.md)
and [ledger](../coverage-ledger.md). The full added requirement groups remain incomplete; the bounded generator profile has executable coverage.

## Comments

Original plan review does not approve this added ticket. Freeze contracts before
coding acceptance fixtures; do not weaken the transferred v1 tests.

Implementation continuation (2026-10-10): The generator-dispatch/1 candidate now assigns all three required result slots, rejects omitted available metrics, and records bounded project/entity/full-current-horizon scope. Arbitrary questions, retained runs, conversational constraints and other domain profiles remain pending. See [implementation status](../implementation-status.md). This is editing-agent prototype coverage, not inherited independent approval or whole-ticket completion.
