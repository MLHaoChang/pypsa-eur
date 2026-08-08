# Solve queue full pass — design

The multi-project solve queue lets a user queue several saved projects, walk away,
and keep working on a different project while they solve. Both halves of that
promise work today. Both are also undermined by defects that destroy user work
silently, and the queue lacks the durability and controls the "walk away" model
implies.

This spec covers three increments: stop the data loss, then make the queue
correct and observable, then make it durable and controllable.

Design decisions D1–D19 behind this spec were settled with the project owner and
are recorded verbatim in the pipeline workspace at
`.superpowers/pipeline/solve-queue-full-pass/design-decisions.md`. The repository
survey this spec draws on is `recon.md` in the same directory.

## The defects, as measured

### D-1 — a project can be forked into two contexts, and the fork destroys solve results

The dispatcher builds its background context with `PyPSAService.build_context()`
and never registers it (`services/solve_queue.py:369-385`; a grep for `register`
in that file returns nothing). Every other cold path registers what it builds.
Nothing in `activate_project` (`routers/projects.py:1915-2011`) checks whether the
target project has a running job — its only guard is `_solver_in_flight()`, which
keys on the *caller's* active context.

Proven by execution against the real backend. With project `X` saved and
non-resident, a queued solve blocked mid-`run_simulation`:

| Probe | Result |
|---|---|
| `PyPSAService.get_context(key_X)` while the job runs | `None` — the background ctx is invisible to the registry |
| `POST /api/projects/X/activate` | `200` — no guard refuses it |
| `get_context(key_X)` after activate | a **second**, distinct context, now the session's foreground |
| foreground ctx has dispatch | `False` |
| `GET /api/simulation/status` | `{'status': 'idle', 'dispatch': 'none'}` |
| `GET /api/simulation/log_stream` | `data: No simulation running` |
| disk after the job completes | dispatch present — the queue persisted correctly |
| disk after one ordinary foreground save | **dispatch gone** |

The last row is the defect: a plain save wipes the solve results. It needs no
unusual action — `switchToProject` calls `saveProjectQuietly(currentProject)` on
every tab switch (`frontend/src/utils/projectActions.ts:371`).

The frontend's `saveProjectQuietly` does skip while its store reads `running`
(`projectActions.ts:442-445`), but the SSE opened against the wrong context errors
immediately, retries three times and then sets `failed`
(`frontend/src/layout/AppHeader.tsx:611-627`), releasing that guard within seconds
while the solve is in fact healthy.

Reachable from ScenariosPanel's "queue this branch" (which queues non-resident
scenarios), from any project evicted under `RESIDENT_CAP` (default 5,
`services/pypsa_service.py:52`), and from the `solve_queue_enqueue` chat tool.

### D-2 — a queue drain reloads the current project from disk and discards unsaved edits

`frontend/src/layout/ProjectTabs.tsx:163-186` fires on any transition of the
global active-job count from `>0` to `0` and calls `projectsApi.load(currentProject)`,
which is `reset_network()` + `import_from_netcdf` (`routers/projects.py:2110-2114`).
No save precedes it. The project reloaded need never have been queued: queue A,
switch to B, edit B, A finishes, B reverts to its last saved state — logged as the
reassuring `Solve queue finished — resynced 'B'`.

The count is taken over every job in the response including other organisations'
redacted rows, so another tenant's batch draining reloads this user's editor.

Its own comment justifies it by "The swap-based queue solves through the SHARED
active slot", a design replaced by the per-project contexts described at
`services/solve_queue.py:9-21`. The effect is vestigial.

### D-3 — nothing enforces one active job per project

`SolveQueue.enqueue` appends unconditionally (`services/solve_queue.py:129-151`).
The invariant is re-implemented in three places on the client — `AppHeader`'s
`enqueuingRef`, `SolveQueuePanel`'s `activeProjects` set derived from a 1.5s poll,
and `ScenariosPanel`'s `inFlight` ref — and nowhere on the server. The
`solve_queue_enqueue` chat tool has no guard at all. ScenariosPanel's own comment
(`:532-536`) records the cost: a double click "really does run every project in
the branch twice … with the second run overwriting the first's results".

