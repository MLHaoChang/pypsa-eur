<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Nonblocking domain jobs, recoverable monitoring and results refresh

Status: ready-for-agent
Type: task
Blocked by: 02, 08, 09

Implement steps 4 and 6 of [the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md)
following [coverage contract](../application-coverage.md). Inventory asynchronous
workers and create neutral adapters for solves, simulation/AC-PF, GridSpine,
FMEA/adequacy/Monte Carlo/frontier/coupling and other discovered families. Include
identity/ACL, status/phase/progress cursor, timestamps, input/result revision,
warnings, terminal outcome and verified cancellation.

Fix the solver-log bridge holding the sole turn until foreground simulation/AC-PF
completion: return stable handles, detach observation and release the lease.
A pending queue is insufficient. Preserve completion semantics and conflicting-
mutation locks. Separate Stop speaking/monitoring and Cancel task/job.

Use authenticated versioned events/cursors/snapshots with deduplicated recovery.
Completion refreshes matching result revisions without overriding newer navigation.
No model turns for polling/notifications. Extend closed event contracts explicitly
with tests or document a neutral separate envelope. Done when a slow job permits
an answered read before completion, every adapter passes lifecycle/cancellation/
recovery, event gaps/duplicates recover, stale updates cannot cross projects/views,
and UI/results match authoritative artifacts for every workload.

## Comments
