# gridspine Increment 4 Implementation Plan — Action Layer, GUI Wiring, Chat Tools

> **Status 2026-09-06: PLAN, nothing landed. Written from the spec's phase-3 remainder ("action layer; GUI wiring; chat tools"), the increment-3 handoff (§4 API, rulings 14–32) and a read of the pypsa-gui backend as it stands on `master`. Two things gate the start: the owner's answers in §Decisions below, and PR #6 (backend god-file decomposition), which is rewriting the two files this increment must touch.**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A planning→dynamics study can be created, run, watched, inspected and exported from the pypsa-gui backend and from the copilot, through ONE set of backend functions. `gridspine` stays headless: `drivers/` is the only surface the backend calls, and it grows exactly what a job runner needs — a config object, per-stage progress, an abort signal.

**Architecture (spec §"One tool" and §"Copilot parity"):** `gridspine/drivers/` gains a job-shaped entry (`run_study(config, progress, stop_event) -> StudyResult`) over the existing `dispatch_year` / `study_dispatch` / `resume_from_dispatch` seam (follow-ups F3). The backend gains `services/gridspine_service.py` — the action layer: plain functions, no HTTP, no FastAPI `Depends` — a router that wraps them, and chat tools that wrap the SAME functions. Runs go through the existing solve queue. The frontend is wired last, thinly.

**Tech stack:** as increment 3 on the gridspine side. Backend: the existing FastAPI app under `pypsa-gui/backend` (routers, services, `db/models.py`, alembic), its solve queue (`services/solve_queue.py`), its copilot registry (`services/chat_tools_schema.py` `TOOLS` + `services/chat_tools.py` `DISPATCHERS` / `TOOL_ROUTES`). Frontend: `pypsa-gui/frontend` (React/TS).

**Spec:** `docs/superpowers/specs/2026-08-27-gridspine-design.md` — "One tool: UI integration", "Copilot parity", "Validation, testing, error handling", phase 3.
**Prior art binding here:** the increment-3 handoff rulings; `pypsa-gui/CONTEXT.md` (vocabulary: **Project**, never "study/workspace/model/case"); `pypsa-gui/docs/adr/0002-chat-changes-need-a-live-api-probe.md`; `pypsa-gui/CHATBOT_TOOLS_AUDIT.md` (the `_h` import-alias convention, enforced by `tests/test_chat_tools_imports.py`).

## What the backend actually is (read 2026-09-06, `master`)

The spec's sketch and the code differ in three places that shape every task below.

1. **There is no action layer today.** Chat tools call the FastAPI route handlers *directly as Python functions* (`services/chat_tools.py::_route(handler, ...)` resolves `Depends` by signature; every wrapper does a function-local `from routers.x import handler as _h`). A minority call a service helper and are tagged `_service_call_` in `TOOL_ROUTES`. So "parity is structural" is already the house style, but with the route as the shared callee, not a service function. This increment introduces the service-function pattern for gridspine only and leaves the rest alone.
2. **The queue runs one kind of job.** `services/solve_queue.py::SolveQueue._run_job` builds a `ProjectContext` and calls `services/solver_service.py::run_simulation(config, network, lock, stop_event, log_queue, state_update)`. `SolveJob` has `stop_event`, `log_queue`, `status ∈ queued|running|completed|failed|aborted|interrupted`, persistence via `services/solve_job_store.py` and `db/models.py::SolveJobRow`, HTTP at `/api/simulation/queue`, SSE log stream. A gridspine run needs the queue to accept a second job kind (Task 4). **PR #6 decomposes `solver_service.py`/`solve_queue.py`; do not touch them until it merges.**
3. **"Study" does not exist; "Project" does.** `db/models.py::Project` with `scenario_type ∈ baseline|scenario|stress|NULL`, a per-project directory (`services/storage_paths.py`, `project_registry.project_dir`) holding `network.nc`, `solver_config.json`, `results_state.pkl`, `metadata.json`, `chat.jsonl`, `uploads/`, snapshots. The spec's "new study → pick type" becomes "new Project of kind planning→dynamics", with gridspine's file artifacts in a `gridspine/` subdirectory of the project directory — a kind, not a new table (§Decisions, D1).

