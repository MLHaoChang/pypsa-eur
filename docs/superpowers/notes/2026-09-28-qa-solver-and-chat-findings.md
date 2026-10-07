# QA end-to-end review — solver lifecycle and chat/LLM tooling

Date: 2026-09-28, against `master` at `9f83f37` plus the fixes on
`claude/solution-fmea-integration-0mx5lc`. These are the two areas the
2026-09-28 review had in scope and never got a reviewer; the eleven findings from
that pass are in `2026-09-28-qa-e2e-findings.md`.

Sixteen findings, from two independent reviewers working read-only and in parallel,
plus CH-9, found on 2026-10-05 while closing CH-2.

## How to read the status column

Reviewer output is a lead, not a fact. Each entry below is marked:

- **VERIFIED** — I read the source myself and confirmed the mechanism and the site.
- **REPORTED** — plausible and specific, but I have not yet confirmed it. Do not act
  on a REPORTED entry without verifying it first.

That distinction is not bureaucracy. In the earlier pass one of eleven reported
findings (QA-S1) had already been fixed months before, and another (QA-N2) had its
mechanism backwards. Both survived only because they were re-checked.

---

## Solver lifecycle

### SL-1 — a raise inside `_apply_modelling_assumptions` leaves the network mutated, unrevertable

**Serious. VERIFIED. FIXED.**

The undo walk moved out of the `restore()` closure to a module-level
`_run_undo_actions(n, undo_actions, phase)`, called from two places: the `restore`
closure the caller invokes after a solve, and a new revert-on-raise handler inside the
apply itself. One implementation, two callers — a second copy of a revert is the drift
that produced three other findings in this register.

The handler catches `BaseException`, not `Exception`: the abort path injects a
`KeyboardInterrupt` into the solving thread (`_AbortWatcher`), and an aborted solve
must leave the network as clean as a failed one. A failure inside the cleanup is
swallowed so it can never mask the original exception.

Also fixed, the `id(n)` half: `periodized_costs.revert` discarded its
`_FOM_SCALED_IDS` entry AFTER the column restores, so a raise in that loop leaked it.
Moved into a `finally`. The leak matters because the registry is keyed by address and
CPython reuses addresses — a leaked entry makes an unrelated later network read as
already-scaled, so `fom_per_horizon` leaves its `fom_cost` on the wrong basis with
nothing anywhere saying so.

Guard: `tests/test_assumptions_revert_on_raise.py`. A raising `phase` (an ordinary
parameter, so no monkeypatching) simulates a mid-apply failure; the injection point is
SWEPT rather than guessed, and a separate test asserts at least one index actually
raises — a parametrised test where every case skips reports green while testing
nothing. Two of the swept indices fail against the previous code.

Reviewing it: `git diff -w` is the useful view. Guarding a ~700-line body in place
re-indents all of it, so the raw diff is ~1300 lines; whitespace-ignored it is 122
insertions and 75 deletions, most of that the relocated restore body.

- `services/solver/assumptions.py::_apply_modelling_assumptions` builds `undo_actions`
  from its first step onward but defines and returns `restore()` only at the very end.
- `services/solver_service.py::run_simulation` binds `restore_modelling` only *after*
  that call returns, and every outer handler is guarded by
  `if restore_modelling is not None`.

So any exception between step 1 and the end of the apply leaves the live network
carrying the LP transforms — scaled `fom_cost`/`capital_cost`, CO2-inflated marginal
costs, scaled loads, possibly added VOLL/DSR slacks — with no handle to revert them.
A later autosave or eviction write-back persists that corruption into the project.

The comment at `run_simulation` is careful and accurate about the window it *does*
cover (between the apply returning and the solve try/finally). The gap is the window
*inside* the apply, which nothing covers.

Compounding: `services/solver/periodized_costs.py` tracks the FOM scaling in a
module-global `_FOM_SCALED_IDS` keyed by **`id(n)`**, added during the fill and
discarded only inside `revert`. A leaked entry is not merely stale — CPython reuses
addresses, so an unrelated future network allocated at the same address reads as
already-scaled and silently skips its own scaling. `fom_per_horizon` consults this,
so the wrong basis reaches FOM reporting.

Fix shape: wrap the body after step 1, run `reversed(undo_actions)` on the way out,
re-raise. Key the scaling registry by weak reference, or discard in a `finally`.

### SL-2 — a queued solve is saved beside a config that did not produce it

**Serious. VERIFIED. FIXED** — `_save_context` takes a `solver_config_override`,
which the queue passes. An override rather than writing the snapshot into the live
context: the user may have edited their config after enqueueing, and that edit is
theirs. Guard: `tests/test_queued_solve_saves_its_own_config.py`, including a test
that the live context is undisturbed.

