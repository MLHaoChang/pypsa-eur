# Guided mode (deferred): P27a gate, backend data safety (A1, A8)

**Reviewer:** independent QA gate. **Date:** 2026-09-29. **Branch:** `claude/epic-allen-k2t1c4` at `e9abffe7d`. **Diff reviewed:** `4d7cfbb86..HEAD` (`2a2362c0b`, `1c1669833`, `9804f4427`, `e9abffe7d`).
**Contract:** `docs/superpowers/specs/2026-09-28-guided-mode-deferred.md` (§0, §1, §8, §9) and the plan's "P27a phase note".
I edited no source or test file. Probes and mutants ran only under `scratchpad/qa27a/`. No processes are left running (uvicorn, vite, stub and chromium count: 0).

## Verdict: NO-GO

Every automated row is green. The lock work (A1) and the rebind announcement (A8) are correct, and 13 of 16 mutants are killed. One blocker remains: the chat path does not match the HTTP path. The HTTP middleware refuses every `/api/network/*` and `/api/io/*` write during a live-network study. But at least eight write-tier chat tools that map to those same routes still change the live network mid-sweep. A1's promise is that "live-network studies refuse edits", and the assistant is the main edit path in Guided mode, so this has to be fixed. The fix is small, and master already has the same derived pattern in place.

## Blockers

### B1. Write-tier chat tools outside the CRUD handlers change the live network during a sweep, while their HTTP routes return 409

- **Where:** the chat chokepoint was added only to `services/network_crud.py:216,397,453`, `network_bulk.py`, `network_buses.py` (cascade and rename) and `network_global_constraints.py`. The chat tools below call their router handlers in process (`services/chat_tools.py:971` `update_meta`, `:997` `set_snapshots`, `:1004` `set_snapshot_weightings`, `:1038` `set_multi_period_snapshots`, `:1053` `set_investment_periods`, `:1091` `upload_timeseries`, `:1112` `generate_exemplary_timeseries`, `:1230` `upload_load_profile`). Those calls never meet `main.py`'s middleware and never reach a guarded handler.
- **Measured.** A live `fmea_sweep` record was installed on the ctx the tool resolves (the same harness as `test_live_network_untouched.py::_chat_refusal`). Each tool was called through `chat_tools.DISPATCHERS`, and the network was compared before and after:

  | tool (tier) | HTTP route (`TOOL_ROUTES`) | chat result during the sweep |
  |---|---|---|
  | `update_meta` (write) | `PUT /api/network/meta` | no error, network **changed** |
  | `set_snapshots` (write) | `POST /api/network/snapshots` | no error, **changed** |
  | `set_snapshot_weightings` (write) | `PATCH /api/network/snapshots/weightings` | no error, **changed** |
  | `set_multi_period_snapshots` (write) | `POST /api/network/snapshots/multi_period` | no error, **changed** |
  | `set_investment_periods` (write) | `POST /api/network/investment_periods` | no error, **changed** |
  | `upload_timeseries` (write) | `PUT /api/network/timeseries/{c}/{a}` | no error, **changed** |
  | `generate_exemplary_timeseries` (write) | service call | no error, **changed** |
  | `upload_load_profile` (write) | `POST /api/network/loads/upload_profile` | no error, **changed** |
  | `create_component`, `batch_create_components`, `create_carrier`, … | CRUD | 409 `study_in_flight` (correct) |

  Over HTTP, the same sweep record gives `POST /api/network/snapshots`, `PUT /api/network/meta` and `POST /api/network/investment_periods` a **409 `study_in_flight`** each (`qa27a/probe/test_probe_http.py`). By the same code path, `upload_generator_profile`, `upload_link_profile`, `set_investment_period_weightings`, `upload_snapshot_weightings_csv`, the vintage tools, `recalculate_line_lengths` and the routeless mutators `apply_demand_from_excel` / `reconstruct_network_from_image` are also unguarded. Their probes did not change my fixture only because of argument or fixture details (400 or no-op), not because of a guard.
