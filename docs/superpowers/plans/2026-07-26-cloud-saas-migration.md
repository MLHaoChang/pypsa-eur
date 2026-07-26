# Cloud / SaaS Migration Plan — pypsa-gui

> **For agentic workers:** implement task-by-task. Steps use checkbox (`- [ ]`) syntax.
> Every task is TDD: failing test → implement → pass → commit.
>
> **v3** — two adversarial review rounds against source. v1 was wrong in
> load-bearing ways; v2 fixed those but mis-scoped Step 0 and introduced a new
> error. Appendix A records every correction so none is re-derived. Read it
> before trusting any prior notes about this codebase.

**Goal:** Make the `pypsa-gui` backend horizontally scalable and hostable, so the
frontend can ship as a multi-tenant SaaS. Single-user mode is legacy and is
**removed**, not preserved.

**Diagnosis:** two independent problems, and v1 conflated them.

1. **The app is not multi-tenant safe *on one machine*.** 13 of 16 routers
   enforce **no authorization at all**; the process-global project registry is
   keyed by project *name*; and there is **no CSRF defence** while production
   cookies are `SameSite=None`. This blocks hosting outright, at any replica
   count. Step 0.
2. **The backend keeps its working set in process memory.** This blocks
   horizontal scaling. Steps 2–3.

**The hinge between them.** Most routes name no project at all —
`GET /api/network/buses`, `GET /api/results/cost_breakdown` — because they
resolve through the *process-global active project*. There is nothing for an
ACL to bind to. The fix is to move "active project" from a **process global** to
a **session attribute in the database** (Step 0b). That is a small, self-
contained change that simultaneously closes the tenancy hole and removes the
first pillar of process state — so Step 0 and Step 3 share a direction rather
than a dependency cycle.

**Prior art:** `docs/superpowers/plans/2026-07-26-multi-user-org-tenancy.md`
landed Postgres for users/orgs/sessions/registry/ACL/locks and left a
*"path resolver seam for later object storage"*. This plan cashes in that seam —
and closes the authorization gap that plan left open outside `projects.py`.

---

## Global constraints

- **Multi-user only.** `PYPSA_GUI_AUTH_ENABLED=false` is deleted, not defaulted.
  Its removal is its own task with a test sweep (`tests/conftest.py` currently
  pins it false; every auth-off branch must go with it).
- **Never break the zip.** `GET /api/projects/{name}/bundle` (`.pypsaproj.zip`)
  is the user's escape hatch. Its round-trip case must pass at the end of
  *every* step.
- **Reversible.** Every step ships behind a flag or with a documented rollback;
  any data migration is dry-runnable and idempotent.
- **Every "two replicas" test must prove which replica answered.** A
  replica-identifying response header is mandatory infrastructure, added in
  Step 0. Without it a sticky proxy makes multi-replica tests pass while state
  is still process-local — the exact failure mode `qa_e2e.py` already had when
  it asserted on HTTP 200 alone.
- Do not redesign the workbench, Scenarios panel, or Compare UX. **Exception:**
  Step 2 necessarily changes `createLogStream` (see Step 2 frontend budget).
- Preserve the SSE contract (`[PHASE]`, `TRACEBACK:`, `/log_history` replay,
  and the chat fanout subscriber).

## Non-goals

- Blobs *inside* the database — PyPSA needs a real path
  (`projects.py:1355` `n.export_to_netcdf(str(p))`; netCDF4/h5py want a file).
- SSO/SAML, per-node ACL, billing implementation, K8s manifests.
- Rewriting the solver or PyPSA interaction.

## Baseline counts (re-measure before trusting; scope matters)

`get_network()` call sites — **114** in `routers/` + `services/` + `main.py`;
**133** across the whole backend excluding `tests/`. Use **133** when sizing
Step 3: the extra 19 are exactly the ones easy to forget.
`qa_e2e.py`: **43** distinct assertion ids (sub-ids like `S1.1a`/`S1.1b` count
separately) across 48 `record()` sites. Backend tests collected: **1117**.
Vitest: **123**.

Re-measure at the start of each step; both this plan and its first review quoted
different figures for the same symbol because they used different scopes:

```bash
grep -rn 'get_network()' --include='*.py' . | grep -v '/tests/' | wc -l
pytest --collect-only -q | tail -1
```

---

## Step 0 — Make it tenant-safe on one machine  ← **first; blocks hosting at all**

Split into **0a** (routes that already name a project) and **0b** (routes that
do not). v2 specified one undifferentiated dependency and was wrong: measured,
`require_project_access(project_id)` has nothing to bind to on most routes.

**Route inventory — measured, not assumed:**

