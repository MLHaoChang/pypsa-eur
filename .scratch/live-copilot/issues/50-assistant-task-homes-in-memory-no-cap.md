<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-50 `_HOMES` is in memory with no size cap

Status: needs-triage
Source: #1945 (pre-existing, found in review)

## What

`_HOMES` maps a task id to its project and lives in process memory with no size cap. After a restart it is empty, so a chat-bound task fails closed as `unreadable` until the user returns to its project (the read at line 205 then refills the entry). Two asks: bound the map, and decide whether a restart should rebuild it from the task store so the task reads without the user's visit.

## Where

`pypsa-gui/backend/services/assistant_tasks.py` (`_HOMES`, line 172; written at 165 and 205; read at 179).