### D-4 — smaller defects

- `SolveQueuePanel.tsx:223` tells the user "While the queue runs, the active editor
  is busy" whenever any job is active. Contradicted by `routers/projects.py:1937-1941`,
  which exists precisely so the editor is *not* busy.
- `frontend/src/api/solveQueue.ts:8-20` types `project_id` and `error` as non-null
  and omits `project_key` entirely, while the backend nulls all three for a redacted
  job (`routers/solve_queue.py:89,147-149`) and always emits `project_key`. A
  redacted row renders a blank name; expanding a redacted completed row would fetch
  `/projects/null/results_bundle`.
- `services/shutdown.py:176` reads `job["project_name"]`, a key `to_public`
  (`services/solve_queue.py:92-110`) never emits, so every queue solve appears as
  `job <id>` in the quit confirmation.

### D-5 — a queued job solves with the config it finds at run time, not the one it was queued with

`services/solve_queue.py:389` reads `ctx.solver_state["solver_config"]` when the job
runs. `PUT /api/simulation/solver_config` (`routers/simulation.py:299-319`) mutates
that live. So a config edited after enqueue silently changes what the queued job
solves. Which config a job gets also depends on residency: a resident project uses
the in-memory value, a non-resident one whatever `_hydrate_context_from_disk` loaded
from `solver_config.json` (`routers/projects.py:1855-1866`). Durability widens both
windows.

## Architecture

### The invariant

**One `ProjectContext` per project, always — whoever is solving it.**

Enforced by a per-registry-key **hydrate-or-adopt lock**. The lock is taken only on
a registry miss, so the common path is unchanged: a `get_context` hit returns
without touching it. On a miss the holder re-checks the registry, and only then
builds, hydrates and registers.

There are **four** cold paths that build-and-register a context, not three:

| # | Path | Location | Frequency |
|---|---|---|---|
| 1 | `activate_project` | `routers/projects.py:1990-1998` | per project switch |
| 2 | `resolve_project_context` | `routers/deps.py:146-153` | per path-scoped read |
| 3 | `resolve_for_session` | `services/active_project.py:100-116` and the scratch branch `:129-134` | **twice per authenticated request, every route** — via `main.py:525` and the constructor dependency at `deps.py:78` / `main.py:342` |
| 4 | the dispatcher | `services/solve_queue.py:369-385` | per queued job |

Path 3 is the highest-frequency of the four. Omitting it would leave the invariant
false in the most common case.

Lock ordering, which must not be violated: **hydrate → `_registry_lock` →
`solve_queue._lock`**. No hydrate lock may be acquired while `_registry_lock` is
held. The existing rule that `_registry_lock` never nests a per-context
mutation lock (`services/pypsa_service.py:563-576`) stands unchanged.

### Consequence for shutdown

`services/shutdown.py:144-153` and the phase2a plan record, as a load-bearing
constraint, that a running queue job's context is in neither registry — which is why
`solves_in_flight()` reads the job table. Registering it makes that false, and
`_context_solves()` (`shutdown.py:109-137`) would count the same solve twice, once
per source. `tests/test_shutdown.py:1030` and `:1175` pin both halves.

Resolved by having the dispatcher set `kind="queue"` on its context claim, which it
does not do today (`services/solve_queue.py:402-411` writes `status`, `stop_event`,
`log_queue` and `thread` but no `kind`). `_context_solves()` then skips
queue-owned contexts, and the job table remains the single source for queue solves.
This mirrors `/run`, which sets `kind="lopf"` at `routers/simulation.py:591-599`
for exactly this purpose, and it also removes a pre-existing misclassification: an
unmarked background solve would report as `"active"`, i.e. as abortable through
`/api/simulation/abort`, which it is not.

