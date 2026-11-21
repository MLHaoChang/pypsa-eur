<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# add_upload serialises only within one process

Status: ready-for-agent
Type: task
Source: GK B (63) on #1622 (export under a foreign lock); predates the viewer role

`services/upload_service.add_upload` serialises on a process-local `threading.RLock` (`_project_upload_lock`). Two API processes that upload the same bytes to one project at once can both miss the sha256 dedup and store the file twice. This affects every upload writer, not only report export.

Done when `add_upload` also holds a cross-process `harness.durable.file_lock` (GK B ruling #1070), a busy lock maps to a stated error for every caller, and a two-process test shows one stored copy.

## Triage (2026-11-19, master 9f850d36)

Still true: `upload_service` guards only with the process-local `_project_upload_lock`. Gated: GK B. Blocked by: none. Acceptance is the Done line above.
