<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 48 — The location gate does not cover the live-network runners

Status: done (STUDY-GATE-1, live runners). What each start solves and where it is gated:

- `start_fmea_sweep` (services/adequacy/fmea_sweep_runner.py:64,80): the live foreground network `n` under `PyPSAService.get_lock()` (class-B/C re-solves, closing base re-solve). Gated first with `active_refusal()`.
- `start_front` (front_runner.py:82,107): `run_front_study(n, lock, ...)`, the live network. Gated first with `active_refusal()`.
- `start_frontier` (frontier_loop_runner.py:81,114): `run_frontier_sweep(n, lock, ...)`, the live network. Gated first with `active_refusal()`.
- `start_coupling_loop` (coupling_loop_runner.py:235,466-486): the validation reads one snapshot of the live `n`, the loop re-solves the live `n` (`run_simulation(final_cfg, n, lock, ...)`). Gated first with `active_refusal()`, which reads that same network and its project dir.
- `start_margin_loop` (margin_loop_runner.py:218,781-800): same as the coupling loop (live `n`, closing `run_simulation` on it). Gated first with `active_refusal()`.
- `start_mc` (mc_loop_runner.py:139-144): solves nothing; takes ONE snapshot of the live network under the mutation lock and the worker reads plain arrays only. The gate is inside that same lock hold, on that `n` and `active_project_dir()` (`site_location.refusal`), not a separate read.
- `start_investment_case` (finance/investment_case_runner.py; the build closure `_ic_build_case`, routers/results.py:2331-2336): the case re-solves the site-today baseline on a `_network_copy` of the live network (`baseline.solve_baseline`, services/results/baseline.py:337), so it solves. Gated first with `active_refusal()` (the copy is of the same foreground network). The route answers 409 `not_solved` before the start on an unsolved network, so an unlocated project is refused by the gate only once it has a solved dispatch.
- `start_business_case` (business_case/runner.py:346; worker at :432-442): values the stored investment-case report (needs a current one, routers/results.py:2980; compares the case hash; reads the existing solve under the lock), so the start itself is not gated. Its worker rebuilds the case through `_ic_build_case`, which re-solves the baseline on a cache miss (routers/results.py, `solve_baseline` on a `_network_copy` of the live network). That re-solve is gated where it starts: `site_location.refusal(snap, project_dir)` on the copy it solves, `project_dir` read on the request, refused as `FinanceRefused("site_location_missing", ...)` (the study record's typed refusal; a worker cannot answer 422) before any LP. The investment case rebuild shares it.
- Solve queue, both ends. Enqueue: `routers/solve_queue.py` `_refuse_unlocated` in `enqueue_solve` (after `require_writable`, the lock and study checks and the on-disk network check, before the config snapshot, capture and `enqueue_unique`) and in `requeue_job` (before `enqueue_unique`), on the ENQUEUED project: `site_location.queued_refusal(project_key, project_dir)` uses the resident context's network when resident, else the saved `network.nc` (a confirmed anchor short-circuits with no load; the resident read takes the network lock non-blocking and reads unlocked when a solve holds it, so enqueueing the project that is solving never waits). The chat `solve_queue_enqueue` calls `enqueue_solve`, so it inherits it; there is no chat requeue tool. Dequeue: `_run_solve_job` (services/solve_queue.py) re-checks `site_location.refusal(ctx.network, job.storage_dir)` on the network it is about to solve, before it claims the context; the job fails with `condition = "site_location_missing"` and `error = "site_location_missing: <message>"`, never solved. Other job kinds (gridspine, campus, profile, blender) are not network solves and are not gated.

Tests: `tests/test_live_runner_location_gate.py`.
Owner: unassigned
Size: M
Follows: 47 (owner decision STUDY-GATE-1: "Studies now, rest next")

## What

These starts solve the active network and still run on an unlocated project. Gate each with `site_location.active_refusal()` (422 typed `site_location_missing`) at the top of its start function, before any worker or record, with one test per start (unlocated, draft anchor, confirmed anchor) and a gate-removed mutant each:

- `routers/results.py` `post_fmea_sweep` -> `services/adequacy/fmea_sweep_runner.py` `start_fmea_sweep`
- `post_front` -> `front_runner.start_front`
- `post_frontier` -> `frontier_loop_runner.start_frontier`
- `post_mc` -> `mc_loop_runner.start_mc`
- `post_coupling_loop` -> `coupling_loop_runner.start_coupling_loop`
- `post_margin_loop` -> `margin_loop_runner.start_margin_loop`
- `post_investment_case` -> `finance/investment_case_runner.start_investment_case`
- `post_business_case` -> `business_case/runner.start_business_case`
- `routers/solve_queue.py` `enqueue_solve` (and the chat queue tools), on the enqueued project's network and directory

Confirm for investment_case and business_case that they solve before gating.

## Acceptance

Each start refuses an unlocated project with the typed 422 and creates nothing. GK review as for 47.