### Consequence for the editor

The global middleware at `main.py:569-590` already refuses every write to
`/api/network/*` and `/api/io/*` while `_solver_in_flight()` holds on the caller's
context. Once activating a solving project lands the session on the solving
context, that gate makes the project read-only for the duration at no extra cost,
and the log, status and results become live and correct for free.

The frontend has no way to say so: `readOnly` is one boolean
(`store/uiStore.ts:296`) and `evaluateMutation` returns a single hardcoded message
about another user holding the lock (`utils/mutationGuard.ts:28-30`). It widens to
a three-state value so the reason is honest.

### Job status vocabulary

Today the status set is `queued | running | completed | failed | aborted`
(`services/solve_queue.py:79`), and the service already names the finished subset
`_TERMINAL = ("completed", "failed", "aborted")` (`services/solve_queue.py:57`).
R25 adds `interrupted`. The complete set after this work, and which members are
terminal:

| Status | Terminal | Meaning |
|---|---|---|
| `queued` | no | accepted, not yet started |
| `running` | no | a dispatcher worker is solving it |
| `completed` | **yes** | solved |
| `failed` | **yes** | raised |
| `aborted` | **yes** | a user stopped it, singly or through R29 |
| `interrupted` | **yes** | the process died under it; nobody stopped it (R25) |

**`terminal` is the single term the requirements use**, and it denotes exactly
`completed`, `failed`, `aborted` and `interrupted`. `_TERMINAL` gains
`"interrupted"` so the constant and the term remain the same set; its three
existing read sites (`services/solve_queue.py:203,260,273`) therefore treat an
`interrupted` job as finished. "Finished", "the job ends" and "a terminal job"
are not distinct concepts in this spec and are not used as requirement wording.

`interrupted` gets no exceptions. It is dismissible (R32) and requeueable (R31)
like any other terminal status, its log is retained and served like any other
terminal job's (R18, R20), and a transition into it invalidates caches like any
other terminal transition (R9). Its only distinctions are presentational —
R27's separate label and icon — and R25's rule that it is never re-enqueued
automatically.

## Requirements

### Increment 1 — stop the data loss

- **R1.** A per-registry-key hydrate-or-adopt lock exists, is taken only on a
  registry miss, re-checks the registry under the lock before building, and is
  documented with the ordering `hydrate → _registry_lock → solve_queue._lock`.
- **R2.** All four cold paths in the Architecture table route their build-and-register
  through R1's lock, including both branches of `resolve_for_session`.
- **R3.** The dispatcher registers the context it builds for a non-resident project,
  under the same key `get_context` would resolve.
- **R4.** The dispatcher sets `kind="queue"` on its context claim.
- **R5.** `shutdown._context_solves()` skips contexts whose `kind` is `"queue"`, and
  `solves_in_flight()` reports exactly one entry per running queue job. The two
  existing tests at `tests/test_shutdown.py:1030` and `:1175` still pass unmodified.
- **R6.** Activating a project with a running queue job resolves to the solving
  context: `GET /api/simulation/status` reports `running`, `GET /api/simulation/log_stream`
  yields that job's log, and no second context is created.
- **R7.** After that job completes, the activated context holds the results with no
  reload, and a subsequent foreground save does not remove dispatch from disk.
- **R8.** The `>0 → 0` resync effect in `ProjectTabs.tsx` is deleted.
- **R9.** When a job transitions into a **terminal** status as defined in
  Architecture § Job status vocabulary, the React Query caches for **that job's
  project only** are invalidated — `results`, `simulationStatus` and `meta`. No other
  project's cache is touched and no backend reload is issued. All four terminal
  statuses invalidate, `interrupted` included; the invalidation carries no
  per-status branch.
- **R10.** `readOnly` carries a reason: `writable | locked-by-user | solving`.
  `evaluateMutation` returns a distinct message per reason. Every existing consumer
  enumerated in `recon.md` §9.2 keeps its current behaviour for the two pre-existing
  states.
