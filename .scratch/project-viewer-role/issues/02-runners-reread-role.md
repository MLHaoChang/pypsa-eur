<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# In-process runners re-read the caller's role mid-run

Status: done (merged #1806 runners, #1848 reports and campus)
Type: task
Source: GK B (63) on #1622 (F2), a known limit of 01 (VIEWER-ROLE-1, R3)

The viewer gate refuses a viewer at every route that starts a run, and the solve queue worker re-reads the requester's role when a job runs. The in-process runners record no requester. If an editor starts one and is then made a viewer, or is removed, the run still writes its results into the project. These runners are:
- studies, tornado and supply architectures;
- adequacy loops;
- business case and investment case;
- report jobs;
- campus island apply.

`assistant_tasks._may_write` does not re-check the role either. That file is in #1575/#1590, so its check lands with or after those PRs.

Done when each runner records its requester and refuses its project write with `project_view_only` once that person's role is `view` or gone, with a test per runner.

## Triage (2026-11-19, master 9f850d36)

Still true: `project_write_gate` is used by routes and `solve_queue`; the in-process runners record no requester. Gated: GK B (app/harness). Blocked by: none for the runners; the `assistant_tasks._may_write` check lands with or after #1575/#1590. Acceptance is the Done line above; slice per runner family if over 1,500 lines (PR-SIZE-1).
