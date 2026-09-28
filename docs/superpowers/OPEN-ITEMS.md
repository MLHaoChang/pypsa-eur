# Open items

**Verified against `83bde2c` (master) on 2026-09-28.** Every entry was reproduced
or re-read in source; nothing here is carried forward on trust. Entries fixed by
PR #18 have been removed rather than annotated — each finding file in `findings/`
carries its own status line, so a file not named here is closed.

This file exists because GitHub Issues is **disabled** on this repository, so
there is nowhere else to keep a queue. It is deliberately thin: one entry per
open item with its anchor and its source. **If Issues is ever enabled, move these
there and delete this file.**

Items 1–9 below came from two independent QA reviews on 2026-09-12 (chat/tool
dispatch, and authorization/tenancy) plus the per-route authorization audit. The
reviewers' reproduction probes were written to `/tmp/claude-0/qa1..qa3/` and are
**not durable** — each entry therefore states the reproduction in words. They run
as pytest files from outside the tree with
`--rootdir=pypsa-gui/backend -p tests.conftest`, which makes the in-tree fixtures
(`client`, `other_org_client`, `api_project`, `install_network`) available without
writing anything into the repo.

---

## Critical

### 1. The user-timeseries store is a process global shared across tenants

`services/user_timeseries.py:39` — `_user_ts` is keyed `(component, attribute,
column)` with no org, project or session. It is authoritative, not a cache: the
timeseries GET prefers it over the network's own data, and every foreground save
serialises it into that project's `user_ts.json` and reapplies it onto the network
before the netCDF export.

Reproduced: org A uploads a profile for `L1`; org B activates its OWN project,
reads A's values, and on save writes them into B's storage, from where they reach
B's `network.nc` and solve results. Cross-tenant read AND write. The desktop build
is affected too, as a multi-project data-integrity bug.

The code mitigated only the BACKGROUND case, on the stated basis that the store
"belongs to the FOREGROUND ctx" — true for one desktop user, false once one
process serves many signed-in sessions, where every session is a foreground.

**Not patched deliberately.** ~230 references across 13 modules;
per-`ProjectContext` isolation is the real fix and needs its own plan. A narrow
containment exists (restore-or-clear on activate, as `load_project` already does)
but closes only the demonstrated path, not the class — two concurrent sessions on
different projects still share one dict. Full analysis, reproduction and fix
criteria: `findings/2026-09-12-user-ts-is-a-process-global-shared-across-tenants.md`.

---

## High

### 2. `GET /api/chat/history` clears a LIVE session's deque mid-turn

`routers/chat.py` — the rebuild block does `sess.messages.clear()` and replays
`chat.jsonl` **unconditionally**. The comment above it reasons explicitly about a
`/history` racing a turn ("two tabs sharing a session_id, or a reload") and guards
only the profile fields behind `session_was_freshly_minted`; `_turn_in_flight` is
never consulted, even though `rewind_session` refuses for exactly this reason.

Reproduced: with a turn in flight and an assistant `tool_use` already persisted,
`/history` clears and rebuilds, then the turn appends its `tool_result` batch —
leaving a `tool_result` that follows no `tool_use`. The session's next turn is a
hard provider-side `invalid_request_error` and only "New chat" recovers. `/history`
also returns that same `last_session_id`, i.e. it hands the reloading panel the
session it just poisoned. RAM only; the durable transcript is fine, which is why
no test catches it.

Same class as the `tool_call_cap_exceeded` pairing bug fixed in PR #18 — the
pairing-invariant checker in `tests/test_chat_tool_pairing_invariant.py` is the
tool to reuse.

### 3. `/abort` does not stop a turn, and a rejected `/stream` erases the abort

Three defects that compound:

**(a)** The dispatch loop checks only `project_switched()`, never the abort event,
so with an abort already set, remaining `write`-tier tools in the same assistant
step all execute. The client keeps draining the stream after Stop (`onAbort` posts
`/abort`, sets `streaming=false`, does not close the stream), so the whole step
lands. `_DisconnectWatcher`'s own docstring says this must not happen.

