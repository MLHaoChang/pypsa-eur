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

**Serious. VERIFIED.**

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

**Serious. VERIFIED.**

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

**Serious. VERIFIED.**

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

**Moderate. VERIFIED.**

`_run_solve_job` computes `objective = sim._compute_run_objective(n, config)` *before*
branching on status, and publishes it unconditionally through `ctx_state_update` and
onto the persisted job row. `_compute_run_objective` reads `n.objective +
n.objective_constant`; PyPSA assigns `_objective` only on a successful solve and it
survives in the netCDF. So an infeasible or aborted run reports the last successful
run's cost, in the queue UI and in the saved row.

Fix shape: compute and publish it only when the final status is `completed`.

### SL-5 — a `BaseException` in a queue job leaves the context permanently owned

**Moderate (rare trigger, severe effect). REPORTED.**

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

**Moderate. VERIFIED, and sharpened by this branch's QA-P2 fix.**

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

**Moderate. REPORTED.**

`_claim`'s docstring says it was extracted "so both runners claim identically", but the
`_draining` check exists only in `_run_solve_job`'s own inlined claim block;
`_claim` — used by `_run_gridspine_job` — has none. A gridspine job popped after the
quit snapshot would start under a process that is exiting, and `interrupted` jobs are
never resumed.

### SL-8 — `clear_finished` reports rows it did not delete

**Moderate. VERIFIED.**

`services/solve_job_store.py::delete_jobs` swallows every exception and returns `None`.
`SolveQueue.clear_finished` returns `len(to_delete)` unconditionally, so on a database
error the caller is told "removed N" while the rows survive and the next listing pulls
them straight back. Its own docstring claims the return is "the count of DISTINCT jobs
actually removed from the table".

Fix shape: have `delete_jobs` return a count or raise; report what actually went.

---

## Chat / LLM tooling

### CH-1 — a colleague's email still reaches the LLM provider, on the success path

**Serious. VERIFIED.**

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

**Serious. REPORTED — reviewer demonstrated the mechanism in-process.**

`chat_service._dispatch_real_tool_call` runs each tool in
`contextvars.copy_context()` on an executor. `PyPSAService.set_active` publishes
through `cls._request_ctx.set(ctx)`, which in server mode mutates only the *copy*.
The generator thread keeps the old context, so every later tool in the same turn
operates on the previous project while the model believes it switched — and
`_project_switched()` never fires, so no `project_rebound` frame reaches the frontend.

Local mode is unaffected (no session cookie → the global `_active` is used), which is
why the suite does not catch it.

### CH-3 — `undo_last` reverts the wrong thing, and the schema says otherwise

**Serious. REPORTED.**

Undo snapshots are pushed by the HTTP middleware in `main.py`. Chat tools call
handlers in-process, so a chat edit pushes nothing. `undo_last` therefore either
refuses ("nothing to undo") or reverts an *older canvas edit* while reporting
`{"undone": true}`.

Several pieces of text the model reads assert the opposite — the
`apply_demand_from_excel` schema ("Writes participate in the turn-level undo"), the
`reconstruct_network_from_image` docstring ("All components are created inside one
undo snapshot"), and the module docstring.

### CH-4 — write tools that skip the holder check the REST route enforces

**Serious. REPORTED.**

`_lock_gated_tool_names` derives its set from route prefixes plus five hand-listed
mutators, so tools that call services directly are outside it. Named:
`delete_upload`, `clear_uploads`, `_save_agent_export` (reached by every `export_*`
tool), `record_asset_health`, `clear_chat_history`, `start_campaign`/`end_campaign`.
`routers/adequacy_worksheet.py::put_asset_health` is missing the check its siblings
`put_worksheet` and `put_stress_scenarios` have, so that one is a shared REST gap.

The header comment asserting that "/api/projects/* write tools already call
`_enforce_project_lock` in the handler body" is false for these.

### CH-5 — `GET /api/chat/history` clears a live session and can poison it

**Serious. REPORTED.**

`routers/chat.py::chat_history` does `sess.messages.clear()` and rebuilds from disk
unconditionally; only the profile adoption is guarded by `session_was_freshly_minted`.
A history fetch during an in-flight turn drops the `tool_use` block whose
`tool_result` the running turn is about to append, leaving an orphan `tool_result`
that makes every later turn 400 at the provider.

### CH-6 — chat session ownership is not enforced on `/stream` or `/history`

**Moderate. REPORTED.**

`/confirm`, `/rewind` and `/abort` all call `session_owner_allows`; `chat_stream` does
not, and `chat_history` mints the session owned by whoever fetched it first. Since
`last_session_id` comes from the shared `chat.jsonl`, a co-member can end up owning
another user's session — which then blocks that user's confirmation cards (404 until
the 300s TTL) and lets either party abort the other's turn.

### CH-7 — `batch_create_components` reports a partially-applied batch as a clean refusal

**Moderate. REPORTED.**

Pass 1 validates only the schema and name uniqueness; handler-level validators
(transformer voltage, bus existence) run in pass 2. An `HTTPException` there is
re-raised by `except HTTPException: raise`, skipping the branch that reports what was
already created. The model is told the batch failed when part of it landed.

### CH-8 — the chat lock gate refuses a study abort the HTTP gate allows

**Moderate. REPORTED.**

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
