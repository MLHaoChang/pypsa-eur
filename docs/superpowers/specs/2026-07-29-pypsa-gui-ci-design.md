# pypsa-gui CI — Design

**Date:** 2026-07-29
**Status:** design for implementation
**Related:** `docs/superpowers/specs/2026-07-29-parallel-session-conflict-map.md` (backlog item 1)

## Problem

`pypsa-gui/` has a real test suite that nothing runs.

- 62 files under `pypsa-gui/backend/tests/`, driven by `pytest.ini`
  (`testpaths = tests`, `python_files = test_*.py`) and the `pixi` task
  `gui-tests = { cmd = "python -m pytest", cwd = "pypsa-gui/backend" }`.
- 10 Vitest specs under `pypsa-gui/frontend/src`, wired as `npm test` → `vitest run`.
- `grep -n "gui" .github/workflows/*.yaml` → **no match**. None of the six existing
  workflows touches the subproject.

Meanwhile two sessions are landing large changes into it: the tenancy branch is +16 127
lines across 112 files, and the agent-continuation plan adds a whole `agent_orchestrator/`
package. Both merge with no automated verification at all.

## Goal

A workflow that runs the GUI's existing tests on every PR that touches `pypsa-gui/`, using
the same commands a developer runs locally, and nothing more.

## Non-goals

- **No lint gate.** `ruff format --check pypsa-gui` reports 102 of 108 files would be
  reformatted, and `ruff check pypsa-gui` finds 9 errors, 7 of them in `test_chat_*.py`.
  Adding either gate now would force a repo-wide reformat that conflicts with essentially
  every line both in-flight sessions are writing. Deferred to a post-merge formatting commit.
- **No REUSE gate.** All 211 tracked `pypsa-gui` source files fail `reuse lint-file`; that
  needs a `REUSE.toml` annotation decision first (separate backlog item).
- **No E2E / browser job.** The tenancy branch's `test:auth-gate` and `scripts/shot-authed.mjs`
  need a running backend; out of scope for a first gate.
- **No coverage thresholds.** Nothing to enforce against yet.

## Design

Two independent jobs in one new workflow file, `.github/workflows/gui.yaml`.

### Triggers

```yaml
on:
  push:      { branches: [master], paths: [...] }
  pull_request: { branches: [master], paths: [...] }
  workflow_dispatch:
```

Workflow-level `paths:` rather than `dorny/paths-filter`. `test.yaml` uses the action
because it has expensive setup steps to guard individually; here the whole workflow is
irrelevant when `pypsa-gui/**` is untouched, so the cheaper native filter is correct.
The filter includes `pixi.toml` / `pixi.lock` (the backend's real dependency source) and
the workflow file itself.

`concurrency: cancel-in-progress` keyed on workflow + ref, matching `test.yaml`.

### Job `backend`

The backend cannot be tested from `pypsa-gui/backend/requirements.txt` alone — its first
line says so explicitly ("all other packages (pypsa, pandas, linopy, etc.) are in the pixi
environment"), and `tests/conftest.py` imports `pandas` and `pypsa` at module scope. So the
job must materialise the pixi environment:

- `prefix-dev/setup-pixi@v0.10.0`, `pixi-version: v0.68.1`, `cache: true`,
  `cache-write` only on a `master` push — identical to the `unit-tests` job in `test.yaml`,
  so it shares that cache entry rather than creating a second multi-GB one.
- `pixi run gui-tests` — the same task a developer runs. If the task definition changes,
  CI follows automatically.

The pixi env already carries every runtime import the tests need: `anthropic>=0.117.0`,
`python-magic`, `python-docx`, `pypdf` (pixi.toml:128-134).

No secrets. The chat tests set and delete `ANTHROPIC_API_KEY` themselves via `monkeypatch`
(`test_chat_e2e.py:870`, `test_chat_metrics.py:148`) and there are no `skipif` markers or
live-network calls in any `test_*.py`, so an unset key is the expected CI state.

### Job `frontend`

- `actions/setup-node@v6`, node 22 (matches `pixi.toml`'s `nodejs = ">=22"`),
  `cache: npm` keyed on `pypsa-gui/frontend/package-lock.json`.
- `npm ci` (lockfile is committed), then `npm test` (`vitest run`), then `npm run build`
  (`tsc -b && vite build` — the typecheck is the point; both sessions add large amounts of TS).

Runs independently of `backend` so a Python failure still reports the TS result.

### Hardening

- `timeout-minutes` on both jobs. The suite includes SSE/streaming tests; a hang must fail,
  not burn the runner.
- `permissions: contents: read` — the workflow reads code and reports status, nothing else.
- Each job `cd`s via `defaults.run.working-directory` where it helps readability.

### Licensing

No SPDX header needed: `REUSE.toml` already annotates `.github/**` as
`CC0-1.0` / "The PyPSA-Eur Authors".

## Risks

| Risk | Mitigation |
|---|---|
| Suite is red today → both open branches immediately show a failing check | Run the suite before merging the workflow; if red, report the failures rather than weakening the gate |
| pixi env solve is slow on a cold cache | Shares the existing `setup-pixi` cache with `test.yaml`; path filter keeps it off non-GUI PRs |
| Flaky SSE/timing tests | `timeout-minutes` bounds the damage; quarantine only with evidence, never blanket `continue-on-error` |
