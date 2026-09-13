# MC Study Router Lift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Cut `post_mc` (274 LOC) out of `routers/results.py` into `services/adequacy/mc_loop_runner.py` without changing behaviour.

**Architecture:** Handler keeps mesh refuse + FastAPI decorator; runner owns validation, locked snapshot, record, and worker. State injected (`solver_state`, `publish_study`).

**Tech Stack:** Python 3.12 / FastAPI / PyPSA / pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-mc-study-router-lift-design.md`

## Global Constraints

- Branch: `cursor/mc-study-router-lift-ce8d`
- Strictly behaviour-preserving.
- Never edit an existing test for the refactor (except if a source-scan goes vacuous — then retarget it, as F1n2).
- Runner never imports `routers.*`.
- Gate: `tests/test_adequacy_mc_endpoint.py` + new tripwire; failing set unchanged.

## Task 1: Runner

- [ ] Create `services/adequacy/mc_loop_runner.py` with `McRequest`, `start_mc`.
- [ ] Move body of `post_mc` minus mesh refuse; inject `_state` → `solver_state`, `_publish_study` → `publish_study`.
- [ ] Thin router handler; re-export `McRequest`.
- [ ] Commit.

## Task 2: Tripwire + verify

- [ ] `tests/test_mc_study_facade_surface.py` — surface names, no-router layering, thin LOC.
- [ ] Update `.cursor/skills/gui-backend-change/SKILL.md`.
- [ ] Run mc endpoint suite + tripwire.
- [ ] Commit, push, open PR.
