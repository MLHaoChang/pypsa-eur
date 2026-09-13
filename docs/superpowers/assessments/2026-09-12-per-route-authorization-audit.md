# Per-route authorization audit

Date: 2026-09-12
Scope: all 267 routes on the `pypsa-gui` backend app, at `30d6206`.
Question: for each route, **who may call it, and what enforces that?**

Method: routes enumerated from the live app's OpenAPI schema (the router
objects are lazily wrapped, so walking `app.routes` yields 2 of 267 — a trap
worth recording). Each enforcement mechanism was then read in source and the
route set it actually covers computed, rather than inferred from naming. Where a
first pass by path prefix disagreed with the code, the code won — see
"Calibration" for two cases where the prefix heuristic was wrong and the routes
turned out to be properly gated.

## The enforcement mechanisms, and what each really covers

| Mechanism | Where | Covers |
| --- | --- | --- |
| Global auth gate → 401 | `main.py:658` | every `/api/*` route except 5 public paths |
| CSRF (origin + double-submit) | `main.py:645` | state-changing methods on `/api`, **before** session resolution |
| Foreign-lock middleware | `main.py:815` | **writes** under `/api/network/`, `/api/io/`, `/api/simulation/`, minus a 3-route queue allowlist |
| `_enforce_project_lock` | route bodies in `projects.py`, `snapshots.py` | `/api/projects/*` write edges |
| `require_project_access` / `ProjectAccessDep` | `routers/deps.py:100` | only the routers that opted in — 6 of 23 |
| `_require_admin_actor` | `routers/admin.py` | 4 call sites, `/api/admin/*` only |
| `reject_unless_local_mode` | `routers/local_settings.py:45` | all of `/api/local-settings/*` |
| `reject_in_local_mode` | `main.py:1071` | all of `/api/admin/*` |

Counted against the 267 routes: 103 reads, 73 writes under the foreign-lock
middleware, 41 under the projects/snapshots route edges, 9 admin, 5 public, 3 on
the queue allowlist — and **33 writes that none of the lock or tenancy
mechanisms reach**. Of those 33, verification cleared 21 (see Calibration) and
confirmed problems in 12.

## Finding 1 — the adequacy studies re-solve the shared network with no lock check

**The highest-value thing in this audit.** Ten routes:

```
POST /api/results/frontier        + /frontier/abort
POST /api/results/mc              + /mc/abort
POST /api/results/fmea_sweep      + /fmea_sweep/abort
POST /api/results/margin_loop     + /margin_loop/abort
POST /api/results/coupling_loop   + /coupling_loop/abort
```

`post_frontier` (`routers/results.py:1104`) takes `PyPSAService.get_network()` —
the **resident, shared** network — and runs a full capacity-expansion solve per
target in a worker thread. `routers/results.py` contains **no lock or holder
check of any kind**: grepping it for `get_lock(db`, `holder_user_id`,
`_enforce_project_lock` and `project_locks` returns nothing.

It is not covered by the foreign-lock middleware either, because that middleware
is a **prefix list** — `/api/network/`, `/api/io/`, `/api/simulation/` — and
`/api/results/` is not on it.

What does exist is `_refuse_if_mesh_busy` + `_publish_study`, which serialise
studies against each other and against a foreground solve under the PyPSA
mutation lock. That is **thread safety, not authorization**: it stops two studies
overlapping, not a non-holder from starting one.

So: the resident `ProjectContext` is shared per `(org, project)` — the
foreign-lock middleware's own comment says a holder's session and a non-holder's
session that `activate` the same project point at the *same in-memory network*.
A user who does not hold the lock can therefore `POST /api/results/frontier` and
re-solve the holder's network, and the holder's next autosave persists it.
`_publish_study`'s comment confirms these mutations reach disk: it was written
because "a sweep's first, lock-free contingency mutation was landing on disk as
the user's project".

This is the same class as `2026-08-27-requeue-is-a-cross-user-overwrite`, which
**was fixed** by porting `enqueue_solve`'s holder check into the route. The same
port is the fix here, and the pattern is already in the codebase.

Server / multi-tenant only.

## Finding 2 — chat sessions have no owner, so the confirmation gate rests on id secrecy

`ChatSession` (`services/chat_service.py:474`) has no user or owner field, and
`get_session` (`:745`) is a plain lookup in a process-global `_SESSIONS` dict.
Nothing compares the caller to the session. So for any authenticated user:

```
POST /api/chat/{session_id}/abort     — end someone else's in-flight turn
POST /api/chat/{session_id}/rewind    — truncate someone else's conversation
POST /api/chat/{session_id}/confirm   — SUPPLY THE APPROVAL for someone else's
                                        pending destructive-tool confirmation
```

