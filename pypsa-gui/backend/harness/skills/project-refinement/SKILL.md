---
name: project-refinement
description: Create or refine a network project, check prerequisites, solve baseline and sensitivity scenarios, and interpret bounded simulation or GridSpine evidence.
---

Lead the workflow using existing project and engineering tools. Keep the
baseline intact and ground every conclusion in a completed run's evidence.

1. Call `project_readiness`. Check project kind, validation blockers, solver
   availability, saved/dirty state, and result freshness. If unbound, use
   `create_project_from_template`, or obtain missing user choices with `ask_user`.
   Use project names returned by tools instead of retyping long identifiers.
2. Use `use_toolset` to expose network or simulation tools as needed. Parameterize
   the intended project through existing handlers, validate, run with
   `run_simulation`, and save the completed baseline with `save_project`.
   Honour the harness's confirmation cards; this procedure never approves them.
3. For a static network sensitivity, use `run_sensitivity_sweep` with 1–4 cases
   and explicit typed changes. Choose meaningful parameters from the user's goal
   and observed constraints. Existing time-series overrides still apply to static
   edits; use the original time-series tools for profile changes. Do not claim a
   static demand change affected a time-series demand profile without checking.
4. Use `wait_for_job` for each returned solve or GridSpine job. The wait happens
   locally. If it returns pending, report the job ID and end the turn. On failed,
   aborted, or interrupted jobs, report the actual state and investigate the error;
   never read unfinished results as successful. Cancelling a wait does not stop a
   job; stopping a job is the separate `solve_queue_abort` action.
5. Use `get_study_evidence` in simulation mode with the baseline as **compare_to**
   to compare saved scenarios. Explain the numerical change and its engineering
   cause. Repeating a sweep reuses solved cases only when provenance and inputs
   still match. Inspect partial failures and saved case IDs before retrying.
6. For planning-to-dynamics work, use `gridspine_create_study`, `activate_project`,
   and `use_toolset` with the gridspine domain. Project rebinding refreshes the
   eligible catalogue on the next model request within the same turn. Configure
   the source through `gridspine_set_dispatch_source`, then run the existing
   pipeline using `gridspine_run_pipeline` and wait for its job.
7. Read ranked snapshots, capacity, and connection evidence through
   `get_study_evidence`. Use **check** = connection for overall pass/fail counts;
   those counts cover every matching row before paging. Filter **status** = fail
   and page details to identify the failing hour, contingency, and constraint.
   Treat preexisting violations as blockers, rather than usable headroom.
8. Refine justified study parameters with `gridspine_update_config`, rerun and
   wait, then compare fresh evidence. Old evidence becomes unavailable after
   configuration/source changes. Steady-state screens do not establish dynamic,
   fault-ride-through, or regulatory compliance; say what remains to be assessed.
