<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07e — Bundle copies of `reports/` take the per-report lock

Status: needs-triage
Type: task
Gated: yes (GK B)
Split from: [07c](07c-report-comment-revisions.md), GK B (38)'s non-blocking finding on #1300

## Problem

07c put every writer of `reports/<id>/` under `store.report_lock` (a per-report file lock, `reports/.<id>.lock`). Bundle copies don't take it yet. Snapshot restore (`routers/snapshots.py`, `_copy_bundle_dirs(snap_dir, project_dir)`), bundle import, and save-as/copy (`routers/projects.py`) rewrite `reports/` through `_copy_bundle_dirs` with no lock. Snapshot create reads `reports/` the same way, also with no lock.

So a restore can run during an address, a template bind or a report job. The copy can then mix one report's version files, `meta.json` and `comments.json` from before and after that write. One possible result is a comment marked addressed against a version the restore removed.

The gap predates 07c, which didn't widen it.

## What to build

- Each copy that writes or reads a project's `reports/` holds `store.report_lock` for each report it copies, or one project-level guard the report writers also respect.
- A copy that can't take a lock within the timeout refuses with 409 `report_busy` and writes nothing, as the report routes do.

## Acceptance criteria

1. A snapshot restore that starts while an address holds the report lock waits or refuses. It never interleaves with the address's files. Tested with a held lock.
2. The same holds for bundle import and save-as/copy.
3. Snapshot create copies a consistent set of report files for each report.

## Comments

10 Nov: built on `claude/sh-07e-report-copy-lock`. GK B (42) gave the plan GO with changes L1–L7; what was decided:

- **Per-report locks, no second lock type.** `store.reports_copy_lock(src, dst, incoming=)` takes 07c's `report_lock` on every report a copy reads (`src`) or replaces (`dst`), in sorted `(reports dir, id)` order.
- **L1, lock order.** Copies take `PyPSAService.get_lock()` first and the report locks second. No report writer takes the network lock, a project context or a solver lock inside a report lock: the holders are the five store writers, `revisions.add_comment` / `withdraw` / `address` and the job's generation meta, and inside it they only read and write files under `reports/<id>/` (the job also takes its own `job.lock`, which no copy takes). `test_a_writer_finishes_while_a_copy_holds_the_network_lock` times each writer against a copy holding the network lock.
- **L2, bound.** All of a copy's report locks share one `COPY_LOCK_TIMEOUT_S` = 3 s deadline, because the network lock may be held meanwhile. The bound covers a thread of the same process too (a 07c writer still waits for a thread without one).
- **L3, archive ids.** Bundle import validates every `reports/<x>/…` member with `validate_report_id` before the project row exists; anything else refuses 400 `invalid_report_id`. A bundled `.<id>.lock` is skipped.
- **L4, one rule.** A copy touches only the reports whose locks it holds. A report created after the lock set was taken is neither copied nor removed: it serialises after the copy. Lock files and stray files in `reports/` are never copied, removed or exported. The set is re-read once the locks are held; a change releases them and starts over, at most 5 times, then `report_busy`.
- **L5, re-entrancy.** `_copy_bundle_dirs` takes the locks itself as a backstop. Nested in the same thread it keeps the outer call's id set, and its release leaves the outer hold in place. A `ReportBusy` from it is re-raised by the snapshot, restore and save callers, which swallow every other copy error.
- **L6, cleanup.** `reports/` is no longer removed and copied whole (that deleted the held lock files). Each report is built in a temp sibling and swapped in; a failure leaves that report as it was and no temp directory. So a copy that fails part-way leaves each report either old or new, never mixed, but not the whole folder as it was. A busy refusal happens before the copy's first write.

What a refusal can still leave behind:

- Restore checks the locks first, before it saves unsaved live edits and makes the "before-restore" snapshot, then takes them again around its own writes. A writer that arrives between the two gets the restore refused with that save and that snapshot already made; no project file is replaced.
- Save-As and Save a Copy refuse before the network is written, but the route has already made the new project's row and empty directory, as it has for the save's other refusals. Bundle import likewise: it writes into a new project directory, so its refusal can only be forced in a test, and the row made just before it stays.

Side effects to know about:

- A save to another name now holds the context's mutation lock for the whole save, and snapshot create holds it across the prune and the capture.
- Export can now answer 409 `report_busy`, the chat export tool included.
- Locking a snapshot's or an export source's reports leaves `.<id>.lock` files there.

Not covered: the study option forks (`services/study/forks.py`) walk the bundle themselves; `services/legacy_import.py` and `import_unclaimed_project` move a whole directory in from outside the registry, where no report writer can reach it yet.
