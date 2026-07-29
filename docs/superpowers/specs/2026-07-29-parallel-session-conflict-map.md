# Parallel Session Conflict Map & Conflict-Free Backlog

**Date:** 2026-07-29
**Purpose:** Map what the two other in-flight sessions own, so a third worker can add value
without generating merge conflicts. Every claim below is verified against the source /
branch diffs, with file evidence.

---

## 1. Session inventory

| | Session A — "tenancy" | Session B — "agent continuation" |
|---|---|---|
| Branch | `cursor/multi-user-tenancy-design-e4a8` | `cursor/agent-continuation-plan-a0f0` |
| PR | #2 (draft, open) | none yet |
| Head vs `master` | 43 commits ahead, 112 files, +16 127 / −266 | at `master` (337ba59) — no pushed commits yet |
| Driving doc | `docs/superpowers/plans/2026-07-26-multi-user-org-tenancy.md` + spec | `docs/superpowers/plans/2026-07-25-pypsa-gui-agent-continuation.md` |
| Scope | Auth (cookie session), orgs/ACL, project registry in DB + alembic, advisory edit locks, legacy project import, admin console, static login gate, dark theme redesign | Track A chat hardening (A9a–A9c, A10–A12) then Track B: new `agent_orchestrator/` LangGraph+LiteLLM package, `/api/orchestrate/*` SSE, Analyze mode UI |
| Remaining work | Review-fix loop on PR #2 | Plan §9 items 4–7: everything from A9a onward — i.e. **most of the plan is still unwritten** |

Track A items A1–A8 are already on `master` (`test_chat_live_meta_prompt.py`,
`test_chat_message_trim.py`, `test_chat_tool_result_budget.py`, `test_chat_track_a_e2e.py`
exist), which is why B's branch currently has nothing new on it.

---

## 2. Ownership map

### Owned by A (do not touch)

- `pypsa-gui/backend/`: `main.py`, `routers/projects.py` (+746 lines), `routers/admin.py`,
  `routers/auth.py`, `db/**`, `alembic/**`, `deps.py`, `settings.py`,
  `services/{auth_service,tenancy_service,project_acl,project_locks,project_registry,legacy_migrate,email_service,storage_paths}.py`,
  `models/schemas.py`, `tools/**`
- `pypsa-gui/frontend/`: `index.html`, `spa.html`, `public/login.html`, `vite.config.ts`,
  `vite.auth-gate.ts`, `src/{App,main,routes}.tsx`, `src/index.css`, `src/auth/**`,
  `src/pages/{admin,auth}/**`, `src/pages/{ProjectsHomePage,ScenariosPanel}.tsx`,
  `src/layout/{AppHeader,Sidebar,UserMenu,AssignMembersDialog}.tsx`,
  `src/api/{client,auth,admin,projects,types}.ts`, `src/store/uiStore.ts`,
  `src/utils/{projectActions,lockState,mutationGuard}.ts`, `src/components/{PageKit,LockBanner}.tsx`
- Root: `pixi.toml`, `pixi.lock`, `pypsa-gui/README.md`, `docker-compose*.yml`, `.gitignore`

### Owned by B (do not touch)

- `pypsa-gui/backend/services/{chat_service,chat_tools,chat_tools_schema}.py`,
  `routers/chat.py`, new `agent_orchestrator/**`, new `routers/orchestrate.py`,
  `litellm.config.yaml`, `backend/tests/test_chat_*.py`, `backend/smoke/**`
- `pypsa-gui/frontend/src/components/{ChatPanel,ChatMarkdown}.tsx`,
  `src/store/chatStore.ts`, `src/api/chat.ts`, `src/utils/chatUi.ts`
- `pypsa-gui/CHATBOT.md` and the `CHATBOT_*.md` backlogs

### Contested — both sessions will edit these

