<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 48 — The location gate does not cover the live-network runners

Status: ready-for-agent
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