| Router | routes | with a project param | Group |
|---|---|---|---|
| `uploads.py` | 6 | 6 | **0a** |
| `snapshots.py` | 4 | 4 | **0a** |
| `compare.py` | 2 | 2 | **0a** |
| `project_network.py` | 2 | 2 | **0a** |
| `network.py` | 79 | **0** | **0b** (all 79) |
| `vintage.py` | 5 | **0** | **0b** (all 5) |
| `results.py` | **28** | 0 | **0b** |
| `simulation.py` | 14 | 0 | **0b** |
| `io.py` | 8 | 0 | **0b** |
| `changelog.py` | 2 | 0 | **0b** |
| `clustering.py` | 1 | 0 | **0b** |
| `chat.py` | 8 | 0 (`{session_id}`) | 0b via session→project |
| `solve_queue.py` | 4 | 0 (`{job_id}`) | 0b via job→project |

> **Implementer trap 1:** `results.py` declares `results_router`, not `router`
> (`routers/results.py:63`, mounted at `main.py:341`). A grep for
> `@router\.` reports **zero** routes there. Count decorators per module's own
> router symbol.
>
> **Implementer trap 2 — do not count path params as project ids.**
> `network.py`'s 24 `{name}` params name **buses, carriers, lines**
> (`PUT /buses/{name}`), and `vintage.py`'s are `{component_class}/{name}`.
> **Zero** route paths in either module contain "project". Earlier drafts of
> this plan put those 26 routes in 0a on the strength of "has a path param" —
> they are 0b. Verify with:
> `grep -cE '@router\.[a-z]+\("[^"]*project' routers/network.py` → 0.

**0a-eligible route count: 14** — `uploads` 6, `snapshots` 4, `compare` 2,
`project_network` 2. Everything else is 0b.

### Step 0a — authorize what can be authorized  ✅ **LANDED**

> **Implemented.** Backend suite re-pinned at **1143 passed / 1 skipped** (was
> 1117; the old baseline did not survive, exactly as this plan warned). Frontend:
> tsc clean, **132** vitest (was 123), production build OK. All 14 E2E cases in
> `backend/tests/test_qa_step0a.py` pass, and the login → workbench journey was
> verified in headless Chrome (10/10, including a forged write refused with 403
> and the same write accepted with the token).
>
> **Two things this step found that the plan did not predict:**
>
> 1. **`POST /api/simulation/queue` was the widest hole, and it is not in the
>    route inventory.** It takes its project in the BODY, so a path-parameter
>    census cannot see it. It resolved through `_safe_project_dir` — any org's
>    project by name — then handed that directory to a background thread that
>    solves it *and saves it back*. Now resolved through the caller's org+ACL,
>    with the authorized directory travelling on the job (the dispatcher runs
>    with no request and no user and cannot authorize anything itself).
> 2. **Re-keying the registry needs `set_loaded_project` to CLEAR tenant
>    identity.** Rebinding only the NAME leaves a context whose `registry_key`
>    points at one project and whose `loaded_project` names another. A
>    background solve then believed it owned the foreground context and refused
>    its own save with a 409. `get_binding`/`set_binding` now move all four
>    fields together, and the three reset-then-rebind paths (undo restore,
>    snapshot restore, rename) use them.
>
> **403 → 404 sweep, narrowed with a rule.** Only `_org_id_for` on the LOOKUP
> path was an oracle; it now degrades to "no match" so an orgless caller gets
> the same 404 as anyone else. The three remaining 403s (delete/rename/manage
> members) all sit *after* `resolve_project`, so the caller has already proved
> read access and 403 is the honest answer — 404 there would be a lie, not a
> defence.

- `require_project_access(project_id)` on the **14** routes that carry a real
  project path param (`uploads`, `snapshots`, `compare`, `project_network`).
- **Registry re-key** from project *name* to `(org_id, project_uuid)`.
  `projects.py:1713-1715` documents today's name key; names are unique per org
  but the registry is per process, so two orgs' `Baseline` collide.
- `ProjectDep` resolves through `project_registry`, not the flat legacy path
  (`deps.py:48-50`, `snapshots.py:383`, `compare.py:70`, `upload_service._resolve_paths`).
- **Changelog tenancy** — `org_id` column + ACL; `DELETE /` requires auth and is
  scoped to the caller's org (`change_log_service.py:20`, `routers/changelog.py`).
- **CSRF + the CORS allowlist, together — they are one trust-boundary decision.**
  `auth.py:_cookie_flags` returns `("none", True)` for **any HTTPS non-local
  host** — i.e. production — and `main.py:139` sets `allow_credentials=True`.
  There are **zero** CSRF tokens in the codebase, so every state-changing route
  is cross-site forgeable, including the destructive `POST /api/projects/{name}`
  and `DELETE /{name}?cascade=true`. Add a double-submit token **and** an
  Origin/Referer check.
  **Replace `allow_origin_regex=r"https://.*\.cursorusercontent\.com"`
  (`main.py:137-139`) with an env-driven explicit allowlist IN THIS STEP.**
  Shipping CSRF while that wildcard stands is bypassable: any page on a
  `*.cursorusercontent.com` subdomain is an allowlisted *credentialed* origin,
  so the Origin/Referer check passes it, CORS lets it read the response, and it
  therefore reads the double-submit token and forges. `SameSite=None` means the
  cookie rides along. 0a is the step that "blocks hosting at all" — i.e. the one
  that may ship to production alone — which is exactly when this bites.
