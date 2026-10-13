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

2026-10-11: Progress observer SSE — `GET /api/chat/{session_id}/progress`
drains the detached ring as existing `tool_progress` frames; stays open while
the monitor is active; ownership/replica gates match /confirm. Tests:
`tests/test_nonblocking_progress_observer_d_i1c.py` (3 passed). Still open:
frontend observer subscription, domain adapters, wait_for_job, D-I2/D-C1.

2026-10-11: Frontend progress subscription — `createChatProgressStream` GETs
the observer; ChatPanel opens it on `tool_result.detached_progress` and
appends `tool_progress` into the store. Cleared on project switch / new chat /
unmount. Tests: `chat.test.ts` + `ChatPanel.detachedProgress.test.tsx`. Still
open: domain adapters, wait_for_job, D-I2/D-C1.

2026-10-11: Foreground `wait_for_job` — handoff mints `fg:…` handles via
`harness/job_adapters.py`; `wait_for_job` polls solver status with the same
bounded contract as queue jobs (timeout/cancel leave the solve running).
Tests: `tests/test_nonblocking_wait_for_foreground_d_i1d.py` (4 passed).
Still open: other domain adapters, D-I2 cursor recovery, D-C1.

2026-10-11: Adequacy/study `wait_for_job` — handoff mints `ad:…` handles for
`run_fmea_sweep` / frontier / mc / coupling / margin / eh / investment_case;
`wait_for_job` polls `solver_state[study_key]` (terminal done/failed/aborted/
refused). GridSpine remains on solve-queue UUIDs. Tests:
`tests/test_nonblocking_wait_for_study_d_i1e.py` (4 passed). Still open:
report-job adapter (`rpt:`), D-I2 cursor recovery, D-C1.

2026-10-11: D-C1 multi-family monitoring — fake slow `fg:`, `ad:` and `rpt:`
handles run together. A `wait_for_job` on any one family completes only that
family; the others stay `running` and time out on `timeout_seconds=0` probes. While
the wait is in flight `get_simulation_status`, `ui_open_panel` (navigate event) and
`list_project_runs` answer immediately through `_dispatch_real_tool_call`.
Cancelling the wait (`wait_cancelled`) stops no family. Tests:
`tests/test_nonblocking_multi_family_d_c1.py` (5 passed, repeated for
flakiness). No real solves. Foreground completion now also publishes a run
(issue 06). Still open: GridSpine/solve-queue family in the same matrix,
real-worker (not state-flip) multi-family run, D-I2 recovery across families,
UI/result matching per workload.
