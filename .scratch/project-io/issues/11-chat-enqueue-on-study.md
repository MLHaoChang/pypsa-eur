<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 The chat tool `solve_queue_enqueue` is not refused on a Study project

Status: needs-triage
Blocked by: none
Source: follow-up from 08.

## Problem

08 stops every UI entry point to Run LOPF on a planning → dynamics Study: the sidebar and header hide it, and a run request gives a toast. The backend does not refuse a LOPF enqueue for a Study project, so the assistant's `solve_queue_enqueue` tool can still queue a network solve for a project that has no network. 08 left `solve_queue.py` alone on purpose (shared with other open work).

## Acceptance criteria

- [ ] Enqueueing a LOPF for a Study project is refused by the backend with a named error kind that says to use Run study.
- [ ] The chat tool surfaces that refusal; a test covers both the route and the tool.

## Likely files

`pypsa-gui/backend/services/solve_queue.py` (or the enqueue route), the harness tool for `solve_queue_enqueue`, `pypsa-gui/tool-error-kinds.d/`.