- **Login rate limiting.** `/api/auth/login` is in `_AUTH_PUBLIC_PATHS`
  (`main.py:108-114`) with no throttle; `_RATE_BUCKETS` (`chat_service.py:216`)
  covers chat only. Credential stuffing is unimpeded.
- **403 → 404 consistency sweep.** `projects.py:2178` and
  `project_registry._org_id_for:53-57` raise **403**, which is an existence
  oracle and contradicts `project_acl`'s 404 policy. Required for S0.1.
- **Solver log leak — use a thread-ident filter, NOT a narrower logger.**
  v2 said "scope it to the solver's own logger"; that is **wrong** and would
  empty the solve log, because the root handler at `solver_service.py:654-656`
  is deliberately capturing `pypsa.*` / `linopy.*` / HiGHS output — that output
  *is* the log. The codebase already solves this: `_RollingWindowFailureCatcher`
  (`solver_service.py:614-622`) filters on `threading.get_ident()` captured at
  construction, with a comment explaining that a concurrent solve would
  otherwise cross-attribute. Apply the same filter to the `QueueHandler`.
  **Step 2 caveat:** a thread-ident filter is correct only while jobs run one
  per thread. If the Step 2 worker runs jobs asyncio-concurrent on one event
  loop, thread ident degenerates to a constant and cross-job contamination
  returns silently — use a `contextvars.ContextVar`, which survives both threads
  and tasks. Decide when the broker is chosen.
- **`PRAGMA foreign_keys=ON`** for the SQLite path — currently absent
  everywhere in `db/` and `alembic/`, so `ON DELETE SET NULL` silently never
  fires. Decide here, not in a test.
- **Delete `PYPSA_GUI_AUTH_ENABLED`** and every auth-off branch.
  `tests/conftest.py:40` pins it `"false"`, so the entire current suite
  exercises the mode being removed — **expect a large test rewrite and re-pin
  the baseline count afterwards.** Do not treat 1117 as invariant across this step.
- **`X-PyPSA-Replica`** response header, **opaque** (hashed per-process id, or
  non-production only) — it is mandated test infrastructure but must not leak
  topology.

**Files (0a):** the 13 routers, `routers/deps.py`, `services/pypsa_service.py`,
`services/project_context.py` (`loaded_project` is typed `str` at `:107` and
becomes a composite key), `services/solve_queue.py` (`:323-324` compares
`project_id == active_id`), `services/change_log_service.py`,
`services/solver_service.py`, `services/auth_service.py`, `routers/auth.py`,
`main.py`, `db/session.py`, `tests/conftest.py`, and
`frontend/src/utils/projectActions.ts:376-397` (consumes the
`{activated, evicted}` payload keyed by the old id).

### Step 0b — session-bound active project

The ~110 routes with no project identifier resolve through
`PyPSAService.get_network()` → the process-global active context. There is
nothing to authorize. Two options were considered; **path-scoping every route is
rejected** (it is the workbench API redesign this plan forbids).

**Chosen: move "active project" from a process global to a session attribute in
the database.** `GET /api/network/buses` then resolves
`project = session.active_project_id`, which is per-user, ACL-checkable, and
survives a replica change.

This is *not* circular with Step 3, despite sharing surface. Step 0b moves the
**pointer** (which project this user is looking at) into the session; Step 3
moves the **payload** (the resident `pypsa.Network`) out of process memory. 0b
is a precondition for 3, not the reverse — and 0b is completable standalone,
because on one machine the payload can stay in `_contexts` exactly as it is.

Two cases 0b must handle explicitly:

- **The unbound "New Project" state.** `pypsa_service.py:31-33` says `_active`
  *"uniquely handles the UNBOUND (New Project) case the registry can't key (no
  project_id yet)"*. A session's `active_project_id` would be an FK to a row
  that does not exist for a fresh network or after `POST /api/network/reset` —
  which is the **default state on first load**. Either create a per-session
  scratch/draft project row, or key unbound contexts by session id. Decide here.
- **The eviction protected set must become plural.** `_evict_if_over_cap:393-395`
  reads a single `cls.get_active_id()`. Under 0b there are N active projects —
  one per session — and eviction is still **write-back**
  (`_save_evicted_ctx:466-484`), so the 6th concurrent user's activation can
  evict *and flush* another user's live editing context. Protect every session's
  active project. (Step 3 removes write-back entirely, but 0b ships first.)

**Files (0b):** `db/models.py` (session gains `active_project_id`),
`services/auth_service.py`, `routers/deps.py`, `services/pypsa_service.py`,
`routers/{network,results,simulation,io,changelog,clustering,vintage}.py`,
`routers/{chat,solve_queue}.py` (session→project and job→project indirection).

