<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07f — Whole-directory operations that still bypass the report lock

Status: needs-triage
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