- **Repro:** `qa27a/probe/test_probe_chat_writes.py` and `test_probe_chat_writes2.py`, run from `pypsa-gui/backend` with `-p tests.conftest --rootdir=.` and `PYTHONPATH` extended by the probe directory. Each failing case prints `PROBE <tool>: exc=None … changed=True`.
- **Why it blocks:** spec §1.1 "Not covered on purpose" justifies leaving these tools out because they "are destructive-tier and already go through `refuse_if_study_running` where they swap". That is true only for `import_*`, `cluster_network` and `delete_timeseries`. The eight tools above are **write**-tier and never swap. The same row also requires "a P27a test [that] enumerates the chat tools that reach a network write and asserts each is refused during a sweep". The shipped `_CHAT_WRITES` lists only the seven CRUD and undo tools. A `set_snapshots` or `upload_timeseries` that lands between a sweep's iterates is exactly the harm A1 exists to prevent: the study measures a plan the user never had. The HTTP and chat paths now disagree about the same operation.
- **Suggested fix (small):** master already solved "chat tools bypass the middleware" for the foreign-lock gate with a derived seam gate, `chat_tools._lock_gated_tool_names()` (`chat_tools.py:4853`, applied by the `DISPATCHERS.update` at `:4989`). It covers every non-read tool that maps to a write route under the gated prefixes, plus `_LOCK_GATE_SERVICE_CALL_MUTATORS`. Call `study_state.refuse_edit_during_live_study()` from that same wrapper, or from a sibling wrapper over the same derived set restricted to `/api/network/` and `/api/io/`. Then derive the test's parameter list from that set instead of the hand-written `_CHAT_WRITES`. That way a tool added later is covered on the day it lands. Note that `undo_last` would then get the dict before the swap string. The parametrised test already accepts that, since it checks only the 409.

## Non-blocking findings (fix with B1 or record)

1. **The call-site lock is not pinned by any test.** Mutants M13 (`sweep.py:418-419` calls `preserve_bus_topology(network)` / `freeze_capacities(network)` without the lock) and M14 (`frontier.py:211` without the lock) **survive**. They survive both `test_live_network_untouched.py`, `live_solve` included, and `test_adequacy_abort.py` / `test_adequacy_frontier.py`. The spy tests cover only the function. The contract row "Call sites … pass their captured `lock`" has no test. A structural pin like the `dtc` one (an AST check that the four live call sites pass a second argument) would close the gap. The same applies to `coupling_loop_runner.py:625` and `margin_loop_runner.py:962`.
2. **The global-constraint chokepoint is not pinned.** Mutant M16 (dropping `refuse_edit_during_live_study()` from `apply_update_global_constraint`) survives `test_live_network_untouched.py` and `test_chat_tools_dispatch.py`. The HTTP path is still covered by the middleware, so the risk is chat-only. It would be covered by B1's derived test.
3. **A8 misses one rebinding case: the network imports.** `import_network_nc`, `import_csv_bundle`, `import_excel` and `import_matpower` go through `io._reset_with_ts_clear` → `reset_network`, which moves `loaded_project` from `'bound-proj'` to `None` (measured: `qa27a/probe/test_probe_rebind.py`). None of them is in `PROJECT_REBINDING_TOOLS` (`chat_service.py:133`). So a second tool in the same turn is refused with `project_switched_mid_turn`, and the frontend keeps the old name. This is the same class of bug as A8, but outside the contracted three tools and already present on master. Record it for P27b or P32. The frame would carry `to: null`; check whether `ChatPanel`'s handler accepts that.
4. **A time-of-check to time-of-use window.** The gate reads the study record without the lock (both in the middleware and in `refuse_edit_during_live_study`). An edit that passes the check in the few microseconds before a sweep publishes its record can still land under the lock between the sweep's start and its first solve. The window is tiny and was not introduced here, since the solver gate has the same shape. Record it.
5. **A misleading smoke log line.** `scripts/smoke-guided.mjs:1998` prints `ok  the browser edit landed while the sweep was running`, which is the *failure* text of `check(refused, …)`. The assertion itself is right. Reword it so the log does not read as the opposite.
6. **A fallback when binding fails.** If `bind_request_context` raises (it is swallowed at `main.py`), the middleware's `get_solver_state()` falls back to the process foreground ctx. A user's edit could then be refused because of another project's sweep. This is an edge case, and the handler-level check uses the endpoint binding. Record it.

## Deviations judged