| File | A's change | B's planned change | Risk |
|---|---|---|---|
| `backend/main.py` | new `logging`/`JSONResponse` imports, `admin`+`auth` in the `from routers import (...)` block, CORS origins, auth middleware in `undo_snapshot_middleware` | register `orchestrate` router in the same import block + `include_router` | **High** — same import tuple, textually adjacent |
| `backend/requirements.txt` | +7 lines appended (sqlalchemy, alembic, psycopg, pydantic-settings, pwdlib, emails, httpx) | +3 appended (langgraph, litellm, langgraph-checkpoint-sqlite) — plan D16 | **High** — both append at EOF |
| `pixi.toml` | +2 tasks, +5 deps, new `[pypi-dependencies]` | likely orchestrator deps | Medium |
| `backend/services/chat_tools_schema.py` | 8 lines: `id` field documented in `list_projects` / `list_scenarios` descriptions | new composite tool schemas (A9a–A9c), orchestrator whitelist | Medium — different regions, but same file |
| `backend/routers/projects.py` | rewritten for ACL/registry | plan **D13** requires hooking `orchestrate.jsonl` into the Save-As / rename / bundle lineage helpers that live here | **High** |
| `frontend/package.json` | `test:auth-gate` script, `ws` devDep | possibly vitest deps for A12 | Low |

**Merge order recommendation: A first, then B.** A's diff is 112 files and already
review-complete; B has not committed anything yet, so B pays a much smaller rebase cost.
Every contested file above is one where A's version is the larger rewrite.

---

## 3. Semantic seams (no file conflict, but broken-on-merge)

These are the ones that will not show up as a git conflict and will therefore be missed.

1. **B's plan D7 assumes there is no app-level auth.** It states "no app-level auth today
   (local/trusted single-user GUI)" and instructs documenting that in `CHATBOT.md`. Once A
   merges, `main.py`'s middleware auth-gates every `/api/*` path not in `_AUTH_PUBLIC_PATHS`
   — which will include `/api/orchestrate/stream`. B's trust-model docs and any test that
   asserts unauthenticated access will be wrong. *Fix: B should write D7 as "inherits the
   host auth mode" and add an auth-on test.*
