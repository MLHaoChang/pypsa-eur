<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Job holds across sessions, tabs, replicas and restarts

Status: ready-for-agent
Type: task
Found by: AA-04 S5 (`04-action-and-job-outcomes.md`); GK B (30) N1.

## Problem

Issue 04 S5 holds a job call whose outcome is unknown on the in-memory
`ChatSession` (`harness/job_holds.py`). A new chat, another tab, a second
replica or a restart has no hold, so it can start the same operation again
(#1070: fail closed needs a cross-process record). Only `solve_queue_enqueue`
reads a persisted record (the solve-queue store's queued and running rows for
the project). Every other job tool (the `execution` and
`execution_long_running` tiers) has none: the solve thread is per process, and
studies, sweeps, reports and profile generation keep no store row keyed by
project and arguments.

## Wanted

A persisted record of an uncertain job call (project, tool, G3 fingerprint,
job id when known, opened at), written when the hold opens and cleared on the
same evidence, which P2 reads when the session has no hold; or, per tool, an
existing store row that identifies the operation. Test the new-session case
for each tool family.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `harness/job_holds.py`, solve-queue store, study/sweep/report/profile-generation job tools.
- Blocked by: none.
- Model tier: opus.
- Gated: GK B; plan to GK B first.
- Size: L.
- Needs a cross-process record keyed by project and arguments (#1070); L: the builder proposes a split.

### 2026-11-08: GK B (35) plan verdict: GO WITH CHANGES

Plan (session_01G7qCunVbDbeiPbRRFtjmd9): a DB table `assistant_job_holds`
(migration 0012) with one row per uncertain job call (project, tool, G3
fingerprint, job id, opened, opened_at, opened_by_user_id, owner_replica_id,
lease_until), written when the hold opens and deleted on the evidence that
clears it; a process registry of the evidence so a hold outlives its
ChatSession; P2 reads the rows when the session has no hold.

Q2: the DB table and migration 0012 (with H8). Q1: an owner-liveness lease is
not "time alone" under N3, but only for work that dies with its owner (H1).

Split: S1 = the record, the write, the evidence registry, the clear and the
same-process new-session read. S2 = the cross-replica lease and restart, with
H1–H3. One PR each.

- H1. A lapsed lease clears only work that died with the owner. S2 adds a
  table, one row per job tool (28): its worker kind (in-process thread or
  future; child process killed with the parent; durable or queue-backed;
  external) and its clearing evidence. A lapse clears only the in-process
  kinds and child processes proven to die with the parent (a test kills the
  parent and asserts the child is gone). Every other kind clears only on its
  own durable terminal state or the user's repeat card. Test one durable
  family: lapsed lease, job still running → still held.
- H2. Lease renewal: one renewer per process that catches and logs every
  exception and keeps running; renewal and expiry compare against one clock
  (the DB server's, or state that replicas share time.time() and name the skew
  risk); clearing a lapsed record is a compare-and-delete on (id,
  owner_replica_id, lease_until), so a renewal that lands first wins (#1083,
  #1088). Tests: a renewal raises once and still renews on the next tick; a
  renewal racing a clear leaves the record.
- H3. Restart: a new process reads its own old records as another owner's;
  they clear only by H1 after the lapse, never on boot alone.
- H4. A failed write is visible: the hold stands in-session, the tool result
  says in plain words that the hold is not shared with other chats, the log
  names the error kind. Test: the write raises, the hold blocks in-session,
  the result carries the note, and a second session is not told it is held.
  The PR names that the window between the call starting and its timeout is
  unprotected across sessions, as today.
- H5. Registry entries are removed exactly on clear (test: nothing left in the
  registry or the DB). State what bounds the registry when a future never ends
  (held until a repeat card), with a test that the card removes both.
- H6. Every read failure holds (N4): the table missing, a DB error and a
  malformed row each deny with the typed kind, never a 500 and never "no
  hold"; a fail-closed test at the catching site, with a base-class mutant.
- H7. No cross-user leak: the job id appears only to opened_by_user_id (test
  with two users in one project); the conflict message never echoes the other
  user's args or fingerprint.
- H8. Migration: grep the open PRs for another 0012 first (none on
  2026-11-08); test upgrade and downgrade; the FK cascade on project delete
  gets its own test; run tests/test_packaging_requirements.py if a new import
  reaches the backend; no new route.
- H9. Mutants, each with its killing test: the record write skipped; the clear
  left out of approved-card handling; owner == me without a registry entry
  read as clear; in S2, a lapse clearing a durable family; a renewal ignored in
  the compare-and-delete.
