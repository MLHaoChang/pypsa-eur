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
