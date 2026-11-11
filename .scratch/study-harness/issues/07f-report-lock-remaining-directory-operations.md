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

What 07f-a still leaves: the closing set is per process. A report writer in another process that takes its lock inside the quiesced window is not refused by it. After a delete or a rename it meets the missing-directory backstop and fails; during a snapshot removal it writes nothing there, since no writer writes into a snapshot.

11 Nov, GK B (46) plan verdict: GO for 07f-a under C1–C5. 07f-b goes ahead with these answers:

- **Q1.** Delete and rename refuse 409 `report_busy` while a report job runs. The detail names the job and says to abort it in Reports. Nothing discards a user's running job.
- **Q2.** Saved then refused → `failed`, `error_kind: report_busy`, `version: n`; never `done` (the #1170 rule). Every reader of version n (version list, export, regenerate base, strict/audited claims) shows that it has no settings or receipt and never borrows version n-1's. One test per reader.
- **C1.** "Closing" holds across processes. Chosen: (a) a marker file `reports/.closing`, written while the locks are held. `report_lock` checks it under its own file lock. The marker is not held open. The operation then moves the directory aside (`os.replace` to a dot-named tombstone, or to the new name for a rename) before it removes anything, so a writer keeping the old path finds no project directory (the backstop). Test: a writer that bypasses the in-process state refuses during the window.
- **C2.** No TOCTOU on the job check. `running_for(dir)` runs after `get_lock()` and the marker. `_launch` refuses a job for a directory that is closing, under the same `_SLOT_LOCK` hold as its claim. Test: a job starting between the check and the quiesce.
- **C3.** The lock order is written down, with a two-thread timed test that goes red when it is reversed.
- **C4.** Every waiter on `get_lock` and on the report locks is listed with its bound, and the PR says which can now time out where they could not before.
- **C5.** Each new guard is mutated on its own and run on its single test; the table goes in the PR body. Prune skipping a busy snapshot must not let the count grow across repeated prunes.

11 Nov, 07f-a built (`claude/sh-07f-report-lock-dirs`):

- **What was built.** `store.reports_closed(dir)`: the report locks (3 s), then `reports/.closing` written while they are held, then the locks released. Every `report_lock` refuses `ReportBusy` while the marker is fresh; a marker older than 600 s (a crash) is ignored; the bound leaves room for a cascade delete closing many busy scenarios in turn. Delete, snapshot delete and fork delete move the directory to a hidden `.pypsa-deleting-<hex>` sibling under the locks and remove it after them. Rename moves it to its new name and clears the marker there. `report_lock` makes `reports/` only, never the project directory, so a writer whose project is gone gets `ReportProjectGone` (404 `report_not_found` at the report routes). A bundle copy still makes its own destination.
- **Deviation from the plan, legacy import.** It copies without taking the report locks, skipping `reports/.<id>.lock` and `.closing` in both the copy and its verification. A legacy tree is outside the registry, so no report writer of the app reaches it, and taking a lock would write lock files into the user's original tree, which that module guarantees never to change. The claim (`shutil.move` into the registry) does take `reports_closed`. `rebase_row` (the CLI that copies a registry row's directory) does too, from the copy to the commit, since a running app can write that directory.
- **Fork delete** (`forks.delete_fork`) is a project delete too, and gets the same closing, job refusal and move-aside. A fork's own empty lock files come from `copy_reports` locking the destination, as a snapshot's do (07e). None of the base's lock files is copied.