The third is the one that matters. The safety-tier system's entire protection for
destructive and execution tools is that *the user must approve*; if the approval
can come from another account, that gate is cross-user forgeable.

**Calibrated honestly: this is not currently exploitable.** `session_id` is
`uuid.uuid4().hex` — 122 bits — so it cannot be guessed, and greps found it
neither logged nor persisted into `chat.jsonl`. It is a capability-style secret
and a strong one.

The problem is that this is load-bearing and undeclared. The confirmation gate's
security depends on session-id secrecy, nothing in the code says so, and the day
a session id becomes visible — a support endpoint, a debug log line, an error
body, a screenshot — the gate is forgeable with no code change and no review
step that would catch it. An ownership check costs one comparison and removes the
dependency.

## Finding 3 — the authorization pattern exists and is adopted by 6 of 23 routers

`routers/deps.py:100` defines `ProjectAccessDep = Depends(require_project_access)`:
a real per-route dependency that resolves the project *named by the request* and
checks the caller's access to it. It is used by `compare.py`,
`adequacy_worksheet.py`, `uploads.py`, `snapshots.py`, `gridspine.py` and
`deps.py` itself.

It is **not** used by the five routers carrying most of the surface:
`network.py` (81 routes), `results.py` (48), `chat.py` (20), `simulation.py` (14)
and `io.py` (8) — 171 of 267 routes. Those rely instead on the path-prefix
middleware, and only one router in the whole app (`local_settings.py`) carries a
constructor-level dependency.

That is the structural cause of Finding 1, and it is a *shape*, not a one-off: a
prefix list denies by omission. A new router mounted under a new prefix is
ungated by default, silently, with nothing failing. A dependency declared on the
route is the opposite default. The gridspine router shows the good version —
`run_pipeline(proj: AuthorizedProject = ProjectAccessDep, ...)` — and it is the
newest of the five surfaces, which suggests the pattern is winning and simply has
not been applied backwards.

## Finding 4 — `/api/simulation/run` still has no admin gate (assessment gap 2, refined)

Refining what the 2026-09-10 hardening assessment said, now that the middleware
coverage is mapped precisely: `/api/simulation/` **is** under the foreign-lock
middleware, so a non-holder is refused. The escalation is therefore narrower than
"any authenticated user" — but it is still real. Any member acting on a project
they legitimately hold can set `extra_functionality_code` via
`PUT /api/simulation/config`'s permissive `merged.update(submitted)`
(`routers/simulation.py:349`) and have it `exec()`-ed in-process by
`POST /api/simulation/run`, with full filesystem and network access, whenever the
operator has set the process-wide `PYPSA_GUI_ALLOW_USER_CODE`.

`routers/simulation.py` contains no `Depends` at all. Being a *lock holder* is
not a privilege level; it means "nobody else is editing this", not "may execute
code on the host".

## Calibration — what the prefix pass flagged and the code cleared

Both of these were in the 33 "uncovered writes" until read properly, and both
are correctly gated. They are recorded because each was gated in a place a
path-based audit does not look.

- **`/api/local-settings/*`** — `routers/local_settings.py:45` builds
  `APIRouter(dependencies=[Depends(local_mode.reject_unless_local_mode)])`, so
  every route in it is refused outside the desktop build. `PUT /anthropic-key`
  writes a process-wide credential and would be a serious finding without that
  guard; it has it. The guard is in the router *constructor*, not at the
  `include_router` call, so checking `main.py` alone misses it.
- **`/api/gridspine/*`** — uses `ProjectAccessDep` per route, as above.

Also holding, and not to be re-litigated: `/api/admin/*` carries both
`reject_in_local_mode` at the mount and `_require_admin_actor` in its bodies;
the queue allowlist routes each run their own holder check (that is the
2026-08-27 requeue fix); `POST /api/auth/logout` acts only on the caller's own
session.

## Recommendation

1. **Port the holder check into the five study routes** (Finding 1). Smallest
   change, closes a live cross-user write, and the pattern is already in
   `enqueue_solve` and `requeue_job`.
2. **Give `ChatSession` an owner and compare it** in `confirm` / `abort` /
   `rewind` (Finding 2). One field, one comparison; removes a silent dependency
   on id secrecy from the control that guards every destructive tool.
3. **Adopt `ProjectAccessDep` on the five hold-out routers** (Finding 3), as
   work that can proceed router by router. Until then, treat the prefix lists in
   `main.py` as the authorization surface they actually are, and add a test that
   fails when a route is mounted under a prefix no mechanism covers — the
   omission, not the route, is what needs to become loud.
