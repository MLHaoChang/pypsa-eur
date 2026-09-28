# Planning-Loop Router Lift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Cut `post_coupling_loop` (516) and `post_margin_loop` (898) out of `routers/results.py` into `services/adequacy/` runners, restoring the thin-results-router invariant without changing behaviour.

**Architecture:** Handlers keep the mesh refusal and the FastAPI decorator; runners own validation, bindings, record, and worker. State is injected (`solver_state`, `state_update`, `publish_study`) so runners never import `routers.*`. Constants and request models move with the runners and are re-exported from the router for existing importers.

**Tech Stack:** Python 3.12 / FastAPI / PyPSA 1.1.2 / pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-planning-loop-router-lift-design.md`

## Global Constraints

- Branch: `cursor/planning-loop-router-lift-ce8d`
- Strictly behaviour-preserving. Defects go to `docs/superpowers/findings/`.
- Never edit an existing test. New tests are additive only.
- Runners never import `routers.*`.
- `services/adequacy/coupling.py` (the pure controller) is not touched.
- Gate: failing set of `test_adequacy_coupling_endpoint.py` + `test_adequacy_margin_loop.py` unchanged (baseline: 93 passed).
- Path-limited commits.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `services/adequacy/coupling_loop_runner.py` | create | coupling-loop constants, request model, `start_coupling_loop` |
| `services/adequacy/margin_loop_runner.py` | create | margin-loop constants, request model, `start_margin_loop` |
| `routers/results.py` | modify | thin handlers + re-exports |
| `tests/test_planning_loop_facade_surface.py` | create | tripwire |

---

## Task 1: Coupling-loop runner

- [x] Move constants + `CouplingLoopRequest` + body of `post_coupling_loop` (minus mesh refuse) into `start_coupling_loop`.
- [x] Inject `solver_state` / `state_update` / `publish_study`.
- [x] Thin router handler; re-export constants/models.
- [x] Ruff F821 clean; coupling endpoint suite green.
- [x] Commit.

## Task 2: Margin-loop runner

- [x] Same shape for `post_margin_loop` → `start_margin_loop`.
- [x] Margin endpoint suite green; both suites' failing set unchanged.
- [x] Commit.

## Task 3: Tripwire + verify

- [x] `tests/test_planning_loop_facade_surface.py`: handlers stay importable; runners never import routers; handler bodies stay short.
- [x] Update `.cursor/skills/gui-backend-change/SKILL.md` to name the runners.
- [x] Commit, push, open PR.