2. **Raw `fetch()` call sites bypass the axios auth interceptor.** A adds
   `withCredentials: true` plus a 401→login redirect to `api/client.ts`, but four call sites
   never go through axios: `api/chat.ts:63` (chat SSE, B's file), `api/uploads.ts:75,87,101,129`,
   and `pages/TopologyCanvas.tsx:147,2361` (layout autosave / delete with `keepalive`).
   Cookies still ride along (same-origin `fetch` defaults to `credentials: 'same-origin'`),
   so this is not an auth *transport* break — but on session expiry these paths get an
   unhandled 401 instead of the redirect, i.e. a silently failing autosave. **`uploads.ts` and
   `TopologyCanvas.tsx` are owned by neither session** (see backlog item 3).
3. **Orchestrator artefact paths vs org storage.** B writes `orchestrate.jsonl` and
   `orchestrate.checkpoints.sqlite` under `projects/<name>/`. A moves projects into
   org-scoped storage via `services/storage_paths.py`. B must resolve paths through the same
   helper `chat.jsonl` uses, not by string-joining `projects_root`.
4. **Edit locks vs chat/orchestrate writes.** A's advisory project locks gate mutations in
   the UI (`mutationGuard.ts`, `LockBanner`). Chat write-tier tools and any future orchestrator
   apply-path do not consult the lock, so an agent can mutate a project another user holds.
   Nobody owns this today — worth an explicit decision.
5. **`list_projects` tool now returns `id`.** A already patched the schema description; B's
   composite tools and orchestrator whitelist should treat `id` as nullable (null in
   single-user mode).

---

## 4. Conflict-free backlog (ranked)

Each item was checked against both branches' `--name-only` diffs and against B's plan file
map (§3 of the continuation plan). "Untouched" means the file appears in neither.

### 1. CI that actually runs the GUI — new file, supports both

*Evidence:* `grep -n "gui" .github/workflows/*.yaml` returns nothing. `pixi run gui-tests`
exists in `pixi.toml` and 62 backend test files exist, plus 10 frontend vitest specs — and
**no workflow runs any of them.** Both sessions are landing thousands of lines with zero
automated verification.
*Work:* new `.github/workflows/gui.yaml` — backend `pytest`, frontend `npm ci && npm test &&
npm run build`, path-filtered to `pypsa-gui/**`.
*Conflict surface:* one new file. Zero overlap.
*Caveat:* will turn both PRs red if anything is broken — that is the point, but it should be
introduced deliberately, not as a surprise.

### 2. REUSE/SPDX coverage for `pypsa-gui/**` — one file, unblocks both

*Evidence:* `reuse lint-file pypsa-gui/backend/main.py` → "no license identifier / no
copyright notice". **All 211 tracked `.py`/`.ts`/`.tsx` files under `pypsa-gui/` lack SPDX
headers**, `REUSE.toml` has no annotation covering `pypsa-gui`, and `.pre-commit-config.yaml`
runs `fsfe/reuse-tool: reuse-lint-file` with no exclusion for it. Every new file either
session adds fails that hook.
*Work:* one `[[annotations]]` block in `REUSE.toml` for `pypsa-gui/**`.
*Conflict surface:* `REUSE.toml` — touched by neither session.
*Open decision:* copyright holder + license for the subproject is the maintainer's call.

### 3. Auth-safe raw `fetch()` call sites — untouched files, prevents a merge-time bug

*Work:* route `api/uploads.ts` (4 sites) and `pages/TopologyCanvas.tsx` (2 sites) through a
shared 401-aware fetch wrapper, or give them explicit `credentials: 'include'` + 401 handling.
*Conflict surface:* `uploads.ts` and `TopologyCanvas.tsx` — in neither branch's diff, and not
in B's file map. Do **not** touch `api/chat.ts` (B) or `api/client.ts` (A); the wrapper should
be a new module both can adopt later.

### 4. Tests for the results KPI math — the highest-churn untested code in the repo

*Evidence:* the last ~10 commits on `master` are almost all KPI corrections
(`0cae702 align cross-tab CAPEX, storage revenue, and curtailment KPIs`,
`a4b2e71 avg capture price and net profit margin`, `63ec716 remove FoM`,
`7051b49 clarify OPEX vs storage charge`). The math lives in
`frontend/src/pages/results/shared.tsx` — 43 exported functions including `weightedSum`,
`weightedSumSplit`, `perPeriodWeightedSum`, `perPeriodPerColumnWeightedSum`, `averageScaling`,
`aggregateTS`, `durationCurvePoints`, `aggregateSeasonalRows` — with **zero tests**, while
13 033 lines of results tabs consume them. Vitest is already wired (`npm test`, 10 specs under
`src/utils`).
*Conflict surface:* `pages/results/**` is untouched by both sessions. New `*.test.ts` files only.
*Payoff:* period weighting is exactly where the cross-tab KPI mismatches keep coming from.

### 5. Backend modules with no test references at all

*Evidence:* cross-referencing every `services/*.py` and `routers/*.py` against `tests/*.py`:
`period_utils` (147 LOC, **recently rewritten** by `cursor/e7189544` "consolidate period
weighting"), `time_aggregation_service` (348), `serialization` (148), `ac_pf_service` (491),
`carrier_catalog` (56), `project_network` (73) — all zero references.
*Conflict surface:* all untouched by both sessions; tests are new files.
*Priority within the item:* `period_utils` first — it is both new and the backend twin of the
frontend weighting bugs in item 4.

### 6. Independent review of PR #2

Read-only, zero file conflict, and the highest-leverage thing available while A is still in
its review-fix loop: 16 127 added lines including an auth surface, a middleware that fails
open/closed on DB errors, and a static login gate. Worth a targeted pass on the auth
middleware, the lock-acquire race, and the legacy-import claim path.

### 7. Ruff errors in untouched files

`ruff check pypsa-gui` → 9 errors. Two are outside both sessions' territory:
`services/filename_service.py:1` (D301) and `smoke/run_chat_smoke.py:104` (D301). The other
seven are all in `tests/test_chat_*.py` — B's territory; leave them.

---

## 5. Do NOT do (things that look helpful and are conflict bombs)

- **`ruff format pypsa-gui`.** `ruff format --check` reports **102 of 108 files would be
  reformatted**, including `chat_service.py`, `chat_tools.py`, `projects.py`, and `main.py`.
  A repo-wide format normalisation right now would conflict with essentially every line both
  sessions are writing. Defer until both branches merge, then do it as a single
  formatting-only commit.
- **Touching `main.py` for anything.** It is the one file both sessions must edit; a third
  editor triples the conflict.
- **Appending to `requirements.txt` / `pixi.toml`.** Both sessions append at EOF already.
- **Editing `CHATBOT.md` or the `CHATBOT_*.md` backlogs.** B's plan updates them in its final
  acceptance pass.
- **Refactoring `chat_tools.py` / `chat_service.py`**, even "just" to add a read-only tool —
  that is the orchestrator's host bridge surface.
- **Renaming or moving anything under `frontend/src/api/` or `frontend/src/store/`** — A owns
  `client/auth/admin/projects/types` + `uiStore`, B owns `chat` + `chatStore`.