- **R11.** The project the user is viewing presents as read-only, with the solving
  reason, whenever a queue job is running on it.
- **R12.** The panel no longer asserts that a running queue makes the active editor
  busy. The replacement text must not claim the editor is blocked while the queue
  runs on a different project, since `routers/projects.py:1937-1941` permits exactly
  that.
- **R13.** `SolveJob` in `api/solveQueue.ts` types `project_id` and `error` as
  nullable and declares `project_key`. A row whose `project_id` is null renders a
  fixed non-empty label instead of an empty element, and its expand control is
  disabled so no request to `/projects/{name}/results_bundle` can be issued with a
  null name.
- **R14.** A regression test reproduces D-1 end to end and fails against the
  pre-R1 code.

### Increment 2 — correctness and visibility

- **R15.** `POST /api/simulation/queue` for a project that already has a `queued` or
  `running` job returns `200` with that existing job and `already_queued: true`. It
  creates no second job. A project with no active job returns the new job with
  `already_queued: false`.
- **R16.** The three client-side duplicate guards are no longer the enforcement
  point; the server refuses duplicates for every caller including the chat tool.
- **R17.** Each `SolveJob` owns its `BufferedLogQueue` for the life of the job.
- **R18.** `GET /api/simulation/queue/{job_id}/log_stream` streams that job's log
  live, and a history endpoint returns its retained lines once the job is
  **terminal**. Retention and the history endpoint apply to all four terminal
  statuses, `interrupted` included — an `interrupted` job's lines are retained and
  served exactly as a `completed` one's. Both endpoints are authorized by the same
  predicate as the listing (`_may_see`), and both answer `404` — byte-identical to
  the genuine not-found message — when the caller may not see the job.
- **R19.** A job's log is readable through R18 both while the job is `running` and
  after it has reached any **terminal** status, regardless of which project the
  caller is viewing and regardless of whether the job's context is still resident.
- **R20.** `SolveQueuePanel`'s expand control shows the live log for a `running`
  row and the retained log for a **terminal** row. It does so for all four terminal
  statuses, `interrupted` included.
- **R21.** The `solve_queue_enqueue` tool description in `chat_tools_schema.py`
  declares `already_queued`.

### Increment 3 — durability, controls, concurrency

- **R22.** A `solve_jobs` table persists every job, with a UUID primary key, an
  `enqueued_by_user_id` column, and a column holding the solver config the job was
  enqueued with. An Alembic migration creates it.
- **R23.** Job identity is a UUID everywhere it is exposed: the abort route's path
  parameter, `SolveJob.id` in `api/solveQueue.ts`, and the `solve_queue_abort` chat
  tool.
- **R24.** The dispatcher solves a job with the config stored on that job (R22), not
  with whatever the context holds at run time. Editing the solver config after
  enqueue does not change what an already-queued job solves.
- **R25.** On boot, every job left `running` becomes `interrupted` and is never
  re-enqueued automatically. Every job left `queued` is re-enqueued and the
  dispatcher starts.
- **R26.** Boot reconciliation runs in `main.py`'s `lifespan` and cannot fail the
  boot, following the pattern of `_chatbot_startup_check` (`main.py:801-819`).
- **R27.** `interrupted` is a distinct job status with its own label and icon in the
  panel, visually separate from `aborted`.
- **R28.** Quitting the desktop app no longer aborts queued jobs; they persist and
  resume under R25. A running job is still stopped, and the quit confirmation names
  its project rather than `job <id>` — the `shutdown.py:176` `project_name` bug is
  fixed.
