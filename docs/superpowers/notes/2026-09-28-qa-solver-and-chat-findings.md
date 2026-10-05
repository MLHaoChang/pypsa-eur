# QA end-to-end review — solver lifecycle and chat/LLM tooling

Date: 2026-09-28, against `master` at `9f83f37` plus the fixes on
`claude/solution-fmea-integration-0mx5lc`. These are the two areas the
2026-09-28 review had in scope and never got a reviewer; the eleven findings from
that pass are in `2026-09-28-qa-e2e-findings.md`.

Sixteen findings, from two independent reviewers working read-only and in parallel.

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

**Serious. VERIFIED (empirically) and FIXED.**

Measured before the fix, binding a request context to A and dispatching a tool that
publishes B through the same `copy_context().run(...)` the dispatcher uses:

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

Fixed with `PyPSAService.adopt_active_from(snapshot)`, called by the dispatcher after
a whitelisted tool returns. Two deliberate limits:

  * it carries ONLY the active-project var. Copying every var back would let a tool
    change the acting user and session — a privilege-transfer primitive, not a fix;
  * it runs only for `PROJECT_REBINDING_TOOLS`. `import_*` and anything else reaching
    `reset_network` publishes too, but publishes an UNBOUND context and is not
    whitelisted; adopting it would make `_project_switched` fire and refuse the rest
    of the turn with `project_switched_mid_turn`, turning a silent bug into a loud one.

**Still open, narrower:** after an `import_*` in server mode the imported network
lands in the registry (visible from the next request) but not for the rest of the
same turn. Fixing that properly means deciding whether an import IS a rebind — a
question about the guard's design, not this dispatcher.

Guard: `tests/test_chat_tool_project_switch_reaches_the_turn.py`, driven through the
real `_dispatch_real_tool_call` with fakes under real tool names (a real
`activate_project` needs a database, two projects and a resident registry entry that
would test everything except this). It pins the scoping and the no-op case as controls;
the primary test fails against the previous code.

`chat_service._dispatch_real_tool_call` runs each tool in
`contextvars.copy_context()` on an executor. `PyPSAService.set_active` publishes
through `cls._request_ctx.set(ctx)`, which in server mode mutates only the *copy*.
The generator thread keeps the old context, so every later tool in the same turn
operates on the previous project while the model believes it switched — and
`_project_switched()` never fires, so no `project_rebound` frame reaches the frontend.

Local mode is unaffected (no session cookie → the global `_active` is used), which is
why the suite does not catch it.

### CH-3 — `undo_last` reverts the wrong thing, and the schema says otherwise

**Serious. VERIFIED (both halves). DOCUMENTATION HALF FIXED; behaviour still open.**

The false claims are corrected: the `chat_tools` module docstring no longer lists undo
among what tools inherit (it listed five things and was wrong about exactly one), the
`bulk_update_components` schema no longer promises "single undo snapshot", and the
`undo_last` schema now tells the model plainly that the stack holds the user's CANVAS
edits and not its own, and not to offer it as a way to reverse its own change.

Still open: giving chat writes a real snapshot. That is a design change — a snapshot
is an `export_to_netcdf` round-trip, so one per in-process tool call is not obviously
affordable — and it is not attempted here.

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
design — see below.**

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

---

## Lower-confidence notes worth keeping

Not counted above; recorded so they are not rediscovered from scratch.

- `chat.jsonl` is written only when a turn ends cleanly. A turn ending via abort, the
  tool-call cap, a mid-turn project switch or a stream error leaves no transcript
  record although its mutations already ran.
- `export_to_csv` / `export_to_excel` write model-supplied cells unmodified, so a
  component name beginning with `=` becomes a formula in a downloadable file.
- `export_*` tools are tagged `Safety: read` but write into the project's uploads
  directory and consume quota.
- `gridspine_export_handoff_bundle` returns an absolute server path (containing org
  and project UUIDs) to the model.
- `apply_demand_from_excel(replace=...)` is declared in the schema and ignored.
- Non-HTTPException error text (e.g. a `FileNotFoundError` carrying absolute storage
  paths) still reaches the provider; `_error_result_content` documents this residual.