**(b)** `session.abort_event.clear()` runs BEFORE the `_turn_in_flight` guard, so a
rejected second `/stream` silently discards the abort. Since the composer
re-enables immediately, "Stop, then retype" is the expected user sequence.

**(c)** Net effect: the cancelled turn keeps executing with its
`tool_pending_confirmation` card still on screen and still live for the full TTL —
clicking Approve executes a destructive tool **after** the user cancelled.

Fix is ordering plus a poll: move the clear inside the `_turn_in_flight` claim, and
check the abort event at the top of the per-tool loop, pairing an `is_error` result
per skipped tool as the project-switch branch already does.

---

## Medium

### 4. A shared tool executor lets one session time out another's tools

`services/chat_service.py` — a single module-level `ThreadPoolExecutor(max_workers=8)`
for all sessions, and `future.result(timeout=PER_TOOL_TIMEOUT_SECONDS)` measures
queue + run, not run. Reproduced: 8 hung handlers occupy the pool, then a trivial
read tool in a DIFFERENT session reports `tool_timeout` having never started — and
runs later, after the user was told it timed out. For an approved destructive write
that means the mutation lands long after the refusal was reported.

### 5. The `update_solver_config` chat tool hits the new gate with `Depends` sentinels

`services/chat_tools.py` calls `routers.simulation.update_solver_config(body)`
directly, bypassing `_route`, so the `db`/`actor` parameters PR #18 added arrive as
`fastapi.params.Depends` objects and the call dies with `AttributeError` inside
`is_org_admin`. It fails **closed** (nothing is stored), but the model gets an
opaque error instead of the authored `user_code_forbidden` 403.

Note the fix needs two changes: `_route` injects by parameter NAME (`db`/`user`/
`session`) and the new parameter is named `actor`, so routing it through `_route`
as-is trips that helper's deliberate `RuntimeError`.

### 6. An admin importing a hostile bundle still gets silent code execution

`routers/projects.py` — PR #18's strip is conditional on
`not user_code_authorized(db, user)`, so a bundle's `extra_functionality_code`
survives when the **importer** is an admin. With the operator flag on, an admin who
imports a `.pypsaproj.zip` a colleague sent them gets arbitrary Python executed on
the next solve, and `_compile_extra_functionality` `exec`s the module body, so side
effects fire at compile time. The check asks "is the importer privileged"; the
security question is "did the importer author this code". The repo already reaches
the other conclusion for the sibling field in the same bundle — `results_state.pkl`
goes through a restricted unpickler at every read site regardless of who imported
it.

### 7. A fifth `is_error` site is still unfenced

`services/chat_service.py` appends `{"is_error": True, "content": problem}` where
`problem` is the pre-dispatch validator output, and those validators interpolate the
caller-supplied component name with `!r`, which does not escape the delimiter. The
laundering path is real: the model reads a hostile name out of a (now-fenced) tool
result and passes it to `delete_component`, and the validator re-emits it unfenced.
`_result_to_anthropic_content`'s docstring counts "four is_error sites"; there are
five reachable ones.

### 8. `ProjectAccessDep` is adopted by 6 of 23 routers

`routers/deps.py` defines the right primitive — a per-route dependency that resolves
the project named by the request and checks the caller's access — used by `compare`,
`adequacy_worksheet`, `uploads`, `snapshots`, `gridspine` and `deps`. NOT used by the
five routers carrying most of the surface: `network` (81 routes), `results` (48),
`chat` (20), `simulation` (14), `io` (8). Those rely on the path-prefix middleware,
which denies by omission: a new router under a new prefix is ungated by default,
silently. This is the structural cause of several PR #18 fixes. Worth a test that
fails when a route is mounted under a prefix no mechanism covers.

### 9. Node positions revert on the blank canvas