| # | Deviation | Judgement |
|---|---|---|
| 1 | Extra chokepoints: `cascade_delete_bus`, `bulk_update_components`, bus rename, global constraints | **Justified and necessary.** These handlers do not route through the three `network_crud` functions, and without them the spec's own parametrised test stays red. The cascade and bulk ones are pinned (M7 and M8 killed); the GC one is not (finding 2). The same reasoning, applied to the time-series and snapshot handlers, is B1. |
| 2 | Stub 7b `{"component_class":"Bus"}` | **Justified.** `list_components` requires the class; `{}` would return a `tool_error`, the opposite of what the smoke asserts. Pinned by `test_stub_openai_endpoint.py`. |
| 3 | The Properties toast shows "Request failed with status code 409" | **Accepted as a P27b deferral.** `QUIET_TOAST_CODES` already holds `study_in_flight` on master, and the card's own `onError` shows the axios text. Exactly one toast appears (smoke screenshot 11). P27b (A1-FE) must bring the sentence. |
| 4 | The seam test asserts the sentence is *contained* in the `tool_error` message | **Acceptable.** The frame's `message` is `str(detail)`, set by the pre-existing `_redact_for_log` path, and `error_kind` is lifted correctly. The model-facing `tool_result` starts with `study_in_flight` and carries the sentence, and the test asserts both. |
| 5 | A structural `dtc` pin | **Justified.** `dtc` has a `lock` in scope, so passing it is no signature error. The AST pin kills M15. |
| 6a | Swap-guard test reads `detail.message` when the detail is a dict | **Justified.** For live-network keys, `POST /api/network/reset` now meets the middleware first (spec §1.1 states this for undo; reset has the same prefix). The label and "abort it" are still asserted, and `mc` / `eh_study` still reach the string. |
| 6b | `dispatch` becomes `none` after the post-sweep edit (the spec said "reads as before") | **Justified.** `_DISPATCH_INVALIDATE_PREFIXES = ("/api/network/",)` invalidates dispatch on any write, with or without a sweep. The replacement assertion (every other bus, link and generator row is unchanged) is stronger. |
| 6c | F1b stubs take `(n, lock=None)` | **Justified.** This follows mechanically from the new call signature. |
| 7 | Smoke adjustments (the mid-sweep row shows the solve's `control`; the rebind is read from the toast) | **Justified.** This is P22.9 bug 3 behaviour, and the full equality is asserted after the sweep. The dock follows the rebind to an empty chat. |
| 8 | Extra top-level `code` assertion | **Good.** It proves the middleware answered, not the handler, which kills M4. |

## Safety and design checks

- **Can a refusal block the restore or an abort?** No. The restore runs in process (`preserve_bus_topology`'s `finally`, `_restore_base`), and nothing under `services/adequacy/` calls a guarded handler (grep). Every study abort is under `/api/results/*/abort` and `/api/simulation/abort`, outside `_STUDY_EDIT_PREFIXES`. `abort_adequacy_study` does not touch `network_crud`.
- **Locks and deadlock.** The write-back takes the runner's captured `mutation_lock` (a per-ctx `RLock`, captured at request time in all four runners). It is held only for a few `loc[]` writes and one `SubNetwork` removal. `topology.close()` in the coupling and margin runners runs **outside** `solver_state_lock`, so there is no lock-order inversion. The middleware check and `refuse_edit_during_live_study` never take the mutation lock, so they cannot deadlock with a worker's `with lock`.
- **`LIVE_NETWORK_STUDIES`.** Correct. `mc` snapshots inputs under the lock and its worker never touches `n` (`mc_loop_runner.py:126-160`). `eh_study` solves `network.copy()` taken under the lock (`eh_study.py:1189`, `_private_copy` `:554`). `fmea_sweep`, `frontier`, `coupling_loop` and `margin_loop` all solve the live `n`. M3 is killed.
- **A8.** The frame fires only on an actual move (M12 killed). Save-a-Copy emits nothing, because `save_project_a_copy` is not in the set and the binding does not move. The same-turn second tool dispatches with the real closure (smoke: `call_stub_7b` returned a `tool_result`, and there was no `project_switched_mid_turn`). Adding `save_project` could absorb an external switch only in the window during the save call itself, since the guard runs before each dispatch. That is acceptable. Missing from the set: the four network imports (finding 3).
- **Master's security.** Unaffected. The study gate only adds refusals. It sits before the foreign-lock gate, and both return 409, so nothing bypasses it. It reads the session-bound ctx. The `/activate` exemption, CSRF, auth and the ownership binding are untouched.
- **Scope of the route enumeration.** I enumerated every mutating route. Covered by the prefix: all `routers/network.py`, `network_time_axis.py`, `network_profiles.py`, `clustering.py`, `vintage.py` and `io.py` writes. Justified exclusions: `/api/projects/*` (save and activate have their own study guards; import, template, load and snapshot-restore refuse via `refuse_if_study_running`; the scenario fork copies on-disk files; layout, worksheet, uploads and members write sidecars only); `/api/simulation/run|preflight|run_ac_pf` (`blocking_study_detail`); the solve queue (its own `running_study_key` check); gridspine (no live-network writes). **Not covered:** the chat tools in B1.

## Evidence

| Row | Command (cwd) | Result |
|---|---|---|
| 1 | full backend suite | run by the orchestrator (not repeated here) |
| 2 | the 14-file set + `tests/test_chat*.py` (55 files) + `test_chat_tool_dispatch_loop_seam.py`, `test_save_guards_seam.py`, `test_adequacy_study_swap_guard.py`, `test_adequacy_swap_guard_callsites.py`, `test_stub_openai_endpoint.py`, `test_adequacy_abort.py`, `test_live_network_untouched.py` (`pypsa-gui/backend`, the §0.2 flags) | **1584 passed, 19 skipped**, exit 0 (`qa27a/row2.log`; file list `qa27a/row2.files`). P27a files alone with `-rs`: 111 passed, 0 skipped. |
| 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`) | exit 0 |
| 4 | `npx vitest run` (`pypsa-gui/frontend`) | 2611 passed, 1 failed: `BottomPanel.test.tsx` "select-all past the cap", which timed out at 5000 ms while the row-2 backend suite was loading the machine. Re-run alone 3 of 3 times: 79/79. There is no frontend diff in this phase (`git diff … -- 'pypsa-gui/frontend/src/**'` is empty), so this is flakiness under load, not a regression. |
| 5 | `smoke-guided.mjs --phase P27a` | **PASS**, 14 screenshots (`qa27a/p27a/`). 409 `study_in_flight` on `PUT` during the sweep; one toast; the edit did not land; after the sweep the row equals the pre-sweep row and the edit gives "Bus updated"; branch 7: card, `Active project: p27a-…`, `meta.loaded_project` matches, `call_stub_7b` got a result, no mid-turn switch, Ctrl+S → 200, no 409 in the console. I read screenshots 11–14; they match. |
| 5 | `--phase P26`, `--phase P25` | **PASS** (38 and 12 screenshots) |
| 7 | frontend `uiMode` diff | none (no frontend diff) |

**Mutations** (`qa27a/mutate.py`, on a scratch copy of the backend; log `qa27a/mutations.log`):

| Mutant | Result |
|---|---|
| M1 topology write-back not under the lock | killed |
| M2 freeze undo not under the lock | killed |
| M3 `LIVE_NETWORK_STUDIES` = all `STUDY_KEYS` | killed |
| M4 middleware study gate dropped | killed |
| M5 edit prefixes widened to `/api/projects/` | killed |
| M6 `network_crud` chokepoint made a no-op | killed |
| M7 bulk chokepoint removed | killed |
| M8 cascade chokepoint removed | killed |
| M9 / M10 / M11 `save_project` / `create_project_from_template` / `import_project_bundle` dropped from the set | killed / killed / killed |
| M12 frame emitted unconditionally | killed |
| M13 sweep call site drops the lock | **survived** (finding 1) |
| M14 frontier call site drops the lock | **survived** (finding 1) |
| M15 `dtc` passes a lock | killed |
| M16 GC update chokepoint removed | **survived** (finding 2) |

**Probes:** `qa27a/probe/test_probe_chat_writes.py`, `test_probe_chat_writes2.py` (B1), `test_probe_http.py` (HTTP parity), `test_probe_rebind.py` (finding 3).

## To reach GO

1. Fix B1: a derived, seam-level study gate for the chat write tools, and a derived parametrised test (it should fail today on `set_snapshots`, `upload_timeseries` and the others).
2. Recommended in the same change: an AST pin for the four live call sites passing `lock` (finding 1).
3. Re-run rows 1, 2 and 5 (P27a).