- **R29.** Cancelling queued jobs in bulk is one operation, and its scope is
  exactly the `queued` jobs the caller could cancel individually. Every candidate
  is filtered through the existing `_may_abort` predicate
  (`routers/solve_queue.py:152-185`) — the same predicate the single-job abort
  route already applies (`routers/solve_queue.py:257`) — so for every job in the
  queue the two agree: a caller who cannot abort a job individually cannot cancel
  it through the bulk operation. Jobs the caller may not cancel stay `queued` and
  otherwise untouched, and the response reports only the count the caller actually
  cancelled. `running` jobs are outside this operation's scope; stopping one
  remains the single-job abort. There is **no global variant and no super-admin
  escalation**: `clear_finished`'s unconditionally-global precedent
  (`services/solve_queue.py:190-200`, `routers/solve_queue.py:267-295`) is
  deliberately not followed, because clearing finished rows is listing hygiene
  while this destroys queued work.
- **R30.** The dispatcher can be paused and resumed. Pausing lets the running job or
  jobs finish and starts no more; resuming continues in FIFO order.
- **R31.** A job in any **terminal** status can be requeued in one action,
  producing a new `queued` job for the same project, subject to R15. All four
  terminal statuses are eligible, `interrupted` included and on the same terms as
  the other three — R25 bars only *automatic* re-enqueue at boot, not a user's
  explicit requeue. A `queued` or `running` job is not requeueable.
- **R32.** A user can dismiss **terminal** jobs from their own view, filtered on
  `enqueued_by_user_id`. All four terminal statuses are dismissible, `interrupted`
  included; a `queued` or `running` job is not. Dismissal is per user and does not
  affect another user's listing. The existing super-admin `clear_finished` is
  unchanged.
- **R33.** `PYPSA_GUI_MAX_CONCURRENT_SOLVES` bounds how many jobs run at once and
  defaults to `1`. At the default, observable behaviour is unchanged from increment 2.
- **R34.** The listing reports `running` as a list of job ids in place of the scalar
  `current`. `api/solveQueue.ts` and the `solve_queue_list` tool description in
  `chat_tools_schema.py` are updated to match.
- **R35.** At a concurrency above 1, no two jobs solving different projects share a
  `ProjectContext` or a mutation lock, and the netCDF I/O lock still serialises every
  write to disk.
- **R36.** Contexts belonging to jobs that are running under R33 are all protected
  from eviction, not merely one.
- **R37.** The `solve_queue_abort` tool description in `chat_tools_schema.py`
  (`services/chat_tools_schema.py:676-681`; the description string is `:678`,
  today `"Abort a running OR cancel a queued job. Safety: destructive."`) states
  that `job_id` is a UUID. This is D18's third chat-surface update and is distinct
  from R23: R23 makes the id a UUID on the wire, R37 documents that fact to the
  model. It lands in increment 3 alongside R23.

## Success criteria

Each is independently verifiable.

1. The D-1 probe sequence leaves solve results on disk after a foreground save.
2. Activating a project mid-solve creates no second context and shows a live log.
3. Editing project B while an unrelated queued batch drains leaves B's unsaved edits intact.
4. A second enqueue of the same project returns the first job and creates nothing.
5. A job's log is readable while viewing a different project.
6. Killing and restarting the process leaves queued jobs queued and the previously running job `interrupted`.
7. A job enqueued under one solver config solves under that config after the config is changed.
8. Two users each see only their own dismissed rows.
9. With `PYPSA_GUI_MAX_CONCURRENT_SOLVES` unset, the backend suite passes unchanged.
10. The full backend and frontend suites pass at every increment boundary.

## Verification

The baseline at branch base `c2cc4510`, established before any change, is **2282
passed / 22 skipped / 0 failed** for `pixi run gui-tests` and **660 passed / 0
skipped / 0 failed across 82 files** for the frontend suite.

The baseline is a **no-regression floor, not an exact match.** An exact match is
not achievable and is not the intent: R14's regression test and R25's boot test
raise the passed count by construction, so "the baseline still holds" would be
false the moment either lands. `N` is defined **per suite**: `N_backend` is the
net number of test cases that increment's diff adds **to the backend suite** and
`N_frontend` the net it adds **to the frontend suite** — in each case cases added
minus cases removed in that suite, counted from the diff, `N >= 0`. The two are
independent; neither constrains the other. At each increment boundary each suite
must satisfy all three conditions:

