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
and [ledger](../coverage-ledger.md). New requirements currently have no executable coverage.

## Comments

Original plan review does not approve this added ticket. Freeze contracts before
coding acceptance fixtures; do not weaken the transferred v1 tests.