`services/solve_queue.py::_run_solve_job` deliberately solves with the *enqueue-time*
config snapshot (`job.solver_config_json`) rather than the context's — the comment at
the top of the module explains that a `PUT /solver_config` after enqueue used to
change the solve silently. But it never writes that snapshot back into
`ctx.solver_state["solver_config"]`, and `routers/projects.py::_save_context` persists
`solver_config.json` from exactly that key.

So `network.nc` holds the snapshot-config solve while `solver_config.json` beside it
describes whatever the context happens to hold. The `/results/*` endpoints read the
context's config too. The original defect was not closed — it moved from the solve to
the record of the solve, which is worse, because the mismatch is now silent and
durable.

Fix shape: publish the chosen config into `ctx.solver_state["solver_config"]` under
the lock before solving, or pass it to `_save_context` explicitly.

### SL-3 — the queue claim does not clear the five `eh_*` result keys

**Serious. VERIFIED. FIXED** — both claims now splat `cleared_result_state()`, derived
from `RESULT_STATE_KEYS`. Guard: `tests/test_claim_clears_every_result_key.py`, which
also fails if either claim starts hand-listing a result key again.

`routers/simulation.py::run` resets `eh_redundancy_comparison`, `eh_lever_comparison`,
`eh_dtc_stress`, `eh_dtc_planning` and `eh_reference_design_report` on claim
(lines 830-834). The queue claim in `services/solve_queue.py::_run_solve_job` resets
`adequacy_report`, `last_reserve_margin`, `lopf_results`, the `ac_pf_*` family and
`last_lost_load` — and not those five.

All five are in `RESULT_STATE_KEYS`, so they are persisted to `results_state.pkl` and
re-hydrated. A queued re-solve therefore saves the *previous* plan's energy-hub tables
next to the new dispatch, and `/results/eh_*` serves them.

The comment sitting directly above that reset list states the invariant the five keys
break: results "must be cleared on a new claim like every other per-solve result, or a
queued run inherits the previous project's adequacy verdict". Same defect class as
QA-E1 in the other register — a comment asserting an invariant the code does not hold.

Fix shape: derive the claim reset from `RESULT_STATE_KEYS` in one shared place used by
both claims, instead of two hand-maintained lists.

### SL-4 — a failed or aborted job publishes the previous solve's objective

**Moderate. VERIFIED. FIXED** on BOTH paths — `/run` had it too, which the reviewer
did not claim and I found while fixing. Guard:
`tests/test_objective_only_on_success.py`, which first asserts the premise (the stale
number really does survive) rather than assuming it.

`_run_solve_job` computes `objective = sim._compute_run_objective(n, config)` *before*
branching on status, and publishes it unconditionally through `ctx_state_update` and
onto the persisted job row. `_compute_run_objective` reads `n.objective +
n.objective_constant`; PyPSA assigns `_objective` only on a successful solve and it
survives in the netCDF. So an infeasible or aborted run reports the last successful
run's cost, in the queue UI and in the saved row.

Fix shape: compute and publish it only when the final status is `completed`.

### SL-5 — a `BaseException` in a queue job leaves the context permanently owned

**Moderate (rare trigger, severe effect). VERIFIED and FIXED, both halves.**

`_dispatch_loop` catches BaseException and carries on — right for the dispatcher.
`_run_solve_job` caught only `Exception`, and its `finally` released the job row and
not the context, so `thread` stayed pointed at the dispatcher, which never exits. The
`finally` now releases the context whenever this run still owns it — a no-op on the
success and `Exception` paths, which already release it.

The restore guard set its once-flag BEFORE restoring, so the defensive second call in
the KeyboardInterrupt handler — which exists for exactly the interrupted-restore case
— found it set and did nothing. It is now marked after; re-running is safe because
SL-1 made the undo walk idempotent.

Guard: `tests/test_queue_releases_what_it_claims.py`, driving a real queued job whose
fake `run_simulation` raises a non-KeyboardInterrupt BaseException, plus a control that
the dispatcher survives and runs the next job. The restore-guard ordering is pinned
structurally, and says so: reaching it for real needs an interrupt mid-restore.

`_run_solve_job` catches `Exception`, not `BaseException`, and its `finally` updates
only the job row. A `SystemExit` from user `extra_functionality_code`, or a late
async injection from `_AbortWatcher`, would leave `ctx.solver_state` at
`status="running"` with `thread=` the immortal dispatcher thread. `_solver_in_flight_ctx`
would then be true forever and every save, activate and load of that project 409s
until a restart.

Also reported, same finding: `_guarded_restore` sets its once-flag *before* running
the real restore, so an interrupt mid-restore makes the defensive second call a no-op
and can leave the network half-restored.

### SL-6 — web-mode rename is not blocked by a queued or running job