### E2E QA — Step 0

| ID | Case | Assert |
|---|---|---|
| S0.1 | **Per-router tenant sweep.** For every route carrying a project param, org B names org A's project (by name **and** by uuid) | **404** every time — never 403, never 200. Body byte-identical to the genuinely-not-found body. Requires the 403→404 sweep. |
| S0.2 | `_AUTH_PUBLIC_PATHS` guard | The set has exactly its 5 known members; a new public path fails the test. (v2's "unauthenticated → 401" was **vacuous** — `main.py:160-191` already does that.) |
| S0.3 | Org A and org B both create `Baseline`; both activate; A adds a bus | B's `GET /network/buses` does not show it. Targets the name-key collision directly. |
| S0.4 | Two orgs, identically-named projects, hit each `ProjectDep` route | Each receives **its own org's data**. (v2 asserted a resolved *path* in a debug header — that is both weaker and itself a leak.) |
| S0.5 | Org B reads `GET /changelog` after org A edits | Only its own entries. `DELETE /` as B leaves A's intact; anonymous → 401. |
| S0.6 | Tenant A solves while tenant B triggers an ERROR log on the same process | A's SSE contains no line from B's request — **and** A's stream still contains `pypsa`/`linopy`/HiGHS lines (guards against the v2 remedy that would have emptied it). |
| S0.7 | Replica header | **Stable** across N calls to a directly-addressed replica, and **differs** between two directly-addressed replicas. (v2's "differs across calls" flakes under round-robin and passes on a per-request seed.) |
| S0.8 | **CSRF**: cross-origin `POST /api/projects/{name}` with a valid session cookie but no token | Rejected. Repeat for `DELETE /{name}?cascade=true`. |
| S0.9 | **Brute force**: N failed logins from one source | Throttled after the threshold; a valid login still succeeds from an unaffected source. |
| S0.10 | Session-bound active project (0b): user A and user B on one process, different active projects | `GET /api/network/buses` returns each user's own network. |
| S0.11 | `PRAGMA foreign_keys` | Deleting a parent row nulls `parent_project_id` on **both** Postgres and SQLite. |

## Step 1 — DB owns identity; storage backend is pluggable

**Corrected framing.** In auth mode `list_projects()` **already returns DB rows
only** — the `iterdir()` loop at `projects.py:649` is unreachable because the
auth branch returns at `:645`. It is the *single-user fallback*, so deleting it
is near-free and is really part of removing single-user mode. The real Step 1
work is elsewhere:

- **Opaque keys + a storage interface.** `services/storage_backend.py`:
  `put/get/delete/exists/open_local(key)`. `LocalDiskBackend` now,
  `S3Backend` in Step 4.
- **Existing rows carry absolute host paths.** `project_registry.py:127` stores
  `str(storage_path_for(...))` — a machine-specific path under the source tree
  (`settings.py:23`). The migration must rewrite every existing row.
- **Locality decision (must be made, not deferred).** `open_local()` on S3 is a
  download per load — the same temp-file cost we rejected DB blobs for. Choose
  and record: (a) shared filesystem (EFS/Filestore), or (b) S3 + a local
  read-through cache with explicit invalidation. This decision constrains Step 3.
- **Replace the crash-safety that the seam drops — OWNED HERE, not in Step 2.**
  `_atomic_write_with` + `os.replace` (`projects.py:229-236`) and
  `_netcdf_io_lock` (`pypsa_service.py:78`) have no S3 equivalent. Specify
  versioned keys + write-then-swap-pointer, or keep (a). Step 2 owns only the
  *cross-host mutation lock*; the storage-level write discipline is Step 1's.
- `tools/reconcile_storage.py` — drift both ways, `--dry-run` default.
- **Retire legacy-flat *readers* before `/unclaimed`.** `deps.py:50`,
  `snapshots.py:383`, `compare.py:70` still read flat dirs; removing the only
  adopter first would strand them. (Largely done by Step 0's Problem 3.)

**Honesty on "no second migration":** flat → `projects/{org}/{project}` already
happened; `legacy_unclaimed` (`settings.py:24`) is a third layout; opaque keys
are a fourth. The constraint is therefore **"no migration after this one"** —
achieved by making the key opaque so the *backend* can change without touching rows.

**Files:** `services/storage_backend.py` (new), `services/project_registry.py`,
`routers/projects.py`, `tools/{reconcile_storage,migrate_to_opaque_keys}.py`
(new), `alembic/versions/0002_*.py`.

### E2E QA — Step 1

| ID | Case | Assert |
|---|---|---|
| S1.1 | Storage key exists with no DB row **and** single-user mode removed | Absent from `GET /projects/`. (v1's version passed against today's code — it only proved the auth branch returns first.) |
| S1.2 | Delete blobs behind the app's back, then open | Exact status (409) **and** a machine-readable `detail.error_kind`, matching the structured-detail style at `projects.py:1744`. Not just "an actionable message". |
| S1.3 | `reconcile --dry-run` on S1.1+S1.2 state | Exactly one orphan-blob, one orphan-row; non-zero exit; nothing mutated. |
| S1.4 | `--fix`, then re-run | Zero drift, exit 0. Idempotent. |
| S1.5 | Rename an auth-mode project | Blob key unchanged; the resident registry re-keys with **no stale entry** under the old key. (v2 targeted the legacy flat path — code scheduled for deletion in Step 0a.) |
| S1.6a | `DELETE` a parent with children, no cascade | 409 listing descendants (`projects.py:2181-2196`). |
| S1.6b | `DELETE ?cascade=true` | Children **and** their blobs gone. |
| S1.6c | DB-level: delete a parent row directly | `parent_project_id` → NULL on **Postgres and SQLite**. The pragma is added in Step 0a, so this is a plain assertion — v2 wrote "add the pragma **or** drop the claim", which both outcomes satisfy. |
| S1.7 | Kill between INSERT and blob write | Row with no blobs; reconcile reports it. |
| S1.8 | **Bundle round-trip** (every step) | Identical component tables, snapshot count, objective. |
| S1.9 | Migration on a copy of real data (11 projects, 113 MB) | All load via PyPSA; **PyPSA-level equality**, not byte-identical checksums — netCDF4/HDF5 embeds creation timestamps, so a byte compare is either trivially true or spuriously false. |
| S1.10 | Zip-slip regression (`upload_guard.safe_extract`) after the storage change | Malicious `../` entry still rejected. |
| S1.11 | Restricted unpickler (`projects.py:346-352`) with `results_state.pkl` in the new backend | Disallowed global still refused, degrading gracefully. |
| S1.12 | **Root portability (N).** Migrate, then point `projects_root` (`settings.py:23`) at a different absolute path | All 11 projects still resolve and load. Existing rows store absolute host paths (`project_registry.py:127`) — this is the actual container failure mode, and S1.9 does not cover it. |
| S1.13 | **Torn write.** Kill the process *during* a blob write | Previous version still loads. `os.replace` guarantees this today; the replacement must too. S1.7 covers a crash *between* INSERT and write — a different fault. |

---

## Step 2 — Solving moves to a worker queue

**Problem.** `solver_service` runs `threading.Thread` inside uvicorn and holds
the PyPSA lock for the whole solve. Autoscaling kills the pod mid-solve; a long
solve pins a web worker.

**Corrections to v1.**
- The log object is **`BufferedLogQueue`** (`simulation.py:22-120`), not
  `queue.SimpleQueue` (the type annotation at `solver_service.py:640` is
  misleading). It is a **5000-line replay ring with a fanout subscriber API**,
  consumed by `chat_service.py:1074` and by `/log_history` (`simulation.py:814`).
  A Redis channel must replicate **replay + fanout**, not just delivery.
- `/log_stream` takes **no `job_id`** and binds to the *active project's* queue
  (`simulation.py:851`); the frontend hardcodes a param-less `EventSource`
  (`frontend/src/api/simulation.ts:502`, reconnect in `AppHeader.tsx:622`).
  Per-job channels therefore **require frontend work** — budget it explicitly.
- Job ids are `itertools.count(1)` per process (`solve_queue.py:109`) — two
  replicas both issue id 1. Ids must become uuids or DB sequences.
- **`_evict_if_over_cap` protects in-flight contexts by probing the in-process
  `solve_queue` singleton** (`pypsa_service.py:398-407`). Step 2 deletes that
  singleton, so **Step 2 must ship a replacement protection signal** (job state
  in the DB) or Step 3's cache can evict-and-save a project a remote worker is
  mid-solve on. This is the rework v1 claimed to avoid.
- **`get_lock()` and `_netcdf_io_lock` become meaningless across hosts.** They
  are process mutexes guarding PyPSA mutation and HDF5 global state. Step 2 must
  define the replacement: a DB advisory mutation lock per project, distinct from
  the TTL'd *user* edit lock (`db/models.py:71`), plus single-writer-per-key
  discipline in the storage backend.
- **User-code `exec()` is process-global and bundle-delivered.**
  `solver_service.py:1392-1422` runs `extra_functionality_code`, which lives in
  `solver_config.json` inside `_BUNDLE_FILES` (`projects.py:60`). Enabling it for
  one tenant gives every tenant RCE in the worker, via an imported zip. The
  worker must sandbox it or the feature must be per-tenant-off by default.
- `tempfile.mktemp()` at `solver_service.py:664` — deprecated, symlink-race, and
  a tail thread streams whatever is at that path into the user's SSE. Replace.

**Files:** `services/solver_service.py`, `services/job_queue.py` (new),
`workers/solve_worker.py` (new), `routers/simulation.py`,
`frontend/src/api/simulation.ts`, `frontend/src/layout/AppHeader.tsx`.

### E2E QA — Step 2

| ID | Case | Assert |
|---|---|---|
| S2.1 | SSE client connects to a replica that is **not** running the worker (verify via `X-PyPSA-Replica`) | Lines arrive **and** `done` fires. (v1's version passed today — curl is already a different process.) |
| S2.2 | Kill the worker mid-solve | Terminal state within **60 s** (pin the bound; "within the heartbeat window" is not assertable). Never stuck `running`. |
| S2.3 | Restart the **web** process mid-solve | Solve completes; reconnecting client gets `done`. |
| S2.4a | SSE contract, client stays connected through the sentinel | `[PHASE]` markers in lifecycle order; forced failure emits `[PHASE] Failed:` **and** `TRACEBACK:`; `done` fires **exactly once**. |
| S2.4b | Client disconnects before the sentinel (`simulation.py:879-883`) | `done` fires **zero** times and the generator returns. (v2's single "at most once" was satisfied by a totally broken `done` — the same defect class as v1.) |
| S2.4c | Reconnect mid-solve | `/log_history` (`simulation.py:814`) replays the ring; the chat fanout subscriber (`chat_service.py:1074`) still receives. |
| S2.5 | Two solves on **different replicas** writing the same project | Serialised by the new mutation lock; no lost update. (FIFO on one queue is today's behaviour and proves nothing.) |
| S2.6 | Abort queued, then abort running | Terminal; no orphaned worker. |
| S2.7 | Solve exceeding timeout | Terminated, `failed` with a timeout reason. |
| S2.8 | Import a bundle whose `solver_config.json` carries `extra_functionality_code`; solve | Code does **not** execute unless explicitly enabled for that tenant. |
| S2.9 | Two concurrent solves, distinct tenants, one worker host | Job ids unique; neither stream contains the other's lines. |

---

## Step 3 — Stateless web tier

**Corrected framing — the registry is LIVE, not dormant.** v1's central claim
was wrong. `services/pypsa_service.py:29-33` carries a stale "DORMANT until
B6/B8" comment, but B6/B8/B9 have landed: `RESIDENT_CAP=5` (`:51`), full LRU
eviction (`:359-443`), `deps.py:54` resolving contexts, live path-scoped routes
in `project_network.py:50`, `/activate` registering at `projects.py:1754-1765`,
and tests in `test_eviction.py` / `test_activate.py`.

So Step 3 is a **partial teardown, not a wiring job**:

- Eviction is **write-back** — it *saves victims to disk* (`:440-443`,
  `_save_evicted_ctx:466-484`). A read cache must not write. Remove.
- The solve queue **mutates non-active contexts in place**
  (`solve_queue.py:324-331`). Step 2 removes this; verify nothing else relies on it.
- Undo/chat/solver state hang off each context (`project_context.py:114-162`)
  and must move to durable storage before contexts become disposable.

**Remaining per-process state:**

| Symbol | File | Note |
|---|---|---|
| `PyPSAService._contexts` / `_active` | `pypsa_service.py:34` | up to 5 resident networks, **not** one |
| `change_log_service._entries` | `change_log_service.py:20` | → DB (tenanted in Step 0) |
| `_user_ts` | `routers/network.py:1987` | module global **shared across resident projects** — `solve_queue.py:406-411` already documents the corruption. Must become **per-project** *and* durable. |
| chat `_SESSIONS`, `_RATE_BUCKETS`, `_METRICS` | `chat_service.py:647,648,216` | session on replica 1 unknown to replica 2 → `POST /chat/{sid}/confirm` 404s and the agent hangs (the `confirmation_token` hang in CLAUDE.md). Rate limit becomes N×; the cost meter undercounts by N. |

`_state` is **not** module-level — `simulation.py:230-288` is an
`_ActiveStateProxy` forwarding to the active `ProjectContext.solver_state`. Its
`RESULT_STATE_KEYS` are deep-copied result DataFrames persisted to
`results_state.pkl` (`project_context.py:179-185`), not job metadata.

**The invalidation contract (v1's biggest hole).** Mutations in workers + a read
cache in the web tier means the web tier can serve a network a worker has since
replaced. Required: a monotonic **version/etag per storage key**, checked on
every cache hit, plus a pub/sub invalidate on write. Untested, this is the
likeliest source of "solve finished, UI shows old numbers".

**The solver-in-flight middleware is a process-wide write gate.**
`main.py:201-222` refuses all `/api/network/*`, `/api/io/*`, `/api/projects/*`
writes when the **active** context has a live worker — so one tenant's solve
409s every other tenant's edits on that pod. Must become per-project.

### E2E QA — Step 3

Cases marked ⇄ assert `X-PyPSA-Replica` **differs** (request crossed replicas);
cases marked ⇉ assert it is **identical** (deliberately same pod). Without one of
those assertions a sticky proxy makes the whole section pass while state is still
process-local.

| ID | Case | Assert |
|---|---|---|
| S3.1 ⇄ | Edit via replica 1, read via replica 2 | Replica 2 returns the committed edit. |
| S3.2 ⇉ | Org A and org B each with a project named `Baseline`, forced onto the **same** replica | Neither sees the other's components (Step 0a regression). |
| S3.3 ⇄ | Audit log after edits across both replicas | Union, in id order, from either replica; scoped to the reading org. Requires a **DB sequence** — `change_log_service.py:27` is a per-process `itertools.count(1)`, so cross-replica ordering is otherwise unimplementable. |
| S3.4 ⇄ | Upload time-series on replica 1; solve on a worker; read on replica 2 | Applied in the solve and visible from replica 2. |
| S3.5 | Rolling restart mid-edit | Session survives (no re-login) **and** the named contract holds: *edits since the last save are lost, and the client is told via a `stale_session` event that triggers a reload.* Pick this contract or another — but v2's "the outcome is asserted" named none, so any behaviour passed. |
| S3.6 | Warm the cache; have a worker mutate the project; read again | Read reflects the worker's write. Assert a cache-miss/invalidation counter incremented — "no 500s" is satisfied by a cache that never evicts *or* one serving stale data. |
| S3.7 | Rename then delete a component across replicas | `_user_ts` + vintage-bounds cascade still fire (three-stores-in-sync rule). |
| S3.8 ⇄ | Chat: start a session on replica 1, confirm a destructive tool via replica 2 | Confirmation resolves; the agent does not hang. |
| S3.9 ⇉ | Tenant A solving; tenant B edits a different project on the **same** pod | B's write succeeds (per-project gate, not the process-wide gate at `main.py:201-222`). |

---

## Step 4 — Operational layer

- **Postgres is already the default** (`settings.py:12`); SQLite is the opt-in
  local fallback (`db/session.py:15`). Smaller than v1 claimed — but SQLite must
  be *forbidden* once >1 replica.
- **Fail closed on secrets.** `settings.py:13` defaults `secret_key` to
  `"dev-only-change-me"` and boots silently. Startup must **refuse** without an
  explicit key. It must also be stable across replicas/deploys or every rollout
  logs everyone out — so this lands **before** Steps 2/3 multiply processes.
- **CORS — moved to Step 0a**, where it belongs with CSRF (one trust boundary).
  Not repeated here; see 0a.
- **S3 backend** — second implementation of the Step 1 interface.
- **Quotas**: solve timeout, memory cap, concurrent solves, storage per org.
  Existing upload caps are per-directory (`_current_usage_bytes`), not per-org.
  Add streaming for the 512 MB upload buffer and the `BytesIO` bundle build
  (`projects.py:2786-2809`) — concurrent tenants on one pod will OOM.
- **Chat metering** per org (Step 3 makes `_METRICS` durable first).
- **Observability**: structured logs with request/job/tenant ids, metrics,
  tracing, health/readiness split.
- **Server-side versioning / soft-delete.** `POST /api/projects/{name}` is a
  destructive save; CLAUDE.md names OneDrive version history as the recovery
  path, which does not exist in the cloud.

### E2E QA — Step 4

| ID | Case | Assert |
|---|---|---|
| S4.1 | Rolling deploy | Sessions survive; no forced re-login. |
| S4.2 | Full suite against `S3Backend` | Steps 0–3 cases pass unchanged — proves the interface held. Only meaningful because the vacuous v1 cases were replaced. |
| S4.3 | Org over storage quota | Clear 4xx; existing projects stay readable. |
| S4.4 | Org over concurrent-solve limit | Queued, not rejected; other orgs unaffected. |
| S4.5 | Two orgs chat | Spend attributed per org; matches the meter. |
| S4.6 | Startup with no `SECRET_KEY` | Process **refuses to start**. |
| S4.7 | Cross-origin credentialed request from a non-allowlisted origin | Blocked. |
| S4.8 | Destructive save, then restore | Prior version recoverable. |
| S4.9 | **Bounded memory (J).** Concurrent large uploads + concurrent bundle downloads | Process RSS stays under the configured ceiling. The 512 MB upload buffer and the `BytesIO` bundle build (`projects.py:2786-2809`) must stream; S4.3 is a *storage* quota and does not cover this. |

---

## Cross-cutting QA

End of **every** step: full pytest, vitest (123), `tsc --noEmit`, production
build, `smoke/qa_e2e.py`, and the bundle round-trip (S1.8).

**The 1117 baseline does not survive Step 0a.** `tests/conftest.py:40` pins
`PYPSA_GUI_AUTH_ENABLED="false"`, so the entire current suite runs in the mode
being deleted. Expect a substantial rewrite and **re-pin a new baseline after
0a** — treating 1117 as invariant would either block the step or hide real
regressions behind an expected-failure count.

**Two harness limits to fix before relying on this suite:**

1. `qa_e2e.py` hardcodes `BACKEND=http://127.0.0.1:8000` /
   `FRONTEND=http://127.0.0.1:5173` (`smoke/qa_e2e.py:23-24`) and `suite_S6`
   shells out to a local frontend build. **It cannot exercise a deployed
   multi-replica target.** Parameterise the base URLs.
2. Its service checks assert on **HTTP 200 alone** — a proxy or auth wall
   satisfies them while the app is down. Assert on response content.
3. `POST /api/network/reset` between batteries resets **one replica**;
   `run_chat_smoke.py` and `tests/conftest.py::_reset_backend_state` assume one
   process. All need a fleet-wide reset or per-test tenancy isolation.

---

## Sequencing

| Step | Depends on | Risk | Note |
|---|---|---|---|
| **0a** — authz (14 routes), registry re-key, CSRF + CORS allowlist, login rate limit, 403→404 sweep, flag removal | — | Med | **Blocks hosting at all.** Not optional, not last. CSRF without the CORS fix is bypassable. |
| **0b — session-bound active project** | 0a | Med | Unblocks the ~110 routes with no project id. Precondition for Step 3, not a duplicate of it. |
| 4a — fail-closed secrets, Postgres-only | 0a | Low | Must precede 2/3 (multi-process). CORS moved to 0a. |
| 1 — DB identity + storage seam | 0 | Med | Data migration; locality decision |
| 2 — worker queue | 1, 4a | **High** | New infra; frozen SSE contract; must ship the eviction-protection and mutation-lock replacements |
| 3 — stateless web tier | 2 | High | 133 `get_network()` call sites; partial teardown of a live registry; invalidation contract |
| 4b — S3, quotas, metering, observability | 1–3 | Med | Mostly additive |

**Biggest risk:** Step 2 — a change to how the product executes work, with new
infrastructure and a contract the frontend parses.
**Second:** Step 3's blast radius plus the cache-invalidation hole.
**Most urgent:** Step 0 — everything else is premature while any authenticated
user can read any org's data.

---

## Appendix A — what v1 got wrong

Recorded so it is not re-derived. All verified against source.

1. **"The registry is dormant."** False. The comment at `pypsa_service.py:29-33`
   is stale; B6/B8/B9 landed. It is live, LRU-capped at 5, and **write-back**.
   This inverted Step 3 from "wiring" to "teardown".
2. **"`list_projects` has two live definitions."** No — the auth branch returns
   at `:645`; `iterdir()` is the single-user fallback only.
3. **"`_active` is *the* active network."** Up to `RESIDENT_CAP` are resident.
4. **"`_state` is module-level."** It is `_ActiveStateProxy` → per-context state.
5. **"`queue.SimpleQueue`."** It is `BufferedLogQueue` — replay ring + fanout.
6. **"A FK cannot dangle."** True only where enforced: there is **no**
   `PRAGMA foreign_keys=ON`, and the API refuses the delete anyway (409/cascade),
   so `SET NULL` never fires in practice.
7. **"Postgres replaces SQLite."** Postgres is already the default.
8. **Counts:** 133 `get_network()` call sites backend-wide (not ~110). The qa-id
   count is **43**, as v1 said — review round 1's "41" came from a regex that
   truncated sub-ids like `S1.1a` to `S1.1`. Recorded because the reviewer's
   correction was itself wrong; re-measure rather than trusting either.
9. **Security scheduled as one QA line in the final phase** — it is the actual
   blocker and is now Step 0.
10. **Several E2E cases passed against unmodified code** (S1.1, S1.5, S1.6,
    S2.1, S2.5) and every multi-replica case was unfalsifiable without a
    replica-id header.

### What v2 then got wrong (round 2)

11. **Step 0 was mis-scoped.** `require_project_access(project_id)` cannot bind
    to the ~110 routes that name no project (`results.py` 28 routes,
    `simulation.py` 14, `io.py` 8, `network.py` 55 of 79, …). v3 splits 0a/0b
    and moves the active-project pointer into the **session**.
12. **"Scope the solve log to the solver's own logger" was a new error.** The
    root handler is deliberately capturing `pypsa`/`linopy`/HiGHS output — that
    output *is* the log. Use the thread-ident filter the codebase already has at
    `solver_service.py:614-622`. S0.6 now guards against this regression.
13. **CSRF was missed by v1, v2, and review round 1.** Production cookies are
    `SameSite=None; Secure` (`auth.py:_cookie_flags`) with credentialed CORS and
    zero tokens anywhere.
14. **No login rate limiting.**
15. `_netcdf_io_lock` was owned by two steps; now Step 1 only.
16. "13 of 18 routers" — there are **16** router modules.
17. **`results.py` uses `results_router`, not `router`** — a naive grep reports
    zero routes where there are 28.
18. `PYPSA_GUI_AUTH_ENABLED` removal had no owning step; now Step 0a, with an
    explicit warning that the 1117-test baseline will not survive it.
