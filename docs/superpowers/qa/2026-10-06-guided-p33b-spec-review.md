# P33b spec review — study freshness after an edit (OPEN-ITEMS 10b / 10a / 10c)

**Reviewed:** `specs/2026-10-06-guided-p33b-study-freshness.md`, the "P33b plan (2026-10-06)" section of `plans/2026-09-28-guided-mode-deferred.md`, OPEN-ITEMS 10a / 10b / 10c. **Tree:** branch `claude/epic-allen-k2t1c4`, HEAD `3aae075d4` (spec anchors were taken on `c99c255f3`; every anchor cited below was re-read on HEAD). **Method:** adversarial read against the code; two throw-away probe tests were run against the backend with the suite's `client` / `api_project` fixtures and deleted afterwards (nothing in the tree was edited). `BE` = `pypsa-gui/backend`, `FE` = `pypsa-gui/frontend/src`.

## Verdict: **APPROVE WITH CONDITIONS**

The design (a per-ctx edit counter at the seams, captured on the record, compared at read time, `null` for Unavailable, "edited since" as the only claim) is the right one and the spec's reasoning in §1.2 holds. Three findings must be fixed in the spec before step 1 starts, because each one makes a stated fact or a red test wrong as written: the chat predicate misses five live-network write tools (B-1); HTTP undo under session routing does not land in the context later requests read, which invalidates the undo rule, the `+2` assertions and smoke (C4) (B-2); and `eh_study_record` in `RESULT_STATE_KEYS` without a dataclass field breaks a pinned invariant (B-3). Everything else is should-fix or a note. 10a's root cause is correctly traced. 10c's two tests are sound.