**C3, lock order.** In order, outermost first:
1. `PyPSAService.get_lock()` (the active context's `mutation_lock`).
2. `reports_copy_lock` / `reports_closed`: each report's `report_lock` in sorted `(reports dir, id)` order. Each one is the thread `RLock`, then the file lock `reports/.<id>.lock`.
3. A job's own `job.lock`, which no copy or close takes.

No report writer takes `get_lock` inside a `report_lock`; the 07e holder list is unchanged. `reports_closed` releases the report locks before it yields, so the move runs under `get_lock` alone, with the marker refusing writers. Tests: `test_{delete,rename,snapshot_delete}_takes_the_network_lock_before_any_report_lock` hold the network lock in a second thread while the operation waits, and a report writer still gets its lock at once. Each is red with the two swapped (mutants M8, M11, M14).

**C4, waiters.** New holders of `get_lock`:
- project delete: one 3 s close per target, plus the moves;
- snapshot delete: one 3 s close plus the move;
- rename: one 3 s close plus the DB commit and the move;
- fork delete: one 3 s close plus the move.

The removal of a moved tree runs after the lock (`test_the_moved_tree_is_removed_outside_the_network_lock`). Prune runs inside a snapshot create's existing hold, as before 07f; it now also waits up to 3 s per busy snapshot.

The timed waiters on the same lock, any of which can now time out during one of those holds where before 07f it could not:

| Waiter | Bound | On timeout |
|---|---|---|
| `harness/run_loops.py:491` `agent_turn` guard | 5 s | card withdrawn |
| `harness/continuation.py:442` `settle_engine` | 5 s | `"running"`, next poll retries |
| `harness/runner.py:1041` `take_locks` | 5 s | busy |
| `services/run_envelope.py:1755`, `:1757` | 5 s | refused |
| `services/run_envelope.py:2217`, `:2221` `answering` | 5 s | ordinary write |
| `services/study/runner.py:479`, `tornado_runner.py:298` | 5 s | 409 `solver_in_flight` |
| `routers/results.py:1147` | 5 s | 409 |
| `routers/results.py:2191`, `:2944` | 2 s | `network_busy` / 409 |
| `routers/simulation.py:783` | 2 s | `network_busy` |
| `services/adequacy/eh_study_runner.py:302` | non-blocking from the polled caller (`flow_service.py:490`); blocking by default | `EhStudyLockBusy` |
| `harness/durable.py:21` `network_guard` | 1 s | 409 `assurance_context_changed` |

`routers/simulation.py:1162`, `:1275` and `:1430` are non-blocking and only defer or report. The worst new hold is about 3 s × targets on a busy cascade delete; with no report busy, it is the moves alone.

No caller passes `report_lock` a timeout. Writers keep `LOCK_TIMEOUT_S` = 30 s, and they meet these holds only through the closing marker, which refuses at once rather than making them wait. The three 5 s branches are tested in 07f-b.

Code review (mp-code-review, 11 Nov), fixed:
- `rebase_row` now closes its source.
- The moved trees of a delete are removed in a `finally`.
- The stale bound is 600 s.
- One `report_busy_http`.
- `store.is_lock_artifact` replaces the private `_lock_file_id` in legacy import.

Left as follow-ups:
- No sweeper removes a `.pypsa-deleting-*` directory left by a crash or a failed removal. It is hidden, logged and outside every listing.
- A partly failed cross-device `shutil.move` in the claim can leave `.closing` in the partial copy; it goes stale.

11 Nov, GK B (46) review of #1373: NOT OK @0c6ecd01, D1. Fixed:

- **D1.** The close locked only the reports that already existed. A `create_report` for a new id could take that id's lock after the close listed the reports and before the marker existed, and then write inside the closed window. Now, after writing the marker, `reports_closed` re-lists the ids. A writer's lock file exists before it locks, so every such writer shows up. The close takes each new id's lock once, past its own marker, inside the same 3 s deadline; if one is still busy at the deadline it refuses `ReportBusy` and removes the marker. `create_report` and `write_figure` make their directory without `parents=True`, so neither can rebuild a removed report or project.
- **Tests.** One forces the interleaving: a writer locks a new id just before the marker is written, and the close does not yield until it is done. One shows that such a writer held past the deadline refuses the close. One shows `write_figure` never rebuilds a removed report. Each is red with the fix removed (mutants M25–M28).
- **Minor: the waiter table** now includes `eh_study_runner.py:302`.
- **Minor: known limit.** A `rebase_row` copy that runs past `CLOSING_STALE_S` (600 s), a tree of many GB, lets writers back in before the copy ends. The CLI then commits a copy that may miss their last write; the source stays, so `--forget-legacy` should wait for a check.

11 Nov, 07f-b (successor session_01G7qCuj3zWPNptek5GKaSVq). The code holds no per-version settings or receipt: `meta.json` has one copy of `generation`, `template_mode`, `template_language` and `mapping_plan`, and `save_version` carries them all. So a refused write after a save left version n showing version n-1's. GK B (46) gave the plan GO under E1–E5:

- **E1.** The missing state is explicit. The job's save writes `settings_unrecorded: {version: n, fields}` and clears those fields, in the same locked meta write; the job's last write (its receipt) removes the mark. A user-created report, also `generation: null`, has no mark.
- **E2.** Export of the version a mark names, with `mapping_plan` among its fields, refuses 409 `report_settings_unrecorded` (option (i)). No job writes `mapping_plan` today, so this guards a future job.
- **E3.** The regenerate base and the strict check read version n's document, never the meta, and the mark does not block them. One test each.
- **E4.** `pending` holds exactly `generation` plus this job's `meta_updates` keys. Every other carried field, and every field on a user edit, still carries. Tested.
- **E5.** An older version k < latest still exports with the latest meta's `mapping_plan`: ticketed as [43](43-export-older-version-borrows-latest-mapping.md).

M26 (07f-a survivor): the close's drain now has a timing test with an upper bound well under 30 s.
