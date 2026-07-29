# pypsa-gui CI — Implementation Plan

**Goal:** Add `.github/workflows/gui.yaml` so `pypsa-gui/`'s existing backend pytest suite
and frontend Vitest + typecheck run on every PR that touches the subproject.

**Upstream spec:** `docs/superpowers/specs/2026-07-29-pypsa-gui-ci-design.md`

## Global constraints

- **Conflict discipline.** Touch only files owned by neither in-flight session:
  `.github/workflows/gui.yaml` (new) and `docs/superpowers/{plans,specs}/2026-07-29-*`.
  Explicitly forbidden: `pypsa-gui/backend/main.py`, `requirements.txt`, `pixi.toml`,
  `pixi.lock`, `frontend/package.json`, `chat_*.py`, `routers/projects.py`, `CHATBOT.md`.
  See `docs/superpowers/specs/2026-07-29-parallel-session-conflict-map.md` §2.
- **No test edits.** If the suite is red, report it; do not modify tests to make CI green.
- **No lint/format gate** (spec §Non-goals) — a repo-wide reformat is the single largest
  conflict generator available right now.
- One commit per task.

## 0. Review status

| Field | Value |
|---|---|
| Status | `CLEARED` |
| Basis | Design reviewed against `test.yaml` conventions, `pytest.ini`, `conftest.py` imports, `pixi.toml` deps, `package.json` scripts, and `REUSE.toml` `.github/**` annotation |
| Blocking gaps | None |

## 1. Tasks

### Task 1: Verify the frontend suite actually passes

- [x] `npm ci` — clean install
- [x] `npm test` → **10 files, 61 tests, all passed** (1.22 s)
- [x] `npm run build` → **FAILS**, and not because of a code regression:
      `tsc -b` succeeds, then `vite build` dies with
      `Could not resolve entry module "index.html"`. Root cause:
      `.gitignore:80` `*.html` swallows `pypsa-gui/frontend/index.html`, so the Vite entry
      point is untracked and absent from any clean checkout
      (`git check-ignore -v` confirms). The frontend cannot be built or `npm run dev`'d from
      a fresh clone of `master` today.
- [x] `npx tsc -b --force` → exit 0, no diagnostics. **Decision:** the frontend job
      typechecks with `tsc -b --force` instead of `npm run build`, with a comment pointing at
      the un-ignore follow-up. The tenancy branch already fixes the root cause
      (`!pypsa-gui/frontend/index.html` + two siblings) and commits the file, so the step can
      become `npm run build` after that merges. Fixing it here would collide with session A
      on both `.gitignore:80` and `index.html` itself.

### Task 2: Verify the backend suite as far as the container allows

- [x] First attempt with a pip-built env (`pip install pypsa fastapi …`): 1033 passed,
      **10 failed** — investigated rather than assumed:
      7 × `test_chat_multimodal` PDF tests died in `cryptography` (`ModuleNotFoundError:
      _cffi_backend` → pyo3 panic) = broken container package;
      2 × `test_security_import` failed because pandas 3.0.5 pickles `pandas.DataFrame` while
      `routers/projects.py:248` allowlists `pandas.core.frame.DataFrame`;
      1 × `test_myopic_feasibility` on a mismatched pypsa/highs.
- [x] Installed pixi 0.68.1 and materialised the real environment
      (pandas 2.3.3, pypsa 1.1.2, numpy 2.4.4).
- [x] **`pixi run gui-tests` → 1043 passed, 1 skipped, exit 0** (4 min 8 s). Every pip-env
      failure was an environment artifact. The gate is green on `master` today.

### Task 3: Write `.github/workflows/gui.yaml`

- [x] Two jobs (`backend`, `frontend`) per spec §Design
- [x] Path-filtered triggers incl. `pixi.toml`, `pixi.lock`, and the workflow file
- [x] `concurrency` + `cancel-in-progress`, `permissions: contents: read`, `timeout-minutes`
- [x] `yaml.safe_load` parses; `actionlint 1.7.7` clean (exit 0);
      `actions/setup-node@v7` confirmed as the current major
- [x] Commit `ci: run pypsa-gui backend and frontend tests`

### Task 4: Report

- [x] Push the branch
- [x] Report what was verified and what was not

## 3. Findings for the other sessions (not fixed here — contested files)

1. **Frontend build is broken on `master`** (Task 1). Session A's `.gitignore` negation is
   the fix; it should land with the branch rather than being duplicated.
2. **pandas 3 will silently drop saved results.** `_SafeResultsUnpickler` allowlists
   `("pandas.core.frame", "DataFrame")` / `("pandas.core.series", "Series")`; under pandas 3
   those pickle as top-level `pandas.DataFrame` / `pandas.Series`, so `find_class` refuses
   them and every call site treats the failure as "no cached results" — results vanish with
   only a warning. `pixi.lock` already carries a `pandas-3.0.3` entry for some solves. Owner:
   session A (`routers/projects.py`). Fix is two allowlist tuples plus a regression test.

## 2. Acceptance

- Workflow exists, parses, and references only commands that exist today
  (`pixi run gui-tests`, `npm ci`, `npm test`, `npm run build`).
- No file outside the allowed set is modified — verified with `git diff --name-only` against
  both session branches' file lists.
- Local verification results are reported honestly, including anything unverifiable here.
