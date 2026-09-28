# MC / Frontier / FMEA-Sweep Study Router Lift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Cut `post_mc` (274 LOC), `post_frontier` (95 LOC), and `post_fmea_sweep` (85 LOC) out of `routers/results.py` into dedicated `*_runner.py` modules without changing behaviour.

**Architecture:** Handlers keep mesh refuse + FastAPI decorator; runners own validation, record, and worker. State injected (`solver_state`, `publish_study`; frontier/sweep also `state_update`).

**Tech Stack:** Python 3.12 / FastAPI / PyPSA / pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-mc-study-router-lift-design.md`

## Global Constraints

- Branch: `cursor/mc-study-router-lift-ce8d`
- Strictly behaviour-preserving.
- Never edit an existing test for the refactor (except if a source-scan goes vacuous — then retarget it, as F1n2).
- Runner never imports `routers.*`.
- Gate: adequacy endpoint suites + study-runner tripwire; failing set unchanged.

## Task 1: MC runner

- [x] Create `services/adequacy/mc_loop_runner.py` with `McRequest`, `start_mc`.
- [x] Move body of `post_mc` minus mesh refuse; inject `_state` → `solver_state`, `_publish_study` → `publish_study`.
- [x] Thin router handler; re-export `McRequest`.
- [x] Commit.

## Task 2: Frontier runner

- [x] Create `services/adequacy/frontier_loop_runner.py` with `FrontierRequest`, `start_frontier`.
- [x] Inject `solver_state`, `state_update`, `publish_study`.
- [x] Thin router handler; re-export `FrontierRequest`.
- [x] Commit.

## Task 3: FMEA sweep runner

- [x] Create `services/adequacy/fmea_sweep_runner.py` with `FmeaSweepRequest`, `start_fmea_sweep`.
- [x] Inject `solver_state`, `state_update`, `publish_study`.
- [x] Thin router handler; re-export `FmeaSweepRequest`.
- [x] Commit.

## Task 4: Tripwire + verify

- [x] `tests/test_mc_study_facade_surface.py` + `tests/test_study_runners_facade_surface.py` — surface names, no-router layering, thin LOC for mc/frontier/sweep.
- [x] Update `.cursor/skills/gui-backend-change/SKILL.md`.
- [x] Run mc / frontier / sweep endpoint suites + tripwires.
- [x] Commit (no push from this follow-on unless asked).