User-reported 2026-07-31; diagnosed, never fixed. `PUT /layout` 404s until the
project directory exists, and on load the server wins unconditionally —
`TopologyCanvas.tsx` resolves `ps ?? layoutMemCache ?? loadDiagramState ?? null`.
`persistLayoutFor`'s `.catch()` falls back to localStorage, which is THIRD in that
chain, so a failed PUT leaves a newer local layout the next load discards for an
older `layout.json`. Both ends are silent, and `PersistedState` carries `savedAt`
which nothing compares. The user's exact sequence was never reproduced, so the
trigger for the failing PUT is still unidentified.
Source: `findings/2026-07-31-blank-canvas-node-drags-revert.md`.

### 10. Component names are not validated at the edge

Names flow from request bodies into the network, into filenames and into
model-facing text with no character-class check. Shared root cause behind the
untrusted-fence bypass and the `Content-Disposition` defect, both of which were
treated at the sink. `upload_service`'s `_FILE_ID_RE` shows the right pattern,
anchored and narrow, and it is not applied to names generally.

---

## Low

### 11. A refused `/stream` request still switches the session model

`routers/chat.py` sets `session.model` (and `profile_id`, `bound_wire`) before
`run_turn` reaches the `_turn_in_flight` guard that refuses the request, so a caller
who cannot get a turn can still change which model the next turn uses. Two fix
options in the finding; it is a design choice about where the guard belongs.
Source: `findings/2026-09-10-a-refused-stream-request-still-switches-the-session-model.md`.

### 12. The abort path leaks a pending confirmation, and `/confirm` then lies

`wait_for_decision`'s abort branch records `"aborted"` without popping
`pending_confirmations` / `_decision_events` / the decision. So `POST /confirm` on an
aborted card answers `200 {"ok":true,"decision":"approve"}` for a destructive tool
that nothing executes — an unresolvable rendered as success — and the leak grows per
aborted card for the life of the session.

### 13. Cookie policy is hardcoded to a preview vendor's hostname

`routers/auth.py::_cookie_flags` returns `SameSite=None; Secure` for any host
matching `.cursorusercontent.com`. Defensible for a preview environment,
indefensible as a permanent rule compiled into the product — the deployment should
decide its own cookie policy through configuration. The CSRF double-submit check
carries the load for those sessions today.

### 14. The `/api/results/mc` gating rationale is false

`main.py` and `tests/test_results_study_foreign_lock.py` both justify the
`/api/results/` prefix as "each take `PyPSAService.get_network()` … and re-solve it",
but `routers/results.py` says of one of the five: "this engine SOLVES NOTHING and
never mutates the network." So a non-holder now loses a genuinely read-only analysis
with a 409 whose message reads "before network changes are accepted". A defensible
reason to gate it does exist — it writes the shared `_state["mc"]` study slot — it is
just not the reason given.

### 15. The attachment listing neutralises `filename` but not `mime`

`services/chat_service.py` interpolates `m['mime']` raw inside the fence. Safe only
because `_sniff_mime` enforces `ALLOWED_MIME_TYPES` — precisely the un-linked
coupling the neighbouring comment worries about for filenames, left unaddressed one
line away.

---

## Verification / CI — read before trusting a green check

- **Three jobs are path-filtered and read green while running nothing.**
  `Gridspine` ("Skip - no gridspine changes") and BOTH `Integration` jobs
  ("Skip - no source changes") report success with every meaningful step skipped.
  A green check mark does not mean those tests ran.
- **`GUI backend` is the only job that validates this backend, and a follow-up push
  cancels it.** The workflow uses `cancel-in-progress` and the job takes ~25 minutes.
  During PR #18, five consecutive heads got NO verdict because each push killed the
  previous run. Do not push while it is in flight.
  A `check_suite.completed` event is silent about this — its own text excludes
  cancelled suites, and several arrived for already-superseded heads.
- **The local gate is "the failing set is unchanged", not "green"**: this backend
  carries pre-existing `No module named 'gridspine'` failures. Extract the set with
  `grep -E '^(FAILED|ERROR) tests/'` — anchoring on `^ERROR` alone also matches log
  lines and silently over-reports.
- `dev-env` has been red since `14eae4d`.