| Condition | Backend (`pixi run gui-tests`) | Frontend (`npm run test`) |
|---|---|---|
| failed | `== 0` | `== 0` |
| skipped | `<= 22` | `<= 0` |
| passed | `== 2282 + N_backend` | `== 660 + N_frontend` |

A suite's `N` is `0` whenever that increment's diff adds no test to that suite,
which is the ordinary case for the untouched suite of a single-sided increment:
R14's and R25's tests are backend-only, so `N_frontend == 0` at those
boundaries. Requiring `passed == baseline + N` rather than `passed >= baseline`
is deliberate: a deleted test compensated by an added one nets to the baseline
total and would pass a floor-only check, so the diff's `N` is what makes the
condition falsifiable. A passed count below that suite's floor, or above it by
any amount other than that suite's `N`, fails the gate. Success criterion 10
means exactly these three conditions, per suite, and nothing more.

R14's regression test is the load-bearing one: it must fail against the pre-R1 code.
The probe that proved D-1 is the template — block the dispatcher inside
`run_simulation`, activate the project, assert a single context, release, assert
disk dispatch survives a foreground save.

R5 is verified by the two existing shutdown tests passing unmodified; changing them
would defeat their purpose.

R25's "never re-enqueued automatically" is what prevents a job that crashed the
process from crash-looping the boot, and needs a test that a `running` job at boot
does not start.

R33's default is verified negatively, and under the same floor rule: with the
variable unset, increment 3's backend suite meets the three conditions above and
no test that passed at increment 2's boundary fails. That, and not literal
equality of the counts, is what success criterion 9's "passes unchanged" means.

## Constraints

- The toolchain is pixi-provided. `node` and `npm` are not on the system PATH.
- Canonical backend gate: `pixi run gui-tests` (`pixi.toml:236`), which resolves to
  the `test` environment because it is declared under `[feature.test.tasks]`. Run
  from the root task table it resolves to `default`, where two desktop tests skip and
  the suite still reads green.
- Frontend: `npm run test` (vitest) and `npm run build` (`tsc -b && vite build`,
  which is the only static gate — no JS/TS linter is configured).
- Python lint: `ruff check .`, with `pypsa-gui/**` exempted from `E402, E701, E702,
  I001` (`ruff.toml:61-66`) because mid-file imports break router/service cycles
  deliberately.
- Observed runtime in the `test` environment: Python 3.13.0, PyPSA 1.1.2. The
  `python ==3.12.12` and `pypsa ==1.0.3` pins at `pixi.toml:214,216` belong to
  `[feature.doc.dependencies]`, a docs-only feature declared `no-default-feature`,
  and do not apply.
- CI does not run the GUI suite; `.github/workflows/test.yaml` has no `pypsa-gui` job.
- This repository has no domain glossary (`CONTEXT.md`, `CONTEXT-MAP.md`) and no ADR
  directory (`docs/adr/`). This spec adds neither and introduces no term requiring one.

## Out of scope

- **Drag-reorder of the queue.** FIFO order stays fixed.
- **Parameter sweeps.** One project cannot be queued N times with N configs. R24
  snapshots the config for determinism, not to enable duplicates — R15 still refuses
  them. Variants are expressed as scenario projects, which `create_scenario` and
  ScenariosPanel's "queue this branch" already support, and which give each variant
  its own results on disk and its own row in Compare.
- **Idle polling of the queue.** `useSolveQueue` stops polling when nothing is
  active, so a client does not learn about a job enqueued elsewhere until something
  invalidates the key. Unchanged here.
- **Running the GUI suite in CI.** Out of scope, noted because it means these suites
  are only ever run locally.
