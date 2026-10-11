<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Nonblocking domain jobs, recoverable monitoring and results refresh

Status: claimed
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

2026-10-11: D-I1 first slice — `_dispatch_real_tool_call` no longer awaits
`solver_log_bridge` for `run_simulation` / `run_ac_pf_stage`. Tool result is the
started handle (`status=started`, `running`, `monitor=get_simulation_status`);
the turn and utterance lease release while the worker continues. Tests:
`tests/test_nonblocking_foreground_solve_d_i1.py` (3 passed). Still open: detached
progress SSE, full domain adapters, wait_for_job for foreground solves, D-I2
cursor recovery, D-C1 multi-family monitoring.

2026-10-11: Detached progress buffer — `start_detached_solver_monitor` runs
`solver_log_bridge` on a daemon thread into a session ring (tool_progress-shaped
payloads); drain via `session.drain_detached_progress()`. No new SSE frame names.
Tests: `tests/test_nonblocking_detached_progress_d_i1b.py` (3 passed). Still open:
emitting progress on an open stream, domain adapters, wait_for_job, D-I2/D-C1.