Also: `gridspine` is referenced nowhere in `pypsa-gui` (clean seam), the copilot sends `list(TOOLS)` in full every turn (no per-context subsetting — the spec's "toolset matching the open study" needs a gate, Task 6), safety tiers are a `Safety: <tier>` marker parsed from the description text, and `len(TOOLS) == len(DISPATCHERS)` plus the `_h` AST scan are enforced by tests.

## Global constraints

- gridspine side: ALL commands via `pixi run …`; gate `pixi run gridspine-tests` (502 passed, 2 skipped at `335ca911`); TDD with RED/GREEN evidence; a mutation per task; path-limited commits; the engine cage (`pypsa` only under `producers/`; `pandapower`/`lightsim2grid` only under `ingest/`, `static/`, `handoff/`). **`drivers/` must not import anything from `pypsa-gui`**, and `pypsa-gui` imports only `gridspine.drivers` and `gridspine.schema` (never `static/`, `producers/`, `handoff/` internals) — a cage test on both sides.
- backend side: `pixi run gui-tests` (the `test` feature environment; `tests/test_desktop_environment.py` asserts the task placement) green before every commit; chat-tool changes need the live API probe (ADR 0002) recorded in the task report; every new tool has a `Safety:` tier, a `TOOL_ROUTES` row and a `DISPATCHERS` entry; the `_h` alias convention.
- **Sequencing against PR #6:** Tasks 1–3 touch only new files and gridspine; Task 4 (queue) and Task 6 (chat tools) touch files #6 rewrites. Land 1–3 on a branch off `master` now; rebase for 4–7 after #6 merges. Do not open a second decomposition.
- No frontend change before Task 5's backend contract is committed and its tests green; the frontend task is one path-limited commit under `pypsa-gui/frontend` only.
- Runtime discipline: no backend test runs a unit commitment longer than 48 h or AC screening on more than one hour; the driver tests already own the long fixtures.

## Decisions the owner must make before Task 4 (ask; do not resolve inside a task)

- **D1 — Project kind.** Add `project_kind ∈ {capacity_expansion (default), planning_dynamics, connection}` as a column beside `scenario_type` (alembic migration, `Project` model, `routers/projects.py` validation), or store the kind only in `metadata.json`? Recommendation: a column — the router and the copilot need to filter on it, and `scenario_type` set the precedent. Vocabulary stays "Project"; the UI label may say "Planning → dynamics study" (CONTEXT.md governs identifiers, not labels).
- **D2 — Queue generalisation.** Give `SolveJob` a `kind` and a `runner` (a callable taking `stop_event`, `log_queue`, `progress`) so `_run_job` dispatches on kind, or add a second, smaller queue class for gridspine? Recommendation: one queue, one dispatcher thread, `kind` field — the spec's "no second job system" is explicit, and abort/status/SSE come for free. This is the change that must wait for PR #6.
- **D3 — Where the dispatch comes from in a planning project.** The spec's variant 1 generates the dispatch with PyPSA nodal UC (what `dispatch_year` does on case39). In the app, a project already holds a PyPSA network (`network.nc`). Increment 4 keeps the case39 driver path (network from `load_case39_res`) and exposes `from_dispatch` (F3) as the second source; wiring a project's own `network.nc` through `producers/pypsa_nodal.to_pypsa` is its own increment (ingest of a real grid is spec phase 4).
- **D4 — Template edits.** `edit_template_param` writes a project-local overlay (`gridspine/templates_overlay.yaml`) with per-field `{value, source, edited_by: user|chat, at}`; the base YAML in `gridspine/templates/data` is never written by the app. Confirm.

---

## Task 1 — `drivers/study.py`: a job-shaped entry with progress and abort

**Files:** `gridspine/drivers/study.py` (new), `gridspine/drivers/year_study.py` (progress/stop hooks only), `tests/gridspine/test_study_job.py` (new).

- [ ] `StudyConfig` (frozen dataclass; validated): `outdir`, `hours=8760`, `k=5`, `window=168`, `overlap=24`, `screen=True`, `n2_prune_threshold_pct=0.0`, `from_dispatch: Path|None`. `to_json`/`from_json` (the backend stores it in the project directory).
- [ ] `run_study(config, progress=None, stop_event=None) -> StudyResult`. `progress(stage: str, done: int, total: int)` is called at every stage boundary and inside the two loops that take time: per rolling window (`run_uc_rolling` gains an optional callback — `producers/` change, cage-safe) and per selected hour in `study_dispatch`. `stop_event.is_set()` is polled at the same points; an abort raises `StudyAborted` after writing a `StageError(stage=..., cause="aborted")` artifact, so the UI renders it like any failure and `resume_from_dispatch` can pick up a finished dispatch.
- [ ] Manifest gains `"config": config.to_json()` and `"status": completed|aborted`.
- [ ] Tests (48 h / 24 h windows, k=1, the F3 fixture size): the progress sequence is exactly `ingest, dispatch×windows, ranking, loadflow×selected, screening×selected, handoff×selected` with monotone `done`; a stop set before the second window aborts with the artifact written and `dispatch.csv` absent; a stop set after the dispatch aborts in `loadflow` and the run resumes with `from_dispatch` to the same selection as an unaborted run; `StudyConfig.from_json(to_json(c)) == c`; an invalid config is a `ContractError` before anything is written.
- [ ] Mutation: drop the stop poll inside the window loop → the first abort test red.
- [ ] Gate, path-limited commit.

## Task 2 — Stage status from artifacts, and the ledger as data

**Files:** `gridspine/drivers/status.py` (new), `gridspine/handoff/bundle.py` (a `ledger.json` beside `ledger.md`), `tests/gridspine/test_study_status.py` (new).

- [ ] `stage_status(outdir) -> dict`: for each of `STAGES`, `pending|running|done|failed|aborted` derived ONLY from files — `loads.csv`, `dispatch.csv`, `metrics.csv`, `selected.csv`, `lf_*_bus.csv`, `bundle_h*/`, `manifest.json`, the `StageError` artifact — plus `selected_hours`, `converged_hours`, `bundles`. No process state: the queue's status and this must agree by construction, and a restarted backend reads the same answer.
- [ ] `ranked_snapshots(outdir) -> DataFrame`: `selected.csv` joined to `metrics.csv` (every ranked column, converged flag, reasons as a list) — the table `list_ranked_snapshots` returns.
- [ ] `ledger.json` written by `export_bundle` with the same entries as `ledger.md` plus `provenance_counts` — the copilot's `get_assumption_ledger` returns data, not a Markdown blob.
- [ ] Tests on the F3 fixture's output directory: status after a complete run; after deleting `manifest.json` (handoff `running`); with a `StageError` present (`failed` at that stage, later stages `pending`); `ranked_snapshots` columns and reasons equal `selected.csv`/`metrics.csv`; `ledger.json` entries == `ledger.md` entries.
- [ ] Mutation: `stage_status` ignores the `StageError` artifact → the failed-stage test red.
- [ ] Gate, path-limited commit.

## Task 3 — The action layer: `pypsa-gui/backend/services/gridspine_service.py`

**Files:** `pypsa-gui/backend/services/gridspine_service.py` (new), `pypsa-gui/backend/tests/test_gridspine_service.py` (new). Depends on D1, D3, D4 for shapes; buildable before PR #6 (no queue yet: `run_pipeline` runs synchronously in this task, queued in Task 4).

- [ ] Functions, all plain Python, all taking `(db, project, ...)` and returning JSON-able dicts — the same functions the router AND the chat tools will call:
  `create_study(db, user, name, kind, config) -> project` (a Project of kind `planning_dynamics`, directory created, `gridspine/config.json` written);
  `set_dispatch_source(db, project, source: "generate"|{"from_dispatch": path})`;
  `run_pipeline(db, project) -> job` (Task 3: synchronous `run_study`; Task 4: enqueued);
  `get_stage_status(project)` (Task 2);
  `list_ranked_snapshots(project)`;
  `get_assumption_ledger(project)` (`ledger.json` of the latest bundle, or the manifest's ledger before any bundle);
  `edit_template_param(db, project, unit_id, field, value, source, edited_by)` (overlay, D4);
  `export_handoff_bundle(project, hour) -> Path` (zip of `bundle_h<hour>/`);
  `fetch_result_figure` — **not in this increment** (read-back is spec phase 4); the function exists and returns a typed "not available" so the tool surface is complete.
- [ ] Every function validates the project's kind and refuses the wrong kind with the same error type the routers use.
- [ ] Cage test: `gridspine_service.py` imports only `gridspine.drivers.*` and `gridspine.schema.*`; and `gridspine/` imports nothing from `pypsa-gui` (`tests/gridspine/test_contracts.py` gains the reverse check).
- [ ] Tests (backend, `pixi run gui-tests`): create → status pending; run (48 h, k=1, screening off — the only backend test that solves) → status done, ranked snapshots non-empty, export writes a zip whose members are `BUNDLE_FILES`; edit_template_param writes the overlay with `edited_by`, re-read through `load_unit_templates(overlay=...)`, the ledger's provenance counts change; wrong project kind refused; a `StageError` from a broken config surfaces as the status's `failed` stage.
- [ ] Mutation: `edit_template_param` drops `edited_by` → the overlay/ledger test red.
- [ ] `pixi run gui-tests`, path-limited commit.

## Task 4 — Runs go through the existing solve queue (after PR #6)

**Files:** `pypsa-gui/backend/services/solve_queue.py` (post-#6 layout), `services/solve_job_store.py` + `db/models.py::SolveJobRow` + an alembic migration (a `kind` column), `services/gridspine_service.py::run_pipeline`, `tests/test_solve_queue*.py`, `tests/test_gridspine_service.py`.

- [ ] D2: `SolveJob.kind ∈ {solve (default), gridspine}` and a `runner` resolved from the kind in `_run_job`; the gridspine runner calls `run_study(config, progress, stop_event)` with `progress` writing to the job's `log_queue` (one line per stage boundary, machine-parsable prefix) — the existing SSE stream shows stage progress with no new endpoint. `enqueue_unique` semantics unchanged (one active job per project).
- [ ] Abort → `stop_event` → `StudyAborted` → job `aborted`; the `StageError` artifact and `stage_status` agree.
- [ ] Boot reconciliation (`reconcile_on_boot`) marks an interrupted gridspine job `interrupted`; `stage_status` then shows where it stopped, and `run_pipeline` on such a project offers `from_dispatch` when `dispatch.csv` exists.
- [ ] Tests: enqueue a gridspine job on a 48 h config, watch status through `queued → running → completed`, abort one mid-dispatch, and the existing queue tests unchanged.
- [ ] Mutation: the runner ignores `stop_event` → the abort test red.
- [ ] `pixi run gui-tests`, path-limited commit.

## Task 5 — Router: `/api/gridspine`

**Files:** `pypsa-gui/backend/routers/gridspine.py` (new), app registration, `tests/test_gridspine_router.py` (new).

- [ ] One endpoint per action-layer function, each a thin wrapper (auth/`Depends` resolution, then the service call); `POST /api/gridspine/projects` (create), `POST …/{project}/dispatch-source`, `POST …/{project}/run` (returns the job), `GET …/{project}/status`, `GET …/{project}/snapshots`, `GET …/{project}/ledger`, `PUT …/{project}/templates/{unit_id}/{field}`, `GET …/{project}/bundles/{hour}` (zip download).
- [ ] Authorization follows the project membership rules the other routers use (`_may_see`-style helpers, not new ones).
- [ ] Tests: every endpoint calls its service function (patch the service, assert the call) and the download streams the zip; unauthorised project → 403/404 as elsewhere.
- [ ] `pixi run gui-tests`, path-limited commit.

## Task 6 — Chat tools (after PR #6; ADR 0002 live probe)

**Files:** `services/chat_tools_schema.py` (`TOOLS` entries via `_t`), `services/chat_tools.py` (`DISPATCHERS`, `TOOL_ROUTES`, wrappers with the `_h` alias), `tests/test_chat_tools_endpoint_map.py`, `tests/test_chat_tools_imports.py`, `CHATBOT_TOOLS_AUDIT.md`.

- [ ] Tools, one per action-layer function, calling the SERVICE function (tagged `_service_call_` in `TOOL_ROUTES`, the sanctioned pattern for non-route callees) — never the router: `gridspine_create_study` (`Safety: write`), `gridspine_set_dispatch_source` (`write`), `gridspine_run_pipeline` (`execution_long_running`), `gridspine_get_stage_status` (`read`), `gridspine_list_ranked_snapshots` (`read`), `gridspine_get_assumption_ledger` (`read`), `gridspine_edit_template_param` (`write`, `edited_by="chat"` hard-coded — the ledger provenance the spec asks for), `gridspine_export_handoff_bundle` (`read`; returns the path/size, the download stays a UI action).
- [ ] The spec's "toolset matching the open study": a gate in `chat_service` that drops `gridspine_*` tools when the bound project's kind is not `planning_dynamics`, and drops the solve tools when it is. Smallest possible change, with a test on the sent tool list per project kind.
- [ ] Invariants kept: `len(TOOLS) == len(DISPATCHERS)`, every tool in `TOOL_ROUTES`, `_h` alias AST scan green, lock-gate derivation unchanged for existing tools (gridspine writes are project-scoped and go through the same `_lock_gated` derivation).
- [ ] Live API probe per ADR 0002: create, run (48 h), status, snapshots, ledger, edit — transcript in the task report.
- [ ] Mutation: drop one tool from `DISPATCHERS` → the registry-invariant test red.
- [ ] `pixi run gui-tests`, path-limited commit.

## Task 7 — Frontend: new project kind, form, stage progress, downloads (thin, last)

**Files:** `pypsa-gui/frontend/src/...` (one feature directory), one path-limited commit.

- [ ] New-project flow gains the kind picker ([1] capacity expansion unchanged, [2] planning → dynamics; [3] connection is greyed with "later"); the [2] form is `StudyConfig` (hours, k, window, overlap, screen, N-2 threshold, dispatch source dropdown with "generate with PyPSA" and "from an existing run").
- [ ] Run button → `POST …/run`; the existing queue panel shows the job; stage progress rendered from `GET …/status` polled while the job is active (the SSE log already carries the stage lines).
- [ ] Results view: ranked snapshots table (reasons, converged, every ranked column), the ledger with provenance counts, bundle download per selected hour. Read-back upload: placeholder only (phase 4).
- [ ] Frontend tests as the repo does them (component tests for the form and the table; no e2e that solves).
- [ ] Commit.

## Reporting per task (unchanged from increment 3)

RED output, GREEN output, the mutation and which tests it turned red, the gate line(s) (`gridspine-tests` and/or `gui-tests`), the commit hash, open decisions raised (not resolved). For Task 6 also the live-probe transcript.

## Out of scope, named so nobody drifts into it

PowerFactory API exporter and read-back (phase 4); a project's own `network.nc` as the dispatch source (D3; own increment); connection-study variant 2 (`drivers/connection.py`); the compliance rule engine; any change to how the existing capacity-expansion flow solves. And the four owner questions in the handoff §6 plus F5 (inverter reactive control) — they change numbers, not this increment's plumbing.