Conditions: B-1, B-2, B-3 resolved in the spec (and B-2's scope decided by the owner — new D-5), S-1 decided (it is an O1 precedence change presented as no change).

---

## BLOCKER

### B-1. The derived chat predicate misses five `_SERVICE_CALL` tools that write the live network

Spec §1.3 B2 derives `tool_edits_network(name)` from `TOOL_ROUTES` ("a non-GET method on an `_UNDO_PREFIXES` path … no hand list") and §1.5 claims the parametrized test means "a new write tool cannot be missed silently". Five tools that mutate the live network today have no route at all — their `TOOL_ROUTES` entry is the `_SERVICE_CALL` sentinel — so the predicate is `False` for them and the parametrized test cannot see them:

| Tool | Tier | `TOOL_ROUTES` | What it writes |
|---|---|---|---|
| `batch_create_components` | write | `_SERVICE_CALL` (`BE/services/chat_tools_schema.py:2896`) | loops `create_component` (`BE/services/chat_tools.py:974-1045`) |
| `batch_delete_components` | destructive | `_SERVICE_CALL` (`:2897`) | loops `delete_component` (`chat_tools.py:1047`) |
| `generate_exemplary_timeseries` | write | `_SERVICE_CALL` (`:2932`) | uploads a profile into `_user_ts` / `n.*_t` (`chat_tools.py:1273`) |
| `apply_demand_from_excel` | destructive | `_SERVICE_CALL` (`:3017`) | writes a Load's series under the lock (`chat_tools.py:4287`, "Pass 2 (LOCKED)") |
| `reconstruct_network_from_image` | destructive | `_SERVICE_CALL` (`:3022`) | materialises buses and lines via `create_component` (`chat_tools.py:4553`) |

(Computed on HEAD with `safety_tier_for` over `TOOL_ROUTES`: these are the only write/destructive tools whose routes are non-HTTP and that touch the network. The other non-HTTP writers — `gridspine_*`, `export_*`, `set_active_profile`, `clear_uploads`, `start_campaign` / `end_campaign`, `clear_chat_history` — do not: `gridspine_service.py:467-470` opens its own `pypsa.Network(str(nc))` from disk.)

A Guided user who asks the assistant to "add these ten generators" (the batch tool exists precisely so that is one turn) gets a network that was edited and a card that still says the results describe it. That is the B1 class the honesty rule exists for.

**Change to the spec (§1.3 B2, §1.5, §1.6):** the predicate is `TOOL_ROUTES`-derived **or** membership in an explicit, pinned `chat_tools_schema._SERVICE_CALL_NETWORK_WRITES = frozenset({…the five…})`. The test becomes: every tool whose tier is `write` / `destructive` and whose routes are `_SERVICE_CALL` must be in exactly one of `_SERVICE_CALL_NETWORK_WRITES` or `_SERVICE_CALL_NON_NETWORK_WRITES` (parametrized over `TOOL_ROUTES`; a sixth unclassified service-call writer fails the test). Add `T.batch_create_components(...)` (+1) to `test_chat_edit_tools_bump_…` and a mutant "`_SERVICE_CALL_NETWORK_WRITES` drops `apply_demand_from_excel`". Replace the sentence "no hand list" with "one hand list, for the tools that have no route, pinned by construction".

### B-2. HTTP undo under session routing does not land in the context the next request reads — the undo rule, the `+2` assertions and smoke (C4) are built on a false fact

Spec §1.1 states "Undo is a same-project in-place replace" and §2.2 restores finished records "into the new ctx's `solver_state` right after `set_binding`"; §1.5 asserts the counter is `+2` after edit → undo; §5 (C4) asserts `rev0 + 2` and that the bus edit is undone.

On HEAD, inside a session-bound request (`BE/main.py:769-775` binds `_request_ctx`), `apply_undo` → `reset_network` → `_publish_active` writes the new **unbound** ctx into the session's scratch slot (`BE/services/pypsa_service.py:287-298`: `slot = ctx.registry_key or cls._request_scratch.get()`), then `set_binding` (`network_undo.py:134` → `bind_project`, `pypsa_service.py:1193-1222`) stamps identity but never `rekey_context`s it (`rekey_context` has exactly two callers: `routers/projects.py:1977` and `:3208`). The project's `org:uuid` slot keeps the **pre-undo** ctx, and that is what `resolve_for_session` returns on the next request (`BE/services/active_project.py:103`).

Reproduced with a probe test on the suite's fixtures (`client`, `api_project`, `registry_key_for`): `PUT /api/network/buses/{b}` x → x+2.5 (200) → `POST /api/network/undo` (200) → `GET /api/network/buses` still reads **x+2.5**; `PyPSAService._contexts` holds `org:uuid → (pre-undo ctx, x+2.5, bound)` and `scratch:<session> → (new ctx, x, bound to the same project)`. A planted finished `eh_study` record was `None` on the pre-undo ctx afterwards (today's prev-clear, 10a) and absent on the new one. No existing test reads the network back after an HTTP undo (`test_unsaved_results.py:42-50` and `test_chat_edits_are_captured.py` assert depth only); the no-session path (`cls._active`) works, which is why the legacy tests pass. The smoke backend runs with sessions (`PYPSAGUI_LOCAL_MODE: '1'` still resolves a session row — P27b's lock flows depend on it), so (C4) will hit this.

Consequences for the spec as written: (i) the record restored onto the new ctx is on a ctx no later request reads, while — with 10a(a) alone — the pre-undo ctx now keeps its record anyway, so the undo rule is both ineffective and unnecessary; (ii) B1 bumps `dirty_state.bump_revision()` on the request's active ctx = the new one, so the project's counter is **not** bumped: `/undo/info` on the next request reads `rev0 + 1`, and `test_undo_bumps_and_keeps_the_finished_record` and (C4) cannot pass; (iii) `test_a_finished_study_survives_a_load_of_another_project`'s "New" leg is fine (different project), but any test that undoes and then reads is measuring the wrong ctx.

**Change to the spec:** this is a pre-existing defect outside 10a/10b/10c, so it needs an owner decision (**new D-5**): (a) add **step 0** — `apply_undo` re-keys the rebound ctx into the project's slot (`PyPSAService.rekey_context(ctx)` after `set_binding`, inside the lock; one line) with a red HTTP test "edit → undo → the next request reads the pre-edit value, and `_contexts[org:uuid]` is the ctx that holds it" in `tests/test_eh_study_record_survives_reactivation.py` or a new `test_undo_rekeys_the_session_context.py`; then §2.2's undo rule, the `+2` assertions and (C4) stand as written (the records restore is then still needed, because the re-keyed new ctx is a fresh copy with the key cleared); or (b) drop the undo rule, the `+2` cases and (C4), record the defect as a new OPEN-ITEM (High: an undo that does not undo), and keep 10a(a) which already preserves the record on the ctx later requests read. Recommend (a): the smoke's undo step is the one sequence a Guided user will actually perform, and the fix is contained. Either way, correct §1.1's fact row and §2.1's "undo drops the record" (it is the prev-clear that drops it, and under sessions the undo itself is what is lost).

### B-3. `eh_study_record` in `RESULT_STATE_KEYS` with no dataclass field, and "pop at hydrate", breaks a pinned invariant

§2.2(b) adds `"eh_study_record"` to `RESULT_STATE_KEYS` and has the hydrate paths `state.pop("eh_study_record", None)`. `tests/test_project_state.py:29-57` pins that the live `_state` dict carries **exactly** the `ProjectSolverState` fields and that every field is in exactly one group; `_restore_results_state` first sets every `RESULT_STATE_KEYS` member to `None` (`BE/routers/projects.py:432`) and `_hydrate_context_from_disk` does the same (`:2341-2342`). As written, the key is written as `None`, then popped, and there is no field — both tests go red at step 4 and the "no orphan key" rule the dispatcher relies on is broken.

**Change to the spec (§2.2(b), §4):** declare `eh_study_record: Any = None` on `ProjectSolverState` (`BE/services/project_context.py:455-480`, with the comment: the on-disk mirror of a **finished** `eh_study`, derived at save, consumed at hydrate, always `None` in memory), and at hydrate assign `state["eh_study_record"] = None` after mapping rather than popping. Rewrite the `:468` comment and the docstring of `test_every_field_is_classified_exactly_once` (`:42-52`) so the rule reads "study records are not persisted — except the finished EH record's mirror, which is compared against the revision, never trusted" — otherwise the next reader re-derives the old rule and deletes the key.

---

## Should-fix

### S-1. The greeting table changes O1's precedence and labels it "—"

Today, a finished hub study with `dispatch === 'stale'` falls through to the dispatch-stale sentence **before** either done arm is considered (`FE/components/ChatLaunchGreeting.tsx:69`: `hubStudy === 'done' && status.dispatch !== 'stale'`), and the P28 QA record states "Precedence is unchanged from the owner's O1 decision" (`qa/2026-09-30-guided-mode-deferred-gate-P28.md:190`). The spec's table puts solved-since above dispatch-stale ("edited > solved-since > dispatch-stale > done") and marks both rows unchanged. With tracking, both-true-and-not-edited is reachable only for a record with `edited_since_study: null` (pre-phase record, restored from an older pkl), so the practical effect is small — but it is a contract change to an owner decision and `test_…solvedState` currently pins the opposite order (the test "moved to the changed-since sentence", P28 record `:190`). Either keep O1's order (edited > dispatch-stale > solved-since > done) or state the change, add it to D-3 and name the test that moves.

### S-2. The capture window: the revision is read on the request thread, the copy is taken later in the worker

`start_eh_study` captures on the request thread (§1.3 "before the worker starts, so it names the network the private copy was taken from"); the private copy is taken in the worker, under `lock`, inside `run_eh_study` (`BE/services/adequacy/eh_study.py:1187-1189`). An edit that completes between the two — including an edit whose handler ran before the capture but whose B1 bump (after `call_next`, `main.py:1018`) lands after it — is **in** the copy and still reads `edited_since_study: true`. The direction is safe (over-report; the study measured the edited network and says re-run), so this is not a blocker, but the sentence "edited since the last study" is then true only in the sense "since the study was requested". Recommend: capture the revision at the copy, under the same lock, and write it into the record (`store["eh_study"]["network_revision"] = revision()` beside the `store.pop` loop at `eh_study.py:1181`, or return it with the report); or keep the request-thread capture and state the window and the "since the study started" semantics in `edited_since`'s docstring and the phase note. The test `…carries_the_revision…` (equality with `/undo/info`) is unaffected either way.

### S-3. Two facts in §1.1 are wrong and one anchor points at the wrong route

- "The stored report is cleared only by a foreground Solve" — the study itself clears `EH_REPORT_STORE_KEY` and its siblings when it starts (`eh_study.py:1181`, pinned by P31 S-1). No behavioural consequence (the `running` body hides it), but the table should say so.
- "Saved-snapshot restore … `:1574` area" — `projects.py:1574` is `save_project`'s `clear_undo` block. The restore is `BE/routers/snapshots.py:563` (`reset_network` at `:681`, `bind_project` at `:690`, `_restore_results_state` at `:745`); the counter assignment goes after `:745`.
- §2.1 lists the hydrate helper's callers as "cold activate, background solve"; it has four (`routers/projects.py:2483`, `services/solve_queue.py:1155`, `routers/deps.py:149`, `services/active_project.py:121`). The last one is the path (E2) actually exercises after the restart (the first request with a stored pointer), so name it.

### S-4. B2 placement must survive the tool timeout path

"After the handler returns or raises" does not cover the per-tool deadline: `future.result(timeout=PER_TOOL_TIMEOUT_SECONDS)` → `TimeoutError` → `tool_timeout` (`BE/services/chat_service.py:4779-4800`), and the comment there says the orphan may still mutate and land. Put the bump in a `finally` around the dispatch (or before the handler, the `mark_dirty` placement at `:4749-4751`, whose rationale — under-reporting costs the work, over-reporting costs a sentence — applies verbatim). §6's "raised before mutating → over-report" row then also covers timeouts.

### S-5. B3 double-counts clustering, and is otherwise unreachable

`set_network` has one production caller, `routers/clustering.py:309`, under `POST /api/network/cluster` — B1 (HTTP) or B2 (`cluster_network`, `TOOL_ROUTES` at `chat_tools_schema.py:2895`) already bumps it, so B3 makes one clustering `+2`. Harmless (the sentence is still true) but say so, and do not write a test that asserts `+1` on clustering. Alternatively drop B3 and keep `set_network`'s existing in-place record clear; the smoke scripts that call `set_network` directly (`BE/smoke/verify_*.py`) are not seams either way.

### S-6. R4's harness needs one more line to be deterministic

After step 4's fresh acquire the new heartbeat for X still meets `lockedByOther.has('X')` (the heartbeat branch "keeps today's rule"), so every tick until step 6 deletes it is a 409 that triggers another re-acquire and consumes `lockReplies`. With `shouldAdvanceTime` fake timers and `LOCK_HEARTBEAT_MS` no tick lands inside the 50 ms wait, so the test as written passes, but it is timing-dependent. Delete `'X'` from `lockedByOther` right after step 3's held send is observed (the heartbeat has already 409'd), and state it. R7 as written is sound; the `delete /projects/Y/lock` negative assertion scoped to `seen.slice(n)` correctly separates it from N-b.

### S-7. `test_done_route_equals_tool_and_stale_follows_the_stored_report` is `live_solve`

The one-line addition lands in a `@pytest.mark.live_solve` test (`tests/test_eh_review_route.py:95-96`). Row 2 runs it only where HiGHS is present; add the same `is False` assertion to the fake-run `done` case in the new file (the spec already builds `done` through a fake there) so the pin does not depend on a solver.

---

## Notes

- **N-1 Non-network study inputs are not tracked, by design — record it.** `PUT /api/simulation/solver_config` (`voll` feeds the EH ENS solve: `eh_study.py:629`, `:653`), `/finance`, `/commercial/value_flows`, `PUT /api/projects/{p}/stress_scenarios`, `/worksheet`, `/asset_health`, uploads and layout are not seams and never bump. The sentence says "network", so it stays true; add one row to §6 ("an input edit that is not a network edit under-reports") and, if wanted later, a separate `inputs_edited_since_study`. Do **not** widen the predicate to those routes: they are not Asset writes and the word would then be wrong.
- **N-2 False positives are the tolerable kind.** `PUT /api/network/meta` (title), carrier colours, `recalculate_lengths`, a no-op `_bulk`, an edit then undo — all say "edited". The remedy is one click and the sentence is true; the spec's §1.2 row and §6 cover it. Nothing bumps falsely from the study, the FMEA sweep or a Solve: `set_network` is not on the sweep's path (`assumptions.py` restores through `undo_actions` and the transient registry, never through a seam), `/api/simulation/*` and `/api/results/*` are outside `_UNDO_PREFIXES`, and no read-only POST exists under `/api/network/` or `/api/io/` on HEAD (`snapshots/weightings.csv` POST is an upload, `network_time_axis.py:232`).
- **N-3 Persistence is consistent on every writer.** `_write_meta` callers: `_save_context` (`projects.py:2111`, gets the key), template create (`:1444`, fresh project, no record yet — the carried-forward counter is written on the first save, harmless), scenario and rename (`:2855-2866`, `:3177-3192`) read-modify-write and keep unknown keys. Bundles, Saved snapshots and Scenarios copy both files (`_BUNDLE_FILES`, `:89`). A metadata without the key (older build, hand copy) hydrates to `0` while a restored record says `N` → `true`, the safe direction. `!=` rather than `>` is right because a Saved-snapshot restore moves the counter backwards together with the record it belongs to.
- **N-4 Concurrency.** `+= 1` from the event loop (B1) and an SSE thread (B2) can lose an increment; a lost increment cannot make the counter equal a captured value that a later edit should have moved away from, so no sentence turns false. `edited_since` reads an int without the lock — fine. Two tabs share one resident ctx and so one counter; the hook on each tab sees the same `/undo/info`. Multi-replica deployments have a per-process counter (the same blind spot as undo depth); say "sticky sessions assumed" in §6.
- **N-5 FE hook.** The single-`prev` design copied from `useStudyFinishedInvalidation` is right; the A → (edit on A from another tab) → B → A case is covered by react-query's refetch-on-mount with the default `staleTime: 0` on `useHubStudy` and the greeting query, not by the hook — worth a sentence so nobody adds a per-project map.
- **N-6 10a root cause: confirmed.** `reset_network` clears on `prev` (`pypsa_service.py:443-445`) before `dict(prev.solver_state)` (`:467`); `prev` is the resident registered ctx; `activate` is a pointer swap (`projects.py:2474-2476`). Copy-then-clear keeps the original purpose (the fresh ctx has no record; a study written after the swap lands in a copy) because `prev`'s record describes `prev`'s own network object, which the swap does not touch. The three `test_adequacy_study_scoping.py` guarantees (`:80-141`, `:143`, `:234-277`) still hold under the reorder.
- **N-7 Wording under every sequence** (given S-2's "since the study was requested"): edit then undo → "edited" (true, monotonic); edit during a running study → the greeting shows the running sentence until done, then "edited" (true); study then restart then activate → the saved counter against the saved record, both from one `_save_context` (true); a pre-phase record → `null` → the O1 sentence (claims nothing). D-2's two sentences are true in all of them.
- **N-8 Owner decisions.** D-1 (a) is sound and its honest alternative is honestly described. D-3 must absorb S-1. D-4 (a) is right for this phase. Add **D-5** (B-2 scope). Nothing listed as a decision should be an author's call.
- **N-9 Smoke feasibility.** `stop('uvicorn')` / `startBackend()` / `waitFor(health)` exist (`smoke-guided.mjs:2370-2373`, `:3157`); `PHASES` is at `:177` and the dispatcher at `:3167-3179` as cited. The P28 reorder removes P28's own edit step entirely (its `info` line about `stale=false` after an edit) — say so in the P28 phase note. (C4) depends on B-2 (a).
- **N-10 Sizing.** M still holds: B-1 adds XS to step 2, B-3 is XS in step 4, B-2 (a) adds a small step 0 (S) that steps 1 and 2 should wait for. The dependency line becomes "0 → 1, 2".

---

## Reproduction of B-2 (for the implementer)

With the suite's fixtures (`client`, `api_project`, `session_ctx`, `registry_key_for`), in `BE/`:

```
name = api_project("probe"); key = registry_key_for(name); ctx0 = session_ctx(client)
bus = client.get("/api/network/buses").json()[0]
client.put(f"/api/network/buses/{bus['name']}", json={**bus, "x": float(bus.get("x") or 0) + 2.5})   # 200
client.post("/api/network/undo")                                                                        # 200
client.get("/api/network/buses").json()[0]["x"]        # still x + 2.5
PyPSAService._contexts                                  # org:uuid → ctx0 (x + 2.5); scratch:<session> → new ctx (x), loaded_project == name
```

Run with the gate's row-1 command on one file; the probe was deleted after the run.
