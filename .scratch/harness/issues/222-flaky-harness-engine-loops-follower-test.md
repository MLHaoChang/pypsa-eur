<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Flaky `test_harness_engine_loops` follower test

Status: done
Type: bug (test)
Found by: Seen in CI; owner list DOCS-1.

## Problem

A follower test in the engine-loops suite fails intermittently. Root-cause it (do not skip or loosen it, per steward CI rules): likely a timing or ordering assumption between loop and follower. Details: ask the coordinator.

## Where to look

`pypsa-gui/backend/tests/test_harness_engine_loops*.py`, the follower test. Details: ask the coordinator.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `pypsa-gui/backend/tests/test_harness_engine_loops*.py` follower test.
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK B.
- Size: S.
- Root-cause the timing/ordering assumption; never skip or loosen the test.

### 2026-11-07: root cause and fix (harness-222)

- Cause (by reading; not reproduced, 20 of 20 runs of the affected tests pass on old and new code): the test helpers `_hold_lease`, `_past_deadline` and the H3 revision pop rewrote the task file with a bare `write_text` (truncates first, no lock). A follower `_read` of the empty file gives `step is None` and the follower exits silently; a concurrent runner save can also overwrite the test's takeover. The stale-follower test also asserted after a fixed `sleep(3 * ENGINE_POLL_SECONDS)`.
- Fix (test only): `_mutate_task` = `runner._file_lock(path)` then `runner._update` (task lock, atomic save); the sleep is now a bounded `wait_until(not continuation.following(ticket))` before the unchanged assertion.
- GK B plan verdict, GO WITH CHANGES: T1 reuse `_update` under `_file_lock` (done, no new locking code); T2 lock order file lock then `tasks._LOCK`, matching `run_explore.py:571` and `run_envelope.py:1381,1837,1920` (no path nests the other way); T3 bounded wait, assertion kept; T4 no stress test added, 20x loop evidence only; T5 no production change.
