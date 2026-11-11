<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07f — Whole-directory operations that still bypass the report lock

Status: ready-for-agent
Type: task
Gated: yes (GK B)
Split from: [07e](07e-report-copies-take-the-report-lock.md), GK B (43)'s finding F3 on #1345

## Problem

07e put the bundle copies of `reports/` under `store.report_lock`. These operations still move, copy or remove a project's `reports/` without it:

- Project delete removes the project directory with `rmtree`. A report job still running re-creates `reports/<id>/` afterwards, because `report_lock` makes the `reports/` directory.
- Project rename moves the directory with `os.replace` while a writer may hold a lock file inside it.
- Snapshot delete and `_prune_oldest` (`routers/snapshots.py`) remove a snapshot directory whose reports a restore may be reading under its locks.
- The study option forks (`services/study/forks.py`) walk the bundle themselves: they copy `reports/` unlocked, and copy the `.<id>.lock` files with it.
- `services/legacy_import.py` (`copytree`) and `import_unclaimed_project` (`shutil.move`) bring a whole directory in from outside the registry.

## What to build

- Each operation above takes the report locks (`store.reports_copy_lock`) or refuses 409 `report_busy`, in the order 07e fixed: network lock first, report locks second.
- Delete and rename decide what happens to a report job that is still running for the project.
- The forks copy reports through `store.copy_reports`, so no lock file travels.

## Acceptance criteria

1. Deleting or renaming a project while a report writer holds its lock waits or refuses; no `reports/<id>/` reappears after a delete. Tested with a held lock.
2. Snapshot delete and prune do not remove a snapshot a restore is reading from.
3. A study option fork carries the reports and no `.lock` file.

## Added by GK B (44) on #1345 (condition of its OK)

Not directory operations, but left open by 07e's longer holds:

- **Typed kind on the job record.** A report job that meets `ReportBusy` ends `failed` with the plain text "report <id> is being written" and no `error_kind` (`services/reports/report_job.py`, the `except Exception` around the store writes; only `ReportDisclosureMissing` sets a kind). Put `report_busy` on the record.
- **Failed though saved.** When `update_meta` or the generation-meta write is the call that is refused, `save_version` has already written the version, yet the job reports `failed` with no `version` on its record. Decide what the record says and test it.
- **Timeout branches with no test.** These wait about 5 s for the network lock, which a save to another name now keeps for the whole save:
  - `services/run_envelope.py` `answering` (near :2217): yields without the lock, so the user's write runs as an ordinary write;
  - `harness/run_loops.py` `agent_turn` guard (near :491): returns False, the card is withdrawn and the tool does not run;
  - `harness/continuation.py` `settle_engine` (near :432): returns `"running"` and the next poll retries.

Acceptance, added:

4. A busy report gives the job record `error_kind: report_busy`; the saved-then-refused case has a stated outcome and a test.
5. Each of the three timeout branches has a test of what it does.

## Comments

11 Nov, plan (SH-07f, sent to GK B before building). Two slices, both GK B, each well under 1,500 lines (PR-SIZE-1):

**07f-a: directory operations (criteria 1–3).**

- `store.quiesce_reports(project_dir)` (new, `services/reports/store.py`). It takes `reports_copy_lock(None, project_dir)`, so it waits up to `COPY_LOCK_TIMEOUT_S` for a writer in progress, then refuses `ReportBusy`. Once the locks are held it marks the directory closing in a process-wide set and releases the file locks, then yields. `report_lock` refuses `ReportBusy` for a directory in that set. The file locks are released before the operation because Windows cannot remove or move a directory that holds an open lock file.
- `report_lock` no longer recreates a missing project directory. It makes `reports/` only when the project directory exists; otherwise it raises `ReportStoreError` ("project is gone"). This is the backstop for a writer in another process, or one that starts after the check: it fails instead of making `reports/<id>/` again.
- **Project delete** (`_delete_project_db`): next to the `solver_in_flight` check, it refuses 409 `report_busy` while a report or mapping job runs for any target. The decision: refuse, not abort. The user aborts the job in Reports, then deletes, the same as the solve queue. `report_job.running_for(project_dir)` is new: `_launch` keeps the running job's directory beside `_ACTIVE`, off the wire record. Each target's `_force_rmtree` then runs under `PyPSAService.get_lock()` and `quiesce_reports` (network lock first). Busy → 409 `report_busy` before the first target is removed.
- **Project rename** (`project_registry.rename_project`, called from the route and the chat tool): the same job refusal before the commit, then `get_lock()` and `quiesce_reports(old_dir)` around the `os.replace`. Busy → 409 `report_busy` before the row is committed.
- **Snapshot delete and `_prune_oldest`**: `get_lock()`, then `quiesce_reports(snap_dir)` around each `_force_rmtree`. Restore reads the snapshot under `get_lock()` and `reports_copy_lock(snap_dir, …)`, so neither delete nor prune can run while a restore reads it. Delete busy → 409 `report_busy`. Prune skips a busy snapshot as it skips an `OSError` one. Fix the two UP017s only if those lines are touched.
- **Study option forks** (`forks._copy_walk`): `reports` is left out of the walk and copied with `store.copy_reports(src, dst)`, so no `.lock` file travels. Busy → the fork's existing failure path (`_force_rmtree` of the half-made fork) with `report_busy`.
- **Legacy import** (`legacy_import` `copytree` ×2) and **`claim_legacy_project`** (`shutil.move`): copy under `reports_copy_lock(source)` with an ignore for `reports/.<id>.lock`. The move runs under `quiesce_reports(source)`. Busy → the existing per-project skip/report path (import) or 409 `report_busy` (claim).
- Tests (`tests/test_report_lock_directory_ops.py`, new): for each operation, a second thread holds a report lock and the operation waits and then succeeds (released early) or refuses `report_busy` (held past the bound). Delete with a running job → 409, and after a delete a held-back writer cannot make `reports/<id>/` again. Rename moves no lock file and does not run while a lock is held. Snapshot delete and prune leave a snapshot that a restore holds. A fork carries the reports and no `.lock` file. Each test is red on master.

**07f-b: job record and timeout branches (criteria 4–5).**

- `run_report_job`: `ReportBusy` → `error_kind: report_busy` on the record (and `ReportStoreError` "project gone" → plain failed).
- Failed though saved: when `update_meta` or the generation meta is refused after the version is saved, the record is `status: failed`, `error_kind: report_busy`, `version: <n>`, with an error that says version n was saved and only its settings or receipt were not. The client can open the version; the run step settles as failed. Tested for both refusal points.
- One test each for the 5 s network-lock timeout branches: `run_envelope.answering` (yields without the lock), the `run_loops` `agent_turn` guard (returns False, card withdrawn, tool not run), and `continuation.settle_engine` (returns `"running"`).

What 07f-a still leaves: a report writer in another process that arrives inside the quiesced window is not refused by the closing set; the missing-directory backstop covers delete, but for rename such a writer can land in the old path's re-made `reports/` only if it re-creates the project directory, which the backstop forbids.
