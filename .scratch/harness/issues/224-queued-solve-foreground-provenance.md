<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A queued solve stamps the foreground project's revision and org

Status: done (#1286)
Type: bug
Found by: SH-07b (2), the trajectory evaluation's decoy check (#1282): pilot:positive scored `wrong_project_results` 5 with every oracle check passing.

## Problem

The solve queue's dispatcher thread has no request, so `PyPSAService.get_active_context()` there resolves the process foreground, which may be another org's project. Two reads used it:

- `solver_service._revision_or_none()` → `dirty_state.revision()` stamped the queued solve's sizing record (`n.meta["sizing_record"]["revision"]`) with the foreground's revision.
- The queue's write-back (`solve_queue._apply_capture`, and the non-capture save) logs `_save_context`'s "save" entry, whose org came from `change_log_service._active_org_id()`: the foreground's org.

The other three reads #1282 found (`workflow_tools._owning_context` x2, `run_simulation`'s `_is_foreground`) are identity checks and #1282 exempts them by call site.

## Fix

- `_revision_or_none(network)` returns the revision of the context that owns that very network (the thread's solving context, or the active one), else None: no provenance rather than the foreground's.
- `change_log_service.owning_org(org)` binds a worker's org (a ContextVar, reset in `finally`). The queue binds its job's org, from the `org:uuid` key, around step 5's write-back. A key with no org has no owner, so that write-back logs nothing.

Tests: `pypsa-gui/backend/tests/test_solve_queue_owning_project.py`.

## Comments

GK B (36), plan review: GO WITH CHANGES, conditions Q1–Q5.

- Q1 (readers of a None revision). The sizing record's revision has two readers: `services/solver/fixed_sizes.py:185` (`run_label` passes it on as `source_revision` in `_state["sizing"]`; no frontend reads that field) and `services/results/grid_risk_production.py:690` (`_solve_stamp` omits a None field). Neither compares it with the current revision; `source_problem` does not read it. Test `test_a_record_without_a_revision_reads_as_no_provenance`. The /run foreground path is pinned by `test_a_foreground_solve_records_the_active_revision_as_master_did` (the active ctx owns the network, so the value is master's `dirty_state.revision()`).
- Q2 (no thread hop). `_owning_org` wraps step 5 in `_run_solve_job` on the dispatcher thread; `_apply_capture` and `_save_context` (routers/projects.py:2549–2941) start no thread and use no executor, and the "save" log is at routers/projects.py:2937 in the same call. The A/B test runs with the decoy as the process foreground while the request thread's own session holds A.
- Q3 (key forms). Every route queues `project_registry.registry_key(project)` = `org:uuid` (routers/solve_queue.py:193, routers/profiles.py:700, campus_electrical_service.py:1815, campus_island_cuts.py:320, gridspine_service.py:583); `projects.org_id` is non-null, personal workspaces are orgs, and local mode is one fixed org (`local_mode.LOCAL_ORG_ID`). A restored or requeued job carries its stored key. Only a hand-made job carries None or a bare name: no owner, so no entry. Tests `test_local_mode_still_logs_the_queued_save_under_its_one_org` (passes on master's code too) and `test_an_unresolvable_key_writes_no_save_entry`.
- Q4. The A/B test asserts the decoy's revision is unchanged and that no entry landed under the decoy's org or under None.
- Q5. Mutants: see the PR body.
