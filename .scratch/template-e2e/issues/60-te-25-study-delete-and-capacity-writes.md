<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Project delete and Capacity-bounds writes during a study

Status: needs-triage
Type: task
Review: GK A + GK B
Source: te 25 hand-off (#2114); GK A (40) rules in `docs/agents/review-gk.md` (filed 2026-11-28 from the coordinator's follow-up lists)

## What is left

- Project DELETE during a study may still answer 409 (no `abortRunningSim` on that path). The delete guard must check the project's own resident context (`get_context(registry_key)`).
- Backend Capacity-bounds writes during a study are unchecked; the write must match `ctx.loaded_project == proj.name` exactly.

## Comments
