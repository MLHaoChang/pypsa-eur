# Open items

**Verified against `83bde2c` (master) on 2026-09-28.** Every entry was reproduced
or re-read in source; nothing here is carried forward on trust. Entries fixed by
PR #18 have been removed rather than annotated — each finding file in `findings/`
carries its own status line, so a file not named here is closed.

This file exists because GitHub Issues is **disabled** on this repository, so
there is nowhere else to keep a queue. It is deliberately thin: one entry per
open item with its anchor and its source. **If Issues is ever enabled, move these
there and delete this file.**

Items 1–9 came from two independent QA reviews on 2026-09-12 (chat/tool dispatch,
and authorization/tenancy) plus the per-route authorization audit; items 4–7 are
fixed on a branch and are listed near the bottom, not deleted, because on master
they are still live. Item 16 is the residual item 4's fix deliberately left open. The
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

### 16. The chat tool pool is shared, and a hung handler's thread is gone for good

`services/chat_service.py` — `TOOL_EXECUTOR_MAX_WORKERS` is 8 for the whole
process. Item 4 made a saturated pool fail honestly and stopped it running work
behind the user's back, but it did not make sessions independent: a Python thread
cannot be killed, so each hung handler costs the deployment one of eight workers
permanently, and eight of them anywhere refuse tool calls everywhere. The fix is
isolation, not a bigger number — a pool per session, or a per-session in-flight
bound over a larger shared pool, either of which needs a decision about lifecycle
(`services/shutdown.py:424` shuts the single executor down as step 7 and takes it
by injection, so a per-session pool has to be reachable from there too). Severity
is Medium and not higher because the failure is now loud and no longer corrupts:
the refusal is accurate and the work does not run later.

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

## Fixed on `claude/gui-backend-qa-followups` — NOT yet merged to master

Kept here, with their original numbers, rather than deleted: this file is verified
against master, and on master these are still live. Delete each entry when the
branch merges.

### 4. A shared tool executor lets one session time out another's tools — `05cfdcd` (RED) + `ca091ff`

`future.result(timeout=...)` measured queue + run and abandoned the future without
cancelling it, so a starved tool was reported as having exceeded a deadline it
never reached and then RAN once a slot freed. Now: the worker start gets its own
grace period, the execution deadline is measured from the start event, and a tool
that never started is cancelled and reported `tool_not_started`.
**Residual, deliberately not fixed here — see item 16.**

### 5. The `update_solver_config` chat tool hits the new gate with `Depends` sentinels — `168384b` (RED) + `f089be8`

Routed through `_route`, and `_route` now resolves the name `actor` as well as
`user` (the same value under the spelling `admin.py`/`chat.py`/`simulation.py`
use), so its `RuntimeError` stays reserved for dependencies that genuinely cannot
be satisfied.

### 6. An admin importing a hostile bundle still gets silent code execution — `7d37d60`

The bundle strip is now unconditional. The privileged-importer check asked "is the
importer privileged" where the question is "did the importer author this code",
which a zip cannot answer. Demonstrated with the old guard restored: a colleague's
module body writes its sentinel file inside the admin's session.

### 7. A fifth `is_error` site is still unfenced — `5a84784` (RED) + `a8eb52b`

The pre-dispatch validator's output now goes through `_error_result_content`.

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
- **The local gate is "the failing set is unchanged", not "green"**: the suite has
  pre-existing failures (three on 3.12 — listed below). Extract the set with
  `grep -E '^(FAILED|ERROR) tests/'` — anchoring on `^ERROR` alone also matches log
  lines and silently over-reports. Whether the set matches CI's is a separate
  question: a local venv built from `gui-requirements.txt` imports `gridspine`
  fine, and CI has been seen without it, so a `No module named 'gridspine'`
  failure in a CI log is an environment difference, not a regression.
- `dev-env` has been red since `14eae4d`.

### Running the backend suite locally

The project floor is **Python ≥3.12** (`pyproject.toml:31`; pixi pins 3.12.12) and
master uses PEP 701 f-strings, so a 3.11 interpreter cannot even import the
backend — a failure that looks exactly like a syntax regression and is not one.

    python3.12 -m venv /tmp/venv312
    /tmp/venv312/bin/pip install -r pypsa-gui/gui-requirements.txt
    /tmp/venv312/bin/pip install pytest openpyxl httpx
    cd pypsa-gui/backend
    /tmp/venv312/bin/python -m pytest tests/ -p no:cacheprovider -q \
        --no-header -W ignore::DeprecationWarning

Note the requirements file is `pypsa-gui/gui-requirements.txt`, NOT
`pypsa-gui/backend/`.

**The 3.12 baseline on `83bde2c` is three failures**, verified by running the full
suite against a clean master worktree with the same interpreter and diffing the
extracted sets — identical to the branch's:

    tests/test_chat_uploads.py::TestUploadsEndpoints::test_post_renamed_exe_as_xlsx_still_rejected
    tests/test_solver_facade_surface.py::test_no_call_site_was_left_behind_by_a_move
    tests/test_sqlite_pragmas.py::test_non_sqlite_engine_is_returned_untouched

Do the master comparison in a separate worktree rather than by stashing, and do
not run two full suites at once — they contend and the timing-sensitive tests get
flaky.