**Moderate. VERIFIED, sharpened by this branch's QA-P2 fix. FIXED** — the guard moved
above the branch so both modes reach it; the rebind stays. Guard: two tests in
`tests/test_storage_layout.py`, one of them the control that an ordinary web rename is
still allowed.

`services/project_registry.py::rename_project` reaches `_queued_job_blocks_rename`
only on the directory-move branch. The row-only branch — which is the **web** branch,
i.e. the deployed one — returns before it.

This became sharper on this branch. QA-P2 added `_rebind_resident_contexts` to that
early return (correctly: without it every rename left the context on the old name and
the next save 409'd). But the queue saves with `_save_context(ctx, project_id,
expect=project_id)` using the *enqueue-time* name, so a rename mid-solve now makes
that identity guard fire and the finished solve is lost.

The fix is not to undo QA-P2 — the rebind is right. It is to reach the guard that
already exists: move `_queued_job_blocks_rename` above the branch so web mode refuses
a rename during a queued or running job exactly as local mode does.

### SL-7 — `_claim` lacks the desktop-quit drain gate its docstring claims to share

**Moderate. VERIFIED and FIXED — and live here:** `gridspine` imports in this
environment, although `2026-09-08-baseline-failures-characterised.md` lists it absent.

`_claim` now checks the drain in the same critical section as the status flip, as the
inline claim does and for the reason its comment gives, and returns a tri-state
(`claimed` / `cancelled` / `parked`). A bool could not express it: `False` sends the
job down the abort path, which records a terminal status and loses a job the drain
exists to keep. The docstring's "both runners claim identically" was false and now
says so.

The second half was worse than "minor": the "no storage directory" return sat between
`_claim` — which had already published the log queue — and the try/finally that closes
it, so anything streaming that job's log waited forever. It is inside the `try` now.

Worth recording: the first cut of the parked test let the unparked job run a REAL
study against a nonexistent directory, and against the old code it hung rather than
failed — a test that hangs on the bug it guards burns the CI timeout and reports
nothing. The study is stubbed now, and the old code fails in two seconds.

`_claim`'s docstring says it was extracted "so both runners claim identically", but the
`_draining` check exists only in `_run_solve_job`'s own inlined claim block;
`_claim` — used by `_run_gridspine_job` — has none. A gridspine job popped after the
quit snapshot would start under a process that is exiting, and `interrupted` jobs are
never resumed.

### SL-8 — `clear_finished` reports rows it did not delete

**Moderate. VERIFIED. FIXED** — `delete_jobs` returns a count or `None`; a failed
delete reports only what left memory. Guard:
`tests/test_clear_finished_honest_count.py`.

`services/solve_job_store.py::delete_jobs` swallows every exception and returns `None`.
`SolveQueue.clear_finished` returns `len(to_delete)` unconditionally, so on a database
error the caller is told "removed N" while the rows survive and the next listing pulls
them straight back. Its own docstring claims the return is "the count of DISTINCT jobs
actually removed from the table".

Fix shape: have `delete_jobs` return a count or raise; report what actually went.

---

## Chat / LLM tooling

### CH-1 — a colleague's email still reaches the LLM provider, on the success path

**Serious. VERIFIED. FIXED** — `_scrub_identities` at the single model-facing seam,
recursive (the member travels inside `detail` and inside lists), keeping the sibling
`yours`. Guard: `tests/test_chat_result_identity_scrub.py`, with a control that
ordinary payloads are untouched.

The 2026-08-27 finding was fixed on the `is_error` path only:
`chat_service._error_result_content` strips everything but a typed kind and a
`message`. The **success** path does not filter at all —
`_result_to_anthropic_content` is a `json.dumps` plus an untrusted-data fence.

`routers/projects.py::activate_project` returns `{"activated", "evicted", "lock":
lock_info}` and `load_project` returns `{**summary, "lock": lock_info}`, where
`_serialize_project_lock` → `project_locks.serialize_lock` emits
`{"holder_email": <another user's address>, "yours": False}`. The chat wrappers in
`services/chat_tools.py` return `_route(...)` unchanged.

So the ordinary path — a non-holder asking chat to open a project someone else is
editing — sends that person's email address to the third-party provider, keeps it in
`session.messages` to be replayed on every later turn of the session, and lets the
model paraphrase it into a reply that is persisted to `chat.jsonl`.

The schema tells the model `activate_project` returns `{activated, evicted}`, so the
`lock` member has no consumer on that side — it is pure leakage.

Fix shape: filter the success result the way the error path already is. An allow-list
of result keys per tool is better than removing this one key.

### CH-2 — a project switch made by a tool does not take effect for the rest of the turn (server mode)

**Serious. VERIFIED (empirically) and FIXED — at the second attempt, plus its import
half. The first fix recorded here did not work in production; see the correction.**

Measured before the first fix, binding a request context to A and dispatching a tool
that publishes B through the same `copy_context().run(...)` the dispatcher uses:

    bound  : A
    inside : B   <- the tool believes it switched
    after  : A   <- what the rest of the turn sees
    next   : A   <- the next tool in the same turn

It is worse than a stale read, because TWO mechanisms depend on that thread seeing
the switch and both were dead. `run_turn`'s `PROJECT_REBINDING_TOOLS` check — whose
own comment names the broken flow, "activate_project -> update_component against the
newly-activated scenario" — re-reads the same stale view, so it emitted no
`project_rebound` frame and never refreshed `turn_project_holder`. The frontend
therefore kept its old `currentProject` and the next autosave's `expect=` 409'd,
which is the 2026-06-08 incident that frame exists to prevent. And the guard meant to
catch EXTERNAL switches could not fire either.

**Correction (2026-10-05).** The first fix, `PyPSAService.adopt_active_from(snapshot)`,
copied the switch back into the dispatcher's context after the tool returned. That
context is itself a throwaway: `routers/chat.py` streams the turn as a SYNC
generator, and Starlette's `iterate_in_threadpool` runs every `next()` in a FRESH copy
of the request task's context — the router says so in two comments. The adopt was
discarded at the next `yield`. Its tests drove the generator with `list(...)`, in one
context, which is the one case where it works. Re-measured with a `next()` per fresh
copy: after `activate_project("B")` from A, the next step of the turn saw **A**.
An async handler (every import) adds a third copy, the `asyncio.run` task, whose
publish the adopt could not see even in one context.

Fixed with a turn CELL, `PyPSAService._turn_cell`: a one-element list bound from the
event-loop task in `chat_stream` (beside `set_acting_user`, for the same reason).
Every per-item copy, and every copy below those, holds the same list, so a write into
it survives all of them. Every read of the request context goes through `_scoped()`
and every write through `_set_scoped()`, so no caller can bypass it, and outside a
chat turn both are exactly the old ContextVar. `adopt_active_from` stays for callers
without a cell, and it had to change: it now adopts only when the tool changed the var
IN ITS COPY. Compared against the cell, an async import's stale snapshot reads as a
switch back to the old project, and the adopt would quietly undo the import. The new
async test caught that before it shipped.

The adopt is no longer scoped to `PROJECT_REBINDING_TOOLS`. The cell follows every
tool, so the direct path does too, or the two would disagree. A copy only the tool
writes to cannot carry another tab's switch, so following it is never wrong. Whether
the turn may CONTINUE is still the guard's call: a tool that changes identity without
being on the list stops the turn, loudly.

**The import half, which this entry used to leave open.** An import IS a rebind, and
the UI already treats it as one: `ImportExport.tsx` clears the active project "so the
5-min autosave ... can't CLAIM and overwrite the previously-active project's folder".
Off the list, a chat import was worse than stale. Measured in LOCAL mode with a real
`import_network_nc` in a real turn:

    import_network_nc  -> ok (1 bus over a 3-bus project X)
    list_components    -> project_switched_mid_turn; the turn stops
    project_rebound    -> none; the panel keeps currentProject = X
    autosave(expect=X) -> the save guard PASSES and writes the import over X

The guard passes because its identity check only fires against a BOUND backend. The
four raw imports, the bundle import and create-from-template are now rebinding tools.
The panel's `project_rebound` handler acted only on a truthy `to`, so it now also
handles `to: null` the way `ImportExport.tsx` does: clear the active project.

Guards: `tests/test_chat_tool_project_switch_reaches_the_turn.py`, rewritten to drive
the real dispatcher through a real `StreamingResponse` over a sync generator, so
TestClient's `iterate_in_threadpool` makes the per-item copies. It pins the mechanism
(no cell: the switch is lost), the async publish, the no-op control, the
direct-caller adopt, and, by spy, that `chat_stream` binds the cell. Also
`tests/test_chat_import_is_a_rebind.py` and two cases in
`frontend/src/components/ChatPanel.test.tsx`. Each was checked against its mutation:
no cell bind in the route, `_scoped` ignoring the cell, the adopt comparing against
the cell, an import dropped from the list, and the frontend `to: null` branch removed.


Original report, for the record:

`chat_service._dispatch_real_tool_call` runs each tool in
`contextvars.copy_context()` on an executor. `PyPSAService.set_active` publishes
through `cls._request_ctx.set(ctx)`, which in server mode mutates only the *copy*.
The generator thread keeps the old context, so every later tool in the same turn
operates on the previous project while the model believes it switched — and
`_project_switched()` never fires, so no `project_rebound` frame reaches the frontend.

Local mode is unaffected (no session cookie → the global `_active` is used), which is
why the suite does not catch it.

### CH-3 — `undo_last` reverts the wrong thing, and the schema says otherwise

**Serious. VERIFIED (both halves). Both halves FIXED — the behaviour on 2026-10-05.**

The false claims are corrected: the `chat_tools` module docstring no longer lists undo
among what tools inherit (it listed five things and was wrong about exactly one), the
`bulk_update_components` schema no longer promises "single undo snapshot", and the
`undo_last` schema now tells the model plainly that the stack holds the user's CANVAS
edits and not its own, and not to offer it as a way to reverse its own change.

**Behaviour, fixed 2026-10-05.** Chat writes now get a real snapshot: the dispatcher
pushes ONE per turn, per project, before the turn's first network-changing tool
(`chat_service._snapshot_for_turn_undo`), at the same seam and for the same reason as
the dirty mark beside it. The unit is the TURN, which answers the cost question this
entry left open — a 30-edit turn pays one `export_to_netcdf`, not thirty — and is what
the texts the model reads had promised all along ("Writes participate in the
turn-level undo"; a reconstruction "created inside one undo snapshot so a misread can be
reverted in one click"). It is also what a user means by "undo what the assistant just
did". An `undo_last` inside the turn pops that snapshot, so the next edit re-arms and
pushes a fresh one. The record of which stacks the turn has pushed to lives on the
`ChatSession` — keyed by the project's `_UndoState` object, which survives an in-place
swap — not in a ContextVar, for the reason CH-2's correction gives.

Which tools: derived like the lock gate (`chat_tools.UNDO_CAPTURED_TOOLS`) — write
routes under the middleware's undo prefixes minus its exclusions, plus the routeless
network mutators; project-folder writes are out, since a snapshot before them would
be an undo step that changes nothing. Kept on failure rather than popped as the
middleware pops a 4xx: a tool that fails partway may have changed the network, the
same reasoning the dirty mark records.

The schemas now say what happens: `undo_last` reverts the whole of the assistant's
last turn (or, called within a turn, returns to its start), and a canvas edit made
after the turn is the newer step.

Guard: `tests/test_chat_undo_snapshot.py` (8) — one step for a two-edit turn through
the real `run_turn`, one step per turn across turns on one session, the re-arm after an
in-turn undo, no step for a read tool or an unconfirmed destructive one, and the
constants against `main.py`'s. Bitten four ways: the push removed, the once-per-turn
check removed, the re-arm removed, the turn-boundary reset removed.

Undo snapshots are pushed by the HTTP middleware in `main.py`. Chat tools call
handlers in-process, so a chat edit pushes nothing. `undo_last` therefore either
refuses ("nothing to undo") or reverts an *older canvas edit* while reporting
`{"undone": true}`.

Several pieces of text the model reads assert the opposite — the
`apply_demand_from_excel` schema ("Writes participate in the turn-level undo"), the
`reconstruct_network_from_image` docstring ("All components are created inside one
undo snapshot"), and the module docstring.

### CH-4 — write tools that skip the holder check the REST route enforces

**Serious. VERIFIED AND FIXED, all of it.**

`put_asset_health` had no check while its two sidecar siblings do — it did not even
accept `db`/`user`, so it could not have checked. It is the third handler missed by
the same sweep (`routers/uploads.py` carries a comment about being the second), and
`asset_health.json` is the outage-rate provenance ledger in `_BUNDLE_FILES`. Guard:
two tests added to `tests/test_worksheet_foreign_lock.py`, the module written for the
first two.

The remaining five plus the export chokepoint are now fixed too, and verifying them
sharpened the finding. The reviewer called the gate's header comment "false
assurance"; it is not, quite — it says upload / export / chat-history tools are
DELIBERATELY ungated because they "write artifacts, not network state", which is a
decision somebody made. What makes it a defect is that `routers/uploads.py` had
already decided the same question the other way, in writing: the upload POST is "a
write edge into `project.directory` same as save/rename/delete", and the DELETE is
"the sharp end of the gap this router had: a non-holder deleting a file another
session is actively referencing (e.g. mid multimodal turn)". Both REST routes check
the lock; the chat tools bypassed the handler and did the work anyway. Two paths, the
same bytes, opposite rules — and the REST side's reasoning is the more specific.

`delete_upload`, `clear_uploads`, `clear_chat_history`, `start_campaign` and
`end_campaign` join `_LOCK_GATE_SERVICE_CALL_MUTATORS`. The ten `export_*` tools
cannot: they are tiered `read` and `write` inconsistently, so the derivation skips
some of them whatever the mutator set says. They are gated at `_save_agent_export`
instead — the single helper all ten reach — which avoids re-tiering ten tools, since
that would change their confirmation-card behaviour for reasons unrelated to this
bug. `export_network_nc`, tiered `read`, is the test that discriminates.

Guard: six tests in `tests/test_chat_tools_dispatch.py`. Worth recording that the
first two cuts of them FAILED against a correct fix — they called the module-level
functions (the gate replaces `DISPATCHERS` entries) and omitted `_bound_to(ctx)`
(`_check_foreign_lock` resolves the active project from the request context). Both
read as "the fix does not work". The bite test is what settled it: reverting the
product change turns five of them red.

`_lock_gated_tool_names` derives its set from route prefixes plus five hand-listed
mutators, so tools that call services directly are outside it. Named:
`delete_upload`, `clear_uploads`, `_save_agent_export` (reached by every `export_*`
tool), `record_asset_health`, `clear_chat_history`, `start_campaign`/`end_campaign`.
`routers/adequacy_worksheet.py::put_asset_health` is missing the check its siblings
`put_worksheet` and `put_stress_scenarios` have, so that one is a shared REST gap.

The header comment asserting that "/api/projects/* write tools already call
`_enforce_project_lock` in the handler body" is false for these.

### CH-5 — `GET /api/chat/history` clears a live session and can poison it

**Serious. VERIFIED. FIXED** — the rebuild is skipped while `_turn_in_flight`. The
`session_was_freshly_minted` guard had wrapped only the three-line profile adoption,
under a comment explaining at length why a live session must not be disturbed.
Guard: `tests/test_history_does_not_clobber_a_live_session.py`, which carries a
guard-on-the-guard (the fixture must actually reach the session the handler
resolves — the first version did not, and passed against the broken code) and a
control that an IDLE session is still rehydrated.

`routers/chat.py::chat_history` does `sess.messages.clear()` and rebuilds from disk
unconditionally; only the profile adoption is guarded by `session_was_freshly_minted`.
A history fetch during an in-flight turn drops the `tool_use` block whose
`tool_result` the running turn is about to append, leaving an orphan `tool_result`
that makes every later turn 400 at the provider.

### CH-6 — chat session ownership is not enforced on `/stream` or `/history`

**`/stream` half: VERIFIED and FIXED. `/history` half: VERIFIED, left open by
design at first, FIXED on 2026-10-05 — see the last section.**

`session_owner_allows` was added for `/confirm`, `/rewind` and `/abort`; `/stream`
was not one of the three, and it is the worst of the four to leave open. It resolves
a caller-supplied `session_id` through a process-global registry whose owner is set
on CREATE ONLY, so an existing session belonging to someone else came back as-is and
the turn ran inside it: the outbound message array is seeded from that session's
history, so a stranger's conversation and its tool results reach the provider on the
caller's behalf and can be elicited in the reply; the caller's message lands in the
stranger's thread; and the tools run with the CALLER's authority.

Fixed with a 403 `session_not_yours`, scoped to sessions that ALREADY existed
(minting one for a caller-chosen id is what every first turn does). A refusal rather
than quietly minting a different session, because the client uses the id it SENT for
`/abort` and `/confirm` — handing back another would leave both pointing at a session
that is not running the turn. The frontend now recovers: `api/chat.ts` carries the
server's `error_kind` on the Error, and `ChatPanel` calls `startNewChat()` on
`session_not_yours` instead of retrying an id the server will refuse forever.

**The `/history` half is left open deliberately.** `chat_history` mints the session
owned by whoever fetches first, and `last_session_id` comes from the project's SHARED
`chat.jsonl` — so a co-member can end up owning a session that is really someone
else's thread. Every available repair is worse than the disease: reassigning the
owner reopens exactly the hole `session_owner_allows` closes; minting it owner-less
trips the deliberate fail-closed rule and locks everyone out; and not minting at all
breaks the rehydration the route exists for. The real defect is deriving a PER-USER
session identity from a shared file, and fixing that is a design change, not a patch.

Note the interaction, which is the honest cost of the `/stream` fix: where that case
occurs, the rightful user now meets a clear 403 at stream time and is moved onto a
fresh session, instead of having their turn run and then block on a confirmation card
they can never answer until the 300s TTL expires. Worse before, and visible now.

**The `/history` half, fixed 2026-10-05.** The three rejected repairs all answered
"whose is THIS session?" for a session that was never the caller's. The fix is to
stop asking: each persisted turn now records its author (`owner_user_id`, the
session owner), and `/history` returns the session of the CALLER's last turn —
searched across the whole transcript, before `limit` trims it — instead of the
file's. Alice no longer adopts Bob's thread, so `/stream` has nothing to refuse
her.

Records written before the key keep their old behaviour (they count as the
caller's). The stricter reading — nobody's, in server mode — was implemented first
and reverted: it costs every server user their session continuity, and the model its
prior context, on the first reload after upgrading. What it would prevent is
bounded anyway. `/history` will not hand back or rebuild a live session registered to
a DIFFERENT known owner (a legacy or forged record), and `/stream` refuses one, which
the panel recovers from. Legacy records age out with each user's next turn.
`/chat/import` drops the key: on an imported turn it names a user of another
install, or is forged. The key is also stripped from the `/history` response, so
co-members' user ids do not reach each other's browsers.

Guard: `tests/test_chat_history_per_user.py` (8) — per-user sessions, minted with the
right owner; no session for a user with no turns, and no minting of the colleague's;
the legacy and mixed-file cases; a live session with a different owner neither
returned nor rebuilt; the key absent from the wire; the write side records it; import
drops it. Each mechanism was checked against its mutation.

Original report, for the record:

`/confirm`, `/rewind` and `/abort` all call `session_owner_allows`; `chat_stream` does
not, and `chat_history` mints the session owned by whoever fetched it first. Since
`last_session_id` comes from the shared `chat.jsonl`, a co-member can end up owning
another user's session — which then blocks that user's confirmation cards (404 until
the 300s TTL) and lets either party abort the other's turn.

### CH-7 — `batch_create_components` reports a partially-applied batch as a clean refusal

**Moderate. VERIFIED and FIXED.**

The code argued against itself. Its non-HTTP branch carried exactly the right
comment — "Validation passed and this still failed, so the batch IS partial. Say
exactly what landed — claiming atomicity we did not deliver would send the agent
looking for the wrong bug" — but the line above it was `except HTTPException: raise`.
Pass 1 checks only the schema and name uniqueness; the per-class handler validators
(the docstring's own "transformer voltage validation", a missing bus) run in
`create_component` during pass 2 and raise HTTPException. So the honest message
covered only the failure that rarely happens.

Now any exception after something has landed reports what landed. The original
status is kept (a voltage mismatch is the caller's data, not a 500) and a structured
detail keeps its `error_kind` with `partially_applied` / `created` added beside it.
A failure on the FIRST entry is re-raised unchanged — nothing landed, so calling it
partial would be the same wrong claim in the other direction.

Guard: three tests in `tests/test_chat_tools_batch.py`. One discriminates (fails
against the previous code); one is a control (first-entry failure); one guards the
fix's design choice (status kept, which the old non-HTTP branch would have forced
to 500).

Pass 1 validates only the schema and name uniqueness; handler-level validators
(transformer voltage, bus existence) run in pass 2. An `HTTPException` there is
re-raised by `except HTTPException: raise`, skipping the branch that reports what was
already created. The model is told the batch failed when part of it landed.

### CH-8 — the chat lock gate refuses a study abort the HTTP gate allows

**Moderate. VERIFIED and FIXED — and it was three drifts, not one.**

1. As reported: chat's exempt set held only the three queue paths, so
   `abort_adequacy_study` was refused under a foreign lock in chat while the same POST
   succeeded over HTTP.
2. Missed by the review: main.py's OWN list said "the five adequacy-study ABORTS"
   and never gained the Energy Hub one, which calls the same `_abort_study` helper and
   whose docstring says "Same contract as the other study abort POSTs". So an EH study
   could be trapped by a foreign lock over plain HTTP too. The review called that
   route "consistent" because both surfaces gated it — consistent, and wrong on both.
3. Found by the new test: `/api/simulation/preflight` is exempt over HTTP and not in
   chat. Latent — `validate_network` is tiered `read`, so the tier filter skips it
   first — but re-tiering one tool would have made chat refuse a preflight HTTP
   deliberately allows.

Fixed by exempting all six study aborts on both surfaces and preflight in chat, and —
the durable part — by extending `tests/test_chat_tools_lock_gate_parity.py` from
prefixes to exemptions. It asks, for every route any tool can reach, whether
main.py's real predicate (`_foreign_lock_gate_exempt`) and chat's set give the same
answer. Per route rather than set-against-set, because the sets legitimately differ
(main exempts routes no tool reaches) and because a pairwise comparison is exactly
what could not see drift 2. Three new tests; all three fail against the previous
code.

Original report, for the record:

`_LOCK_GATE_EXEMPT_PATHS` (3 entries) is narrower than
`main.py::_FOREIGN_LOCK_GATE_EXEMPT_EXACT` (11, including the study aborts), so
`abort_adequacy_study` is refused under a foreign lock while the equivalent HTTP POST
succeeds. `main.py`'s own comment says gating an abort "would be actively harmful:
... trap it with no way to stop it". Fails closed, so the direction is safe, but it
recreates the trapped-study scenario in chat.
`tests/test_chat_tools_lock_gate_parity.py` compares only the prefix sets.


### CH-9 — in server mode a raw import over HTTP is invisible to the next request

**Serious. VERIFIED (empirically) and FIXED. Found 2026-10-05 while closing CH-2's
import half; not from the original two reviews.**

`POST /api/io/import/{netcdf,csv,excel,matpower}` calls `reset_network`, which in a
request publishes the new UNBOUND context into the session's scratch slot. But every
request re-resolves its context from the session's active-project POINTER
(`active_project.resolve_for_session`), and the import routes never moved it. So the
next request resolved the previous project again. Measured with the authenticated
TestClient: save and activate a 3-bus project, import a 1-bus file. The response is a
200 reporting 1 bus. `GET /network/buses` returns the 3 buses, and `/network/meta`
still says `loaded_project: X`. The frontend then clears `currentProject` as it does
after every raw import, so the user is shown the OLD network labelled as unsaved, and
a Save As would have saved that network under the new name.

`/network/reset` (New Project) had already met exactly this and says so: "Leaving the
pointer set would make the very next request re-resolve the old project ... the reset
would appear to silently undo itself." The four imports now un-point the session the
same way (`routers/io._unbind_session`), on SUCCESS only. A refused import (an `ic:`
bus) raises before that point, and the session stays on its project, which is still
resident and untouched, because the reset landed in the scratch slot. The chat import
wrappers call these async routes directly, so they now pass the acting db and session
themselves (`chat_tools._import_raw`), as `import_project_bundle` already did.

Local mode was never affected: there is no pointer, and the process foreground is
what every request reads.

Guards, in `tests/test_chat_import_is_a_rebind.py`: the HTTP import is what the next
request sees; a refused import leaves the session on its project; and the chat tool
moves the acting session's pointer. The first and third fail with the
un-pointing removed, and with the wrapper's session dropped, respectively.

---

## Lower-confidence notes worth keeping

Recorded on 2026-09-28 and not counted above. **Each was verified on 2026-10-06**;
the outcome is given per note. The four that were real are fixed, and so is one
interaction the #82 merge created. Guard: `tests/test_chat_tool_output_hygiene.py`
(6 tests), with each fix checked against its mutation.

- **`chat.jsonl` is written only when a turn ends cleanly.** VERIFIED, left as a
  design decision. A turn ending by abort, the tool-call cap, a mid-turn project
  switch or a stream error leaves no transcript record, although its mutations ran.
  They are not unrecorded: the change log has every one. Persisting a partial turn
  is a design change, not a patch: an interrupted assistant message can end on a
  `tool_use` with no `tool_result`, and replaying that from `/history` makes every
  later turn of the session invalid to the provider. That needs an explicit
  "interrupted turn" record shape and a sanitiser on rehydration.
- **`export_to_csv` / `export_to_excel` write model-supplied cells unmodified.**
  VERIFIED and FIXED, and worse than noted for xlsx. openpyxl stores any string
  starting with `=` as a live formula (`data_type == "f"`), not merely text a
  spreadsheet might interpret, so a bus named `=HYPERLINK(...)` became a working
  link in a file the user was invited to open. CSV cells starting with = + - @ tab
  or CR now get OWASP's leading apostrophe, except strings that are numbers
  (`"-5"`). xlsx formula cells are re-typed as strings, so the text survives
  unchanged.
- **`export_*` tools are tagged `Safety: read` but write into the uploads directory
  and consume quota.** VERIFIED, left as a product decision. Re-tiering them changes
  their confirmation behaviour, which this register does not decide. The part that
  was a defect, a non-holder writing into a locked project, is closed at their
  shared chokepoint (`_save_agent_export` calls `_check_foreign_lock`; CH-4).
- **`gridspine_export_handoff_bundle` returns an absolute server path.** VERIFIED and
  FIXED. `str(path)` named the storage root plus the org and project UUIDs, and was
  of no use to anyone, since nothing can fetch a server-side path. The tool now
  returns the download route the study view uses,
  `/api/gridspine/{name}/bundles/{hour}`.
- **`apply_demand_from_excel(replace=...)` is declared and ignored.** VERIFIED and
  FIXED by removal. Nothing read the flag, and the tool always replaces the Load's
  profile, which is what the confirmation card already says. The flag offered the
  model an option that did not exist. It is gone from the signature and the schema,
  and the description now says "REPLACING any profile it already has".
- **Non-HTTPException error text reaches the provider.** VERIFIED and FIXED for the
  identifying part. A bare exception such as `FileNotFoundError` carries the
  absolute path it failed on, which under the org-scoped layout is
  `<root>/<org uuid>/<project uuid>/…`. `_error_result_content` now replaces the
  two storage roots with `<project storage>`, keeping the file name the model needs
  to explain the error. Targeted rather than a general path scrub, which would
  mangle legitimate text.
- **New, from the #82 merge: a chat edit refused by a live study still cost an undo
  snapshot.** VERIFIED and FIXED. The HTTP middleware returns the study refusal
  before it snapshots; the chat dispatcher snapshotted first, and the gate (inside
  the handler) refused afterwards. That exported a network the study was
  re-solving, holding the network lock against it, for an undo step that changes
  nothing. Every captured tool is refused during a live-network study, so
  `_snapshot_for_turn_undo` now skips while one runs.
