# Guided mode P33b — study freshness after an edit: implementation spec (OPEN-ITEMS 10b, 10a, 10c)

**Date:** 2026-10-06. **Plan entry:** [`../plans/2026-09-28-guided-mode-deferred.md`](../plans/2026-09-28-guided-mode-deferred.md) §"P33b plan (2026-10-06)". **Parent spec:** [`2026-09-28-guided-mode-deferred.md`](2026-09-28-guided-mode-deferred.md) — its §0 conventions and §0.2 integration gate apply unchanged; §3 (P28) holds the greeting and Results-card contracts this phase extends. **Origin:** P28 gate owner decision O1 (soften now, track later), the P28 re-gate's unpinned mutants R4 / R7, and the P28 phase note's contract drift 3. **Open items closed:** [`../OPEN-ITEMS.md`](../OPEN-ITEMS.md) 10b, 10a, 10c. **Opened:** OPEN-ITEMS 12 (the `/api/io/import/*` session pointer, found while scoping D-5).

**Amended 2026-10-06** after the independent review [`../qa/2026-10-06-guided-p33b-spec-review.md`](../qa/2026-10-06-guided-p33b-spec-review.md) (APPROVE WITH CONDITIONS): blockers B-1 / B-2 / B-3 and should-fixes S-1 … S-7 are applied, the owner decisions D-1 … D-5 are recorded in §8, and §9 maps every finding to its change. The amendment adds **step 0** (§0a: HTTP undo and Saved-snapshot restore re-key the session's context) on which the undo claims in §1 and §2 build. Every anchor the review cited was re-read on `bb8b2e7b8`; two reproductions in §0a were re-run there with throw-away probes on the suite's fixtures and deleted.

`FE` = `pypsa-gui/frontend/src`, `BE` = `pypsa-gui/backend`. Every anchor was checked on `c99c255f3` (`origin/master`, 2026-10-06) and, for the amended passages, on `bb8b2e7b8`. No code was edited for this spec. Vocabulary is `pypsa-gui/CONTEXT.md`'s: *Solve*, *Asset write*, *Component*, *Context* (`ctx`), *Saved snapshot* / *State capture*, *Guided* / *Expert mode*, *Unavailable*.

## 0. Conventions and scope

- Deferred spec §0.1 applies: test-first, additive backend fields only, **no new backend module** (`smoke/check_bundle.py` is the gate), no change to `chat_tools_schema.TOOLS`, tool descriptions or the system block; Expert unchanged (snapshots byte-for-byte); sentences, no engine ids, no "0" for a missing number; every gate row names its command and cwd.
- Deferred spec §0.2 is the gate, with the additions in §5 here.
- The honesty rule from the P28 gate governs every sentence in this phase: **a sentence states what its signal carries, not what it suggests.** B1 (a "changed since" sentence on a "solved since" signal) and R-1 (a "solved since" sentence on an "edited since" signal) were both this class. The signal this phase adds is a monotonic edit counter, so its sentences say "edited since", never "differs from" (§1.2).
- One backend source for both surfaces: the greeting (`FE/components/ChatLaunchGreeting.tsx`) and the Hub design Results card (`FE/pages/hubDesign/cards/ResultsCard.tsx`) read the same two fields from the same two routes, and the chat tool `review_eh_study` returns the same body as `GET /api/results/eh_review` (it already does: `BE/services/chat_tools.py:2823-2835` calls `routers.results.get_eh_study()` then `eh_review.review_latest`).

## 0a. Step 0 (D-5) — HTTP undo and Saved-snapshot restore must land in the context later requests read

**The defect (review B-2, reproduced).** Inside a session-bound request (`BE/main.py:769-775` binds `_request_ctx`, `_request_slot` and `_request_scratch`), every in-place replace goes `reset_network()` → `_publish_active(ProjectContext(...))`. `_publish_active` routes the **new, unbound** ctx by its own identity — `slot = ctx.registry_key or cls._request_scratch.get()` (`BE/services/pypsa_service.py:287-298`) — so it lands in `scratch:<session>`. The caller then stamps identity back with `set_binding` → `bind_project` (`:1193-1222`), which writes `loaded_project` / `org_id` / `project_uuid` / `storage_dir` and **never re-keys**: `rekey_context` (`:251-272`) has exactly two callers, the first save of a draft (`BE/routers/projects.py:1977`) and rename (`:3208`). The project's `org:uuid` slot keeps the **pre-replace** ctx, and that is what `resolve_for_session` hands the next request (`BE/services/active_project.py:103`, `get_context(key)`).

Reproduced on `bb8b2e7b8` with the suite's `client` / `api_project` / `registry_key_for` fixtures (probe deleted afterwards):

| Sequence | `GET /api/network/buses` afterwards | `PyPSAService._contexts` afterwards |
|---|---|---|
| `PUT /api/network/buses/{b}` x → x+2.5 (200) → `POST /api/network/undo` (200) | **x+2.5** | `org:uuid` → pre-undo ctx (x+2.5, bound); `scratch:<session>` → the re-imported ctx (x, bound to the same project) |
| Saved snapshot taken → `PUT` x → x+2.5 → `POST /api/projects/{p}/snapshots/{id}/restore` (200) | **x+2.5** | `org:uuid` → pre-restore ctx; `scratch:<session>` → the restored ctx, bound to the same project |

No existing test reads the network back after an HTTP undo or restore under sessions (`BE/tests/test_unsaved_results.py:42-50` and `test_chat_edits_are_captured.py` assert depth; `test_active_pointer_paths.py:109-122` asserts the pointer). The no-session path (`cls._active`) works, which is why the legacy tests pass; the smoke backend runs with sessions, so §5 (C4) hits this.

**Which `reset_network` callers have it** (all seven on `bb8b2e7b8`, read and classified):

| Caller | Lands where | Verdict |
|---|---|---|
| `apply_undo` (`BE/services/network_undo.py:129-134`; also the chat tool `undo_last`, `chat_tools.py:4000` → `routers.network.undo_last` → the same function) | scratch, bound to the same resident project | **step 0** |
| Saved-snapshot restore (`BE/routers/snapshots.py:681-695`, `bind_project` at `:690`; also the chat tool `restore_project_snapshot`) | scratch, bound to the same resident project (or to another project whose `org:uuid` ctx is resident) | **step 0** |
| `load_project` (`BE/routers/projects.py:2656`) | scratch, then `PyPSAService.register(registry_id, ctx)` at `:2793` puts it under `org:uuid` | correct today |
| `import_bundle` (`:1083`), `create_from_template` (`:1402`) | scratch, bound to a **fresh** row that was never resident; the pointer moves (`:1098`, `:1416`); the next request hydrates a second copy from the files the handler just wrote | not lossy (disk equals memory at that instant); out of scope, noted in OPEN-ITEMS 12 |
| `POST /api/network/reset` ("New", `BE/routers/network.py:623`) | scratch, unbound; the pointer is cleared (`:633-635`) so the scratch ctx is the one read | correct today |
| `/api/io/import/{netcdf,csv,excel,matpower}` (`BE/routers/io.py:191` `_reset_with_ts_clear`) | scratch, unbound; the pointer is **not** cleared, so the next request reads the project's resident ctx — reproduced: a 7-bus netcdf imports with 200 and the next `GET /api/network/buses` returns the project's 1 bus | a different class (the "New" rule, not re-keying): **OPEN-ITEMS 12**, not step 0 |
| the refusal branch inside `load_project` (`:2791`) | scratch, unbound, on purpose | correct today |

There is no redo (`grep -rn redo BE/services BE/routers` finds none).

**The fix (one line at each of the two sites, inside the lock, after the binding is put back):** `PyPSAService.rekey_context(PyPSAService.get_active_context())`. `rekey_context` moves the ctx into the slot its identity implies, pops every other slot that held it (the scratch entry) and re-points `_request_slot`. An unbound ctx (undo on a never-saved draft) has `registry_key is None` and the call is a no-op, which is the right answer: the draft stays in scratch. Outside a session (`cls._active`) the call also registers the new ctx under `org:uuid`, replacing a stale entry `load_project` left there — harmless and more correct. The replaced pre-replace ctx is not written back to disk (unlike `register`'s displaced-context save): it is the same project and the replacement **is** its current state. `reset_network` already carries `solver_state` (as a copy), the undo stack, both locks and `chat_state` forward, so nothing else moves.

**Why step 0 comes first.** Without it (i) §2.2's undo rule restores the record onto a ctx no later request reads, while — with 10a(a) alone — the pre-undo ctx keeps its record anyway, so the rule is both ineffective and unnecessary; (ii) B1 bumps the request's active ctx (the new one), so the project's counter is not bumped and `/undo/info` on the next request reads `rev0 + 1`, not `+ 2`; (iii) (C4) cannot pass. With it, the undo rule is needed again (the re-keyed ctx is a fresh copy whose finished keys 10a(a) cleared), the `+2` assertions hold, and the undo a Guided user actually performs undoes.

**Red tests — `BE/tests/test_undo_rekeys_the_session_context.py` (new; `client`, `api_project`, `registry_key_for`, `session_ctx` from `BE/tests/conftest.py:452-560`):**

| Test | Asserts |
|---|---|
| `test_an_http_undo_is_read_by_the_next_request` | the probe above as a test: `PUT` → `POST /api/network/undo` 200 → `GET /api/network/buses` reads the pre-edit `x`; `PyPSAService._contexts[key]` is the ctx `session_ctx(client)` resolves to; no key other than `key` maps to a ctx whose `loaded_project` is the project (one ctx per project) |
| `test_a_chat_undo_is_read_by_the_next_request` | the same through `_dispatch("undo_last", {})` (`test_chat_edits_are_captured.py:54-60`) |
| `test_a_saved_snapshot_restore_is_read_by_the_next_request` | snapshot → `PUT` → restore 200 → `GET` reads the snapshot's `x`; registry holds one ctx for the project |
| `test_a_restore_of_another_resident_project_rekeys_under_that_project` | A active, B resident with a snapshot → restore B's snapshot → `_contexts[key_B]` is the restored ctx, `_contexts[key_A]` untouched, the pointer is B (`test_active_pointer_paths.py:109` stays green) |
| `test_an_undo_on_an_unbound_draft_stays_in_scratch` | `POST /api/network/reset` → an edit → undo → the ctx is still under `scratch:<session>` and no `org:uuid` entry appeared |

**Mutants:** drop the `rekey_context` call in `apply_undo` → the first two tests; drop it in `restore_snapshot` → the third and fourth; call `rekey_context` before `set_binding` (key still `None`) → all four.

**Size:** S (two one-line edits, one test file). Steps 1 and 2 wait for it (plan: 0 → 1, 2).

## 1. 10b — an edit after a finished Energy Hub study (the P33b marker)

### 1.1 Facts found in the tree

| Fact | Anchor |
|---|---|
| The EH review's `stale` is true only when the stored report is gone and the record's copy was reviewed; nothing records an edit. | `BE/services/adequacy/eh_review.py:436` `_STALE_SOURCE`, `:455-466` |
| The stored report is cleared by a foreground Solve **and by the study itself when it starts** (it pops `EH_REPORT_STORE_KEY` and its siblings; pinned by P31 S-1). The `running` body hides the second, so only the Solve produces a visible `stale`. | `BE/routers/simulation.py:1166` `eh_reference_design_report=None` (inside `POST /run`, `:1042`); `BE/services/adequacy/eh_study.py:1180-1182` |
| The study record is created with `started_at` and no network identity; the worker updates it in place at the end. | `BE/services/adequacy/eh_study_runner.py:340-358` (record), `:398-411` (`record.update(...)`), `:419` `publish_study` |
| The EH study solves a **private copy** taken under the ctx lock; the user's network is never written by the study, so no "closing restore" touches it. | `BE/services/adequacy/eh_study.py:1187-1189` (`network = network.copy()` under `lock`); `BE/services/project_context.py:265-273` (`LIVE_NETWORK_STUDIES` excludes `eh_study`) |
| Transient rows are a per-ctx registry the solver fills during one Solve and empties in its restore; `dispatch_status` ignores them so a Solve does not read as an edit. | `BE/services/project_context.py:131-132`; `BE/services/dispatch_status.py:77-100` |
| **No network revision counter exists.** The only "changed" signals are the per-ctx undo stack (`_UndoState`, depth = undoable edits since the last save) and `results_unsaved` (memory differs from disk). Neither survives a save, and neither is comparable to a point in time. | `BE/services/project_context.py:151`, `:160`; `BE/services/dirty_state.py:43-68`; `BE/services/undo_service.py:107-190` |
| HTTP edits all pass one chokepoint: `undo_snapshot_middleware` pushes a State capture for every write under `/api/network/` and `/api/io/` except `/api/network/undo` and `/undo/info`, pops it on a 4xx/5xx, and clears stale dispatch after a 2xx. The chat seam bypasses it. | `BE/main.py:104-107` (`_UNDO_PREFIXES`, `_UNDO_EXCLUDE`), `:667` (def), `:966-990` (`should_snapshot`, push), `:1018-1025` (`call_next`, pop), `:1038-1060` (dispatch clear) |
| Chat edits call the handlers directly; the single dispatch site marks the project dirty for every non-`read` tool (over-marking by design). | `BE/services/chat_service.py:4731-4751` |
| Which chat tool hits which route is a maintained map, pinned by tests. | `BE/services/chat_tools_schema.py:2839` `TOOL_ROUTES`; `BE/tests/test_chat_tools_endpoint_map.py` |
| Undo is meant as a same-project in-place replace: `reset_network()` then re-import, then the binding is put back — but under session routing the re-imported ctx lands in `scratch:<session>` and is never re-keyed, so the next request reads the pre-undo ctx (§0a; step 0 fixes it, and every undo claim below assumes step 0). | `BE/services/network_undo.py:92-146` (`:129` reset, `:134` `set_binding`); `BE/services/pypsa_service.py:287-298`, `:251-272` |
| Clustering replaces the network in place through `set_network`, which clears finished study records. | `BE/services/pypsa_service.py:492-546` (`:511-513` clear) |
| A live-network study refuses edits (P27a); the EH study does not block them, which is why an edit during or after it is possible at all. | `BE/main.py:323` `_STUDY_EDIT_PREFIXES`, `:844-856` |
| `/api/network/undo/info` is polled every 3 s by three always-mounted readers in both modes, on one query key. | `BE/services/network_undo.py:68-91`; `FE/layout/AppHeader.tsx:320-325`, `FE/components/StatusBar.tsx:41-46`, `FE/layout/Sidebar.tsx:1090-1095` |
| After a finished hub study nothing polls `/simulation/status` (StatusBar polls it only while `status` is `completed`/`failed`, and the study leaves the live status `idle`); `eh_study` is polled only while `running`. So the FE cannot learn of an edit without a trigger. | `FE/components/StatusBar.tsx:64-70`; `FE/pages/hubDesign/useHubData.ts:23-34`; `FE/pages/results/ehStudyPoll.ts` |
| The Expert EH panel shows no `stale` state at all; the word appears only in its invalidation set. | `FE/pages/results/EhReferenceDesignPanel.tsx:898-912` |
| Greeting precedence today (Guided, hub `done`, dispatch not `stale`): `reviewStale` → "was solved since"; else the O1 done sentence. Dispatch `stale` falls through to the pre-P28 "changed since" sentence (R-1). | `FE/components/ChatLaunchGreeting.tsx:59-102`, `:142-150` (record query), `:162-168` (review query, `enabled` on `done`) |
| Results card: one banner, from `review.stale === true`. | `FE/pages/hubDesign/cards/ResultsCard.tsx:19-20`, `:41-46` |

### 1.2 Design decision: a per-ctx revision counter, not a content fingerprint

**Chosen: `ProjectContext.network_revision: int`, bumped at the edit seams, captured on the study record at start, compared at read time.**

| Criterion | Counter | Content fingerprint (hash of the static tables) |
|---|---|---|
| False positive on the study's own run | none: the study takes a private copy and never crosses a seam | needs an exclusion for every column a Solve touches on the copy; zero risk only if the hash is taken on the copy, which is what the counter gets for free |
| False positive on the solver's transient rows / closing restore | none: a Solve is under `/api/simulation/`, not a seam | must subtract the transient registry at hash time, and the registry is emptied in the restore, so a hash taken mid-restore can differ (`dispatch_status.py:77-100` exists because of exactly this) |
| Cost | one integer increment | a hash of every static DataFrame on every read of `/eh_study` and `/eh_review` (the greeting reads both on every mount); or a cached hash with its own invalidation, which is a counter again |
| An edit that is then undone | reads as edited (monotonic) — the sentence says "has been edited since", which is true | reads as unchanged, which is also true but requires the hash |
| Survives save / reload | yes, if persisted in `metadata.json` and restored on hydrate (one field) | yes, inherently — but only once the record is persisted, which is the same condition |
| Catches an edit that bypassed every seam (a hand-edited `network.nc`, a future route under a new prefix) | no (same blind spot as the undo stack and the dirty flag, and as the foreign-lock gate's prefix list — OPEN-ITEMS 6) | yes |

The counter wins on the two "must not false-positive" conditions in the brief, on cost, and on being the same kind of thing the codebase already keys "unsaved work" on. Its one weakness (an edit through a path that is not a seam) is an existing class, recorded in §6 with a test that fails when `TOOL_ROUTES` gains a write to a `/api/network/` path the chat predicate does not classify.

**Naming.** The record field is `network_revision` (the captured value); the derived boolean on both routes is **`edited_since_study`**, not the plan's placeholder `network_changed`: the signal is "an edit seam was crossed", and a field named after a stronger claim is how B1 happened. `/undo/info` carries the current `network_revision`.

### 1.3 Backend contract

**Where the counter lives.** `BE/services/project_context.py` `ProjectContext` gains `network_revision: int = 0` (declared beside `results_unsaved`, same "plain int under the GIL" rule; **carried forward** by `reset_network`'s and `set_network`'s `_publish_active(ProjectContext(...))` constructor calls exactly as `undo` and `solver_state` are — the counter belongs to the project, not to one network object; a fresh unbound ctx starts at 0). `BE/services/dirty_state.py` gains `bump_revision(ctx=None) -> int` and `revision(ctx=None) -> int` (the module already owns the per-ctx "memory changed" family; no new module).

**Bump sites** (an edit is anything the product already treats as an undoable or dirtying change to the network):

| # | Seam | Rule | Anchor |
|---|---|---|---|
| B1 | HTTP | after `response = await call_next(request)`: `is_write and 200 ≤ status < 300 and path.startswith(_UNDO_PREFIXES) and path != "/api/network/undo/info"` → `dirty_state.bump_revision()`. `/api/network/undo` **is** bumped (it is an edit; it is in `_UNDO_EXCLUDE` only because it must not push a capture of itself). A refused edit (4xx) and a read never bump. | `BE/main.py:1018-1025`, beside the pop |
| B2 | chat | at the single dispatch site, **before the handler runs, in the same `if tier != "read"` block as `mark_dirty`** (`:4749-4751`), for tools for which `chat_tools_schema.tool_edits_network(name)` is true. The predicate is `TOOL_ROUTES`-derived **or** membership in one hand list, for the tools that have no route: `tool_edits_network(name) = any(method != "GET" and path.startswith(_UNDO_PREFIXES) for (method, path) in TOOL_ROUTES[name]) or name in _SERVICE_CALL_NETWORK_WRITES`, with `chat_tools_schema._SERVICE_CALL_NETWORK_WRITES = frozenset({"batch_create_components", "batch_delete_components", "generate_exemplary_timeseries", "apply_demand_from_excel", "reconstruct_network_from_image"})` (the five `_SERVICE_CALL` write/destructive tools that mutate the live network: `chat_tools.py:974-1045`, `:1047`, `:1273`, `:4287`, `:4553`) and `_SERVICE_CALL_NON_NETWORK_WRITES` for the other seventeen (`gridspine_*` ×7 — `gridspine_service.py:467-470` opens its own `pypsa.Network` from disk — `export_*` ×6, `set_active_profile`, `clear_uploads`, `start_campaign`, `end_campaign`). Both lists are pinned by construction (§1.5): every `_SERVICE_CALL` tool whose tier is `write` / `destructive` must be in exactly one. Placement before the handler covers a handler that raises **and the per-tool timeout** (`future.result(timeout=…)` → `tool_timeout`, `:4779-4800`, whose comment says the orphan may still mutate and land); the `mark_dirty` rationale applies verbatim — under-reporting costs the truth of a sentence, over-reporting costs a re-run of a study that was already stale in the user's mind. `run_eh_study`, `run_simulation`, `review_eh_study`, `get_adequacy_results` do not qualify; `update_component`, `delete_component`, `undo_last`, the bulk, import and batch tools do. | `BE/services/chat_service.py:4749-4751`; `BE/services/chat_tools_schema.py:2792` (`_SERVICE_CALL`), `:2888-2889`, `:2912`, `:3022`, `:3025` |
| B3 | in-process replace | `PyPSAService.set_network` bumps the new ctx's counter (`prev.network_revision + 1`). Its finished-study clear stays (clustering replaces the measured network; §2.2 explains why `reset_network` is different). Its one production caller is `POST /api/network/cluster` (`BE/routers/clustering.py:309`), which B1 (HTTP) or B2 (`cluster_network`, `TOOL_ROUTES` `:2895`) already bumps, so **one clustering is `+2`** — harmless (the sentence stays true) and no test asserts `+1` on clustering. B3 is kept for the direct in-process callers (`BE/smoke/verify_*.py`, tests) so "every replace of the network object bumps" has no exception to remember. | `BE/services/pypsa_service.py:511-546` |
| — | Solve, study, queue, save, activate, layout, uploads | never bump: none is under a seam prefix. `POST /api/results/eh_study` and `/api/simulation/run` are pinned by tests as non-bumping. | — |

**Capture.** `start_eh_study` (`BE/services/adequacy/eh_study_runner.py:340-358`) publishes the record with `"network_revision": None`; the **worker** (`:361`, the first lines of `worker()`) writes `record["network_revision"] = dirty_state.revision()` **under `lock`** (the ctx's mutation lock, the same object `run_eh_study` takes for `network.copy()` at `eh_study.py:1187-1189`) immediately before calling `run_eh_study`. One site serves both callers (`POST /api/results/eh_study` at `BE/routers/results.py:1419-1436` and the chat tool `run_eh_study` at `BE/services/chat_tools.py:3008`, which calls that route handler); the worker runs under `_ctx.run(worker)` (`:416`), so `dirty_state.revision()` resolves the request's ctx there exactly as on the request thread.

*Why not on the request thread (review S-2):* an edit completing between a request-thread capture and the copy — including an edit whose handler ran before the capture but whose B1 bump (after `call_next`, `main.py:1018`) landed after it — is **in** the copy and still reads `edited_since_study: true`. *Why not inside `run_eh_study` beside the copy:* the suite's `fake_run` monkeypatch replaces `run_eh_study` (`test_eh_review_route.py:23-53`), so a capture there would make every fake-run record read `null`. The worker-side capture under the same lock leaves one window — between the worker releasing `lock` and `run_eh_study` re-acquiring it for the copy — that is microseconds wide and, like the request-thread window, only over-reports (the study measured the edited network and says re-run). **Correction (P33b gate N-2, 2026-10-06):** that holds for the HTTP seam (B1 bumps after the handler), not for the chat seam. B2 bumps **before** the handler, so a study whose worker captures and copies between a chat tool's bump and the handler taking the mutation lock records captured == current with a pre-edit copy, and then reads `false` for an edit it did not measure — an **under**-report. The window is the gap between the dispatch-site bump and the handler acquiring the lock (milliseconds), and needs an HTTP-started study to begin during a chat edit. It is left open by decision: closing it with a second bump after the handler would change every chat edit to +2 and still not cover a timed-out orphan, whose mutation can land after `tool_timeout`. Recorded as a known limitation in the P33b phase note. The sentence's claim is therefore "edited since the study **started**"; the record's `started_at` is the point in time it names. The equality test `…carries_the_revision…` (§1.5) is unaffected; `…an_edit_during_a_running_study_reads_edited` pins the direction.

**Read time.** `BE/services/study_state.py` gains `edited_since(record, *, revision=None) -> bool | None`: `None` when the record is not a dict, has no `network_revision`, or is `running`; otherwise `revision_now != record["network_revision"]`. Unavailable is `null` on the wire (ADR-0001): a record from before this phase, or restored from an older `results_state.pkl`, says nothing rather than `false`.

| Route / body | Addition | Anchor |
|---|---|---|
| `GET /api/results/eh_study` | `edited_since_study: bool \| null` on the wire dict (the record keeps `network_revision` too; it is a plain int and ships). `thread` / `stop_event` are stripped as today. | `BE/routers/results.py:1388-1401` |
| `GET /api/results/eh_review` and `review_eh_study` | `review_latest` copies `record.get("edited_since_study")` (default `None`) to `out["edited_since_study"]` beside `stale`. `stale` and `source` are unchanged; the two booleans are independent (both can be true). The `running` and `no_data` bodies are unchanged. | `BE/services/adequacy/eh_review.py:440-467`; `BE/routers/results.py:1708-1734` |
| `GET /api/network/undo/info` | `network_revision: int` (the current counter; the FE's refresh trigger, §1.4). Existing keys unchanged. | `BE/services/network_undo.py:68-91` |

**Persistence** (needed so a saved project re-opened later reads honestly — §2.2 persists the record; the counter must travel with it):

| Where | Rule | Anchor |
|---|---|---|
| `metadata.json` | `_save_context` writes `"network_revision": ctx.network_revision`. | `BE/routers/projects.py:2111-2125` |
| hydrate — the helper has **four** callers: cold activate (`BE/routers/projects.py:2483`), the solve dispatcher (`BE/services/solve_queue.py:1155`), path-scoped reads (`BE/routers/deps.py:149`) and **the first request after a restart with a stored pointer** (`BE/services/active_project.py:121` — the path §5 (E2) exercises) | `_hydrate_context_from_disk`: `ctx.network_revision = int(meta.get("network_revision") or 0)` (read `_read_meta(src)` unconditionally, not only when the network has dispatch). | `BE/routers/projects.py:2282`, `:2369-2384` |
| load (`GET /api/projects/{name}`), bundle import, Saved-snapshot restore | the same assignment on the active ctx after `_restore_results_state`. | `:2693` (load), `:1161` (import), `BE/routers/snapshots.py:745` (restore: `reset_network` `:681`, `bind_project` `:690`, `_restore_results_state` `:745`; the assignment goes after `:745`. `projects.py:1574` is `save_project`'s `clear_undo` block, not the restore.) |
| eviction | `_save_evicted_ctx` → `_save_context` → the same metadata block; nothing extra. | `BE/services/pypsa_service.py:935-990` |

An unsaved edit then a discard-and-reload: the disk counter is the pre-edit one and so is the restored record's → `edited_since_study: false`, which is true of the network on disk.

### 1.4 Frontend contract

**Types** (`FE/api/simulation.ts:976-1012`, `FE/api/network.ts:251`): `EhStudyPayload.edited_since_study?: boolean | null`; the `EhReview` `ok` arm gains `edited_since_study: boolean | null`; `undoInfo` returns `{ depth, unsaved, network_revision: number, … }`.

**Refresh trigger** — `FE/hooks/useNetworkRevisionInvalidation.ts` (new hook, sibling of `useStudyFinishedInvalidation.ts`, same shape): reads `nk(project, 'undoInfo')` with `refetchInterval: 3000` (deduplicated with the three existing pollers), takes `enabled: boolean`, and when `network_revision` differs from the last sample seen **for the same project** invalidates `nk(project, 'results', 'eh_study')` and `nk(project, 'results', 'eh_review')`. The first sample, an `undefined` sample, and a sample from another project are not transitions. Called by `ChatLaunchGreeting` with `enabled: guided && !!currentProject` and by `HubDesignPanel` (Guided-only surface) — so Expert gains no request and no behaviour. Switching Expert → edit → Guided re-arms the hook, whose first enabled sample differs from its last → one invalidation (correct). The hook keeps a single `prev` (project, revision) pair, not a per-project map: the sequence A → (an edit on A from another tab) → B → A is covered by react-query's refetch-on-mount with the default `staleTime: 0` on `useHubStudy` and the greeting's record query, so the hook needs no memory of A's last revision — a per-project map would be a second mechanism for a case the first already handles.

**Greeting** (`solveLine`, `FE/components/ChatLaunchGreeting.tsx:59-102`; Guided only, Expert arms untouched; the record query already runs, the review query stays `enabled` on `done` with no poll). `editedSince = hubStudy?.edited_since_study === true`.

| State (Guided) | Sentence | Change |
|---|---|---|
| `status.running` | `A solve is running right now.` | — |
| hub `running` | `The hub study is running — follow it in Hub design.` | — |
| hub `failed` / `aborted` | `The last hub study did not finish — see Hub design.` | — |
| hub `done`, **`editedSince`** (whatever `stale` and `dispatch` say) | **`The network has been edited since the last study — run it again in Hub design.`** | new, first among the `done` arms |
| hub `done`, not edited, review `stale === true` (**whatever `dispatch` says**) | `A study has run, but the network was solved since — run it again in Hub design.` | sentence unchanged; **now above the dispatch-stale arm** (D-3, an owner-approved change from O1 — see below) |
| hub `done`, not edited, not stale, `dispatch === 'stale'` | `Solved earlier, but the results are stale — the network changed since.` (R-1 fall-through) | — |
| hub `done`, otherwise (incl. `edited_since_study: null`) | `The last study’s results are in Hub design.` | — (O1; claims nothing, so Unavailable falls here) |
| no hub study: `fresh` / `stale` / none | unchanged (P28 §3.2) | — |

**Precedence: edited > solved-since > dispatch-stale > done** (D-3). "Edited since the study" is the fact a Guided user acts on; "solved since" only says the stored copy was replaced, and the dispatch arm is about the foreground Solve, not the study. An `edited_since_study` of `null` never selects a sentence.

*This is a change from O1, not a restatement (review S-1).* Today the `done` arms are entered only when `status.dispatch !== 'stale'` (`FE/components/ChatLaunchGreeting.tsx:69`), so dispatch-stale beats **both** done arms, and the P28 record says "Precedence is unchanged from the owner's O1 decision" (`../qa/2026-09-30-guided-mode-deferred-gate-P28.md:190`). The table above puts the solved-since arm above dispatch-stale. The one combination whose sentence changes is hub `done` ∧ review `stale` ∧ `dispatch === 'stale'` ∧ not edited: today "Solved earlier, but the results are stale — the network changed since."; after this phase "A study has run, but the network was solved since — run it again in Hub design." Both are true of that state; the second names the study the Guided user is standing in front of, and with tracking in place the combination is reachable only for a record with `edited_since_study: null` (a pre-phase record, or one restored from an older pkl), because a tracked record that is not edited cannot have been edited since its last Solve. **Which tests move:** none. The P28 case that pins dispatch-stale (`ChatLaunchGreeting.solvedState.test.tsx:240`, "hub done, review not stale, live dispatch stale → the changed-since sentence") is review-**not**-stale and keeps its sentence under both orders; `:205` and `:224` (review stale with dispatch `none` / `fresh`) keep theirs too. No existing test covers review-stale ∧ dispatch-stale, which is why the reorder was invisible; the new case "hub done, review stale, dispatch stale, `edited_since_study: null` → the solved-since sentence" (§1.5) pins it, and the phase note records the P28 record's `:190` sentence as superseded for that one combination.

**Results card** (`FE/pages/hubDesign/cards/ResultsCard.tsx:41-46`): a second constant `EDITED_TEXT = 'The network has been edited since this study, so these results describe the design as it was. Run again to refresh.'`, rendered with `data-testid="hub-results-edited"` when `study?.edited_since_study === true`; when both it and `review.stale` are true **only the edited banner shows** (one amber line; the edited fact subsumes the other for the user's next action). `STALE_TEXT` and `hub-results-stale` are unchanged otherwise. `flowState` (`FE/pages/hubDesign/flow.ts:14-31`) is **not** widened: `'stale'` and `'done'` already drive the rail identically, and the card reads the record directly.

**Expert surfaces.** No change. `EhReferenceDesignPanel` shows neither `stale` nor the new field; the `/eh_study` payload it reads gains two ignored keys. The `*.expertUnchanged.*` snapshots and `Results.expertUnchanged` pass byte-for-byte; the hook is never enabled in Expert. (A one-line note on the Expert panel is a candidate for a later phase — owner decision D-4.)

### 1.5 Red tests

**Backend — `BE/tests/test_eh_study_edited_since.py` (new; the `_setup` / `_poll` / `_tool` idioms and the `fake_run` monkeypatch of `BE/tests/test_eh_review_route.py:23-53`, `:69-95`):**

| Test | Asserts |
|---|---|
| `test_a_finished_study_carries_the_revision_and_reads_unedited` | record has `network_revision == /undo/info.network_revision`; `/eh_study.edited_since_study is False`; `/eh_review.edited_since_study is False`, `stale is False`; route body deep-equals `_tool(ctx)` |
| `test_an_asset_write_after_the_study_reads_edited_on_both_routes` | `PUT /api/network/buses/{b}` → both routes `edited_since_study is True`; `stale` still `False`; `/undo/info.network_revision` incremented by 1; the tool body equals the route body |
| `test_a_solve_after_the_study_is_stale_but_not_edited` | `ctx.solver_state.pop(EH_REPORT_STORE_KEY)` (the `:118` idiom) → `stale True`, `edited_since_study False` — the two signals are independent; then an edit → both `True` |
| `test_the_study_itself_and_a_foreground_solve_never_bump` | `network_revision` before `POST /eh_study` == after `done`; `_run_and_join` (`BE/tests/test_solver_run_api.py`) on the feeder → unchanged; a 2 s wait after the study for its thread to exit |
| `test_a_refused_edit_and_a_read_never_bump` | `PUT /api/network/buses/does-not-exist` → 404, counter unchanged; `GET /api/network/buses` unchanged |
| `test_undo_bumps_and_keeps_the_finished_record` | edit → `POST /api/network/undo` 200 → `/eh_study` 200 `done`, `edited_since_study True`, counter +2 — every read on a **fresh request** after the undo, so the test measures the ctx later requests read (step 0; also pins §2.2's undo rule) |
| `test_an_edit_during_a_running_study_reads_edited_when_it_finishes` | the `release` idiom (`test_eh_review_route.py:69-95`): study `running` → `PUT` a bus → `release.set()` → poll `done` → `edited_since_study True` (the capture is taken when the study starts, not when it finishes) |
| `test_chat_edit_tools_bump_and_execution_and_read_tools_do_not` | through the real dispatch site — `_dispatch(tool_name, args)` of `test_chat_edits_are_captured.py:54-60` (`chat_service._dispatch_real_tool_call`), not a direct `T.…` call, which bypasses B2: `update_component`: +1; `batch_create_components` (one bus): +1; `review_eh_study`, `get_adequacy_results("eh_study")`: +0; `run_eh_study` (fake run): +0 |
| `test_a_chat_edit_tool_that_raises_or_times_out_still_bumps` | a monkeypatched `update_component` handler that raises → `tool_error` frame and +1; one that sleeps past a monkeypatched `PER_TOOL_TIMEOUT_SECONDS` → `tool_timeout` frame and +1 |
| `test_tool_edits_network_is_derived_from_tool_routes_plus_one_pinned_list` | `tool_edits_network("update_component")`, `("undo_last")`, `("delete_component")`, `("batch_create_components")`, `("apply_demand_from_excel")` true; `("run_eh_study")`, `("review_eh_study")`, `("run_simulation")`, `("gridspine_run_pipeline")`, `("export_to_excel")` false; **every** tool in `TOOL_ROUTES` with a non-GET `_UNDO_PREFIXES` route is true (parametrized over `TOOL_ROUTES`) |
| `test_every_service_call_writer_is_classified_exactly_once` | parametrized over `TOOL_ROUTES`: for every tool whose routes are `_SERVICE_CALL` and whose `safety_tier_for` is `write` / `destructive`, the tool is in exactly one of `_SERVICE_CALL_NETWORK_WRITES` and `_SERVICE_CALL_NON_NETWORK_WRITES`; both sets are subsets of `TOOL_ROUTES`' `_SERVICE_CALL` keys (a sixth unclassified service-call writer, or a stale name, fails here) |
| `test_a_record_without_a_revision_reads_unavailable` | plant a `done` record with no `network_revision` (the `session_state` fixture) → both routes `edited_since_study is None` (JSON `null`), never `false` |
| `test_undo_info_carries_the_revision_and_its_other_keys_are_unchanged` | key set = today's five + `network_revision`; `depth` / `unsaved` semantics untouched (`BE/tests/test_unsaved_results.py` stays green) |
| `test_the_revision_survives_save_and_reload` | study → edit → `POST /api/projects/{name}` → `metadata.json["network_revision"]` == counter; `GET /api/projects/{name}` (load) → `/undo/info.network_revision` equal; `/eh_study.edited_since_study True` (with §2.2) |

Also in `BE/tests/test_eh_review_route.py::test_done_route_equals_tool_and_stale_follows_the_stored_report` (`:95-127`): the deep-equality assertions already cover the new key; add `assert body["edited_since_study"] is False` after the first read (one-line change, justified in the phase note). That test is `@pytest.mark.live_solve` (`:95`) and runs only where HiGHS is present, so the same `is False` assertion is also made in the fake-run `done` case of the new file (`…carries_the_revision_and_reads_unedited`), which is the pin that does not depend on a solver.

**Frontend:**

| File | Cases |
|---|---|
| `FE/components/ChatLaunchGreeting.solvedState.test.tsx` (new `describe('greeting: edited since the study (P33b)')`) | hub done + `edited_since_study: true` → the edited sentence; edited **and** review stale → edited wins; edited and `dispatch: 'stale'` → edited wins; `edited_since_study: false` + stale → the solved-since sentence (unchanged); **review stale + `dispatch: 'stale'` + `edited_since_study: null` → the solved-since sentence (the one combination D-3 reorders; no existing case covers it)**; `edited_since_study: null` → the O1 done sentence; Expert with `edited_since_study: true` → `Not solved yet.` and `getEhStudy` not called (extends the `:285` guard). The P28 cases at `:205`, `:224`, `:240` are untouched and stay green. |
| `FE/pages/hubDesign/cards/ResultsCard.test.tsx` | `edited_since_study: true` → `hub-results-edited` with `EDITED_TEXT`; both true → only `hub-results-edited`; `false` / `null` / absent → neither banner (the existing `:150-164` cases stay) |
| `FE/hooks/useNetworkRevisionInvalidation.test.tsx` (new; the `useStudyFinishedInvalidation.test.tsx` harness) | a revision change on the same project invalidates exactly the two hub keys; the first sample is not a transition; a project switch between samples is not; `enabled: false` never invalidates and re-enabling with a moved revision invalidates once |
| `FE/pages/hubDesign/HubDesignPanel.flow.test.tsx` | the panel calls the hook (one invalidation after a polled revision change; a spy on `invalidateQueries`) |
| `FE/pages/Results.expertUnchanged.test.tsx` and siblings | unchanged, pass byte-for-byte |

### 1.6 Mutation targets (each killed by a named test)

| Mutant | Killed by |
|---|---|
| B1 bumps on 4xx too | `…refused_edit…` |
| B1 excludes `/api/network/undo` | `…undo_bumps…` |
| B1 bumps on `/api/simulation/run` (prefix widened) | `…never_bump` |
| B2 bumps every non-`read` tool (the `mark_dirty` rule) | `…execution_and_read_tools_do_not` |
| B2 bumps after the handler (a raise or a timeout skips it) | `…raises_or_times_out_still_bumps` |
| `tool_edits_network` is a hand list missing `undo_last` | `…derived_from_tool_routes_plus_one_pinned_list` |
| `_SERVICE_CALL_NETWORK_WRITES` drops `apply_demand_from_excel` (or `batch_create_components`) | `…classified_exactly_once` (the dropped tool is in neither set) and `…execution_and_read_tools_do_not` (`batch_create_components` +0) |
| a tool is added to both service-call lists | `…classified_exactly_once` |
| capture reads the revision after `run_eh_study` returns / from `solver_state` instead of the ctx | `…an_edit_during_a_running_study…` (after-return capture reads `false`); `…carries_the_revision…` (equality with `/undo/info`) |
| step 0: `rekey_context` dropped from `apply_undo` or `restore_snapshot` | §0a's tests; `…undo_bumps…` reads `+1` on the next request |
| `edited_since` returns `False` for a record without a revision | `…reads_unavailable` |
| `review_latest` drops the field | deep-equality cases |
| metadata not written / not restored | `…survives_save_and_reload` |
| greeting: edited arm dropped; edited below stale; `null` treated as true | the three greeting cases |
| card: both banners rendered; `null` renders the banner | the card cases |
| hook: invalidates on the first sample; ignores `enabled`; invalidates on a project switch | the hook cases |

## 2. 10a — re-activating a project does not restore its hub study record

### 2.1 Root cause (traced in code; the P28 note did not trace it)

The study record is **in-memory only, per ctx, and is nulled in place on the outgoing ctx by every network-replacing route**:

1. `ProjectSolverState` keeps the seven `STUDY_KEYS` records in no persistence group, on purpose: "a study measures a network in memory and must not be restored from disk beside a network it may no longer describe" (`BE/services/project_context.py:455-480`, `:468`). The **report** is persisted (`eh_reference_design_report` is in `RESULT_STATE_KEYS`, `:227-250`), the **record** is not.
2. `PyPSAService.reset_network` runs at the start of every load, bundle import, template create, Saved-snapshot restore, "New", **and undo** (`BE/services/pypsa_service.py:369-490`; call sites `BE/routers/projects.py:1083`, `:1402`, `:2656`; `BE/services/network_undo.py:129`; `BE/routers/network.py:623`). Before building the new ctx it writes `prev.solver_state[key] = None` for every finished study key (`:443-445`) and only **then** copies the dict for the new ctx (`:467`). `prev` is the **resident, registered** ctx of the project the user is leaving — so the clear mutates project A's own record, not just the copy the fresh ctx receives. The comment at `:425-445` describes the copy's purpose (the fresh ctx must not inherit A's study); clearing on `prev` is more than that purpose needs.
3. `POST /api/projects/{p}/activate` is a pure pointer swap for a resident ctx (`BE/routers/projects.py:2473-2476`) and hydrates only `RESULT_STATE_KEYS` on a cold one (`:2339-2360`, through `_hydrate_context_from_disk`, whose four callers are listed in §1.3 — the restart path (E2) goes through `active_project.py:121`, not through `activate`). So re-activation returns whatever the resident ctx holds — and after step 2 that is `eh_study = None` — or, after eviction / restart, nothing at all.

**The P28 smoke's exact path:** P26 ran the H2 study, then created the microgrid project from a template (`create_from_template` → `reset_network`, `:1399-1402`) while H2 was still resident. That call nulled H2's finished `eh_study`. P28 (C) then activated H2 (resident, pointer swap) and read `/eh_study` → 204; the hub rail opened at Site and the greeting said "No study has run yet". The report was still there (`eh_review` answered from the stored report, `stale: false`), which is why the Results card could have shown results while the rail said no study.

Two more losses of the same class, both reproducible from the reading above: **undo** after a finished study — it is `reset_network`'s clear on `prev` (`:443-445`) that drops the record, not the undo itself (`network_undo.py:129` → `reset_network`; the binding is restored at `:134`, the record is not); and, under session routing, the undo is then **itself** lost because the re-imported ctx lands in the scratch slot (§0a) while the pre-undo ctx — now without its record — is what the next request reads. The review reproduced exactly that: after `PUT` → undo, a planted finished `eh_study` was `None` on the pre-undo ctx (today's prev-clear) and absent on the new one. And **eviction or a backend restart** drops it (nothing on disk). The last is what a user who "re-opens a project" tomorrow hits.

### 2.2 Fix

**(a) In memory — the resident ctx keeps its finished records (small, no decision needed).** In `reset_network`, copy first and clear the finished study keys **on the copy**: `state = dict(prev.solver_state); for key in STUDY_KEYS: if not record_is_running(state.get(key)): state[key] = None; … solver_state=state`. A running record is still shared by reference (the mesh must keep seeing it — `:434-445` rationale holds). `prev`'s own dict is untouched, so a later `activate` of that project finds its record. Every case in `BE/tests/test_adequacy_study_scoping.py` keeps its meaning: the fresh ctx has no study (`:80-141`), a running record is left alone (`:143`), and a study written after the swap does not leak back (`:234-277` — the copy is still a copy). `set_network` (clustering, `:511-513`) keeps its in-place clear: it is the same project and the measured network is gone.

**Undo keeps the record (builds on step 0).** `apply_undo` captures `finished = {k: st.get(k) for k in STUDY_KEYS if not record_is_running(st.get(k))}` before `reset_network()` and restores the non-`None` entries into the new ctx's `solver_state` right after `set_binding` and the step-0 `rekey_context` call (`BE/services/network_undo.py:129-140`, the same lock-held block that puts the binding back). The restore is needed precisely because step 0 makes the new ctx the one later requests read, and 10a(a) clears the finished keys on that copy. Undo is an in-place edit, and B1 bumps the revision for it on the new ctx — which, after step 0, is the project's ctx — so a restored record reads `edited_since_study: true` — honest, since the network was edited (then reverted). The same capture-and-restore is **not** applied to Saved-snapshot restore: a restore replaces the measured network with another one, so the record must go, exactly as for clustering.

**(b) On disk — the finished EH record travels with the project (owner decision D-1; recommended).** With §1.3's revision persisted, the objection recorded at `project_context.py:468` is answered for this one key: a restored record is compared, not trusted.

| Where | Rule |
|---|---|
| `ProjectSolverState` and `RESULT_STATE_KEYS` | **a declared dataclass field** `eh_study_record: Any = None` (`BE/services/project_context.py:455-480`, beside the result-state fields, with the comment: the on-disk mirror of a **finished** `eh_study` record — derived at save, consumed at hydrate, always `None` in memory), and `"eh_study_record"` in `RESULT_STATE_KEYS`. `tests/test_project_state.py:29-57` pins that the live `_state` dict carries exactly the dataclass fields and that every field is in exactly one group; a key in `RESULT_STATE_KEYS` with no field (and a `pop` at hydrate) breaks both. The live `eh_study` key stays a `STUDY_KEYS` member and is never pickled. **Rewrite the `:468` comment** ("They are deliberately in NO persistence group …") and the docstring of `test_every_field_is_classified_exactly_once` (`test_project_state.py:42-52`) to: *study records are not persisted — except the finished EH record's mirror, `eh_study_record`, which is result state, compared against `network_revision` at read time and never trusted as current* — otherwise the next reader re-derives the old rule and deletes the key. |
| `services/study_state.py` | `finished_study_record(record) -> dict \| None`: a deep copy without `thread` / `stop_event` when `status ∈ {done, failed, aborted}`, else `None`. (A running record never reaches a save: `_refuse_save_during_study`, `BE/routers/projects.py:1889`.) |
| save (`_save_context`, `:2008-2022`) | after the comprehension: `results_state["eh_study_record"] = finished_study_record(ctx.solver_state.get("eh_study"))` — derived from the live record, never from a stale mirror key. |
| hydrate (`:2339-2360`) and `_restore_results_state` (`:418-460`, active-scoped) | both first set every `RESULT_STATE_KEYS` member to `None` (`:432`, `:2341-2342`), which now includes the mirror. After the key loop: `rec = state.get("eh_study_record")`; `if rec and state.get("eh_study") is None: state["eh_study"] = {**rec, "thread": None, "stop_event": None}`; then **assign** `state["eh_study_record"] = None` — never `pop` it, so the dict keeps the declared key set. `record_is_running` already answers `False` for `thread is None` (`project_context.py:306-343`), so the mesh never sees a restored record as live. |
| unpickler | the record is builtin types only (`model_dump(mode="json")` report, str/float/None/list/dict); pinned by a round trip through `_safe_unpickle_results` (`:388`). The `__schema__` stays 1 (additive key; an older pkl simply lacks it). |
| bundles, Saved snapshots, Scenarios | nothing extra: `results_state.pkl` and `metadata.json` are already in `_BUNDLE_FILES` (`:89`) and copied by `create_scenario`. A Scenario inherits the base's record and revision and reads `edited_since_study: false` until edited — true, its network is the studied one. |

Interaction with the marker: the restored record carries the `network_revision` it was captured at; the restored ctx carries the saved counter; `edited_since` compares them (§1.3), so a study from before a later saved edit reads `true` and one saved right after the study reads `false`. A record restored from a pkl written by this phase always has a revision; one planted by an older build (not possible — both land together) would read `null`.

**Honest alternative if D-1 is declined:** (a) alone. The in-memory loss (the smoke's path, and undo) is fixed; after eviction or a restart the Guided rail opens at Site and the greeting says "No study has run yet", which is true of the session. The Results card is unreachable then (the rail blocks it with "Run the study first"), so no stale result is shown. The P33b smoke's restart part (§5, (E2)) is dropped.

### 2.3 Red tests

**`BE/tests/test_eh_study_record_survives_reactivation.py` (new; `client`, `install_network`, `api_project`, `session_state` fixtures from `BE/tests/conftest.py:452-516`, `:718`):**

| Test | Asserts |
|---|---|
| `test_a_finished_study_survives_a_template_create_and_reactivation` | project A, study `done` (fake run) → `POST /api/projects/from_template/eh_h2_hub?name=B` 200 → `/eh_study` on B is 204 → `POST /api/projects/A/activate` → `/eh_study` 200 `done` with the same `started_at`; `/eh_review` 200 `stale False` |
| `test_a_finished_study_survives_a_load_of_another_project` | the same through `GET /api/projects/B` (load) and through `POST /api/network/reset` ("New") |
| `test_the_fresh_context_still_has_no_study` | after each of the three swaps above, the active ctx's `eh_study` is `None` (the `test_adequacy_study_scoping` guarantee, re-pinned here against the reorder) |
| `test_undo_keeps_a_finished_record` | (shared with §1.5; reads on a fresh request after the undo, so it needs step 0) |
| `tests/test_project_state.py` (existing, stays green) | `test_state_dict_keys_match_solver_state_fields` and `test_every_field_is_classified_exactly_once` pass with the mirror declared as a field in `RESULT_STATE_KEYS`; the docstring at `:42-52` is rewritten (§2.2(b)) and the "study keys are in neither persistence group" assertion keeps holding, since `eh_study` itself is still only in `STUDY_KEYS` |
| `test_a_running_record_is_still_shared_not_copied` | with a `running` record (the `:143-193` idiom) → after `reset_network(allow_during_study=True)` the new ctx's record `is` the old one |
| `test_a_finished_record_is_saved_and_restored_cold` (D-1) | study → `POST /api/projects/A` → `results_state.pkl` data has `eh_study_record` without `thread`/`stop_event`; drop the resident ctx (`PyPSAService._contexts.pop(key)` under `_registry_lock`, the eviction test idiom) → `POST …/activate` (cold) → `/eh_study` 200 `done`, `edited_since_study False`; the 409 mesh admits a new study (`POST /eh_study` 200) |
| `test_a_restored_record_reads_edited_when_the_saved_network_moved_on` (D-1) | study → edit → save → cold activate → `edited_since_study True`; `GET /eh_review` `stale False` |
| `test_a_failed_and_an_aborted_record_are_restored_too` (D-1) | the `:186-227` idioms of `test_eh_review_route.py`, then save / cold activate → status preserved; a 204 review stays 204 |
| `test_legacy_pkl_without_the_record_restores_nothing` (D-1) | pkl written without the key → `/eh_study` 204 (today's behaviour) |

**Mutation targets.** Clear before copy (today's code) → the first test; undo does not restore → `…undo_keeps…`; the copy shares a finished record (`state[key]` not nulled) → `…fresh_context_still_has_no_study`; save pickles the live record (thread present) → the round-trip assertion; hydrate maps the record when `eh_study` is already set → a test that plants a newer in-memory record and hydrates (the planted one wins).

## 3. 10c — two lock races with correct guards and no test

Both guards are in `FE/utils/projectActions.ts`; both tests go in `FE/components/ChatPanel.reboundLock.test.tsx` (the harness at `:46-125`: an axios adapter that records `seen`, a `lockedByOther` set that 409s both `POST /projects/{p}/lock` and its heartbeat, `holdHeartbeat` / `holdY` promises that delay one reply, fake timers advanced past `LOCK_HEARTBEAT_MS`). The harness gains one control: `lockReplies: Map<string, Array<'ok' | 'hold-409'>>` consumed per `POST /projects/{p}/lock` (not the heartbeat), so one project can answer differently to successive acquires; an exhausted queue falls back to today's `lockedByOther` rule.

**R4 — a stale 409 re-acquire *failure* landing after a fresh acquire of the same project** (guard: `gen !== _lockGen` in the re-acquire `.catch`, `projectActions.ts:311-317`; the `_heartbeatProject !== projectId` half does not cover it because the fresh acquire re-started the heartbeat for the same project).

`it('R4: a heartbeat 409 re-acquire for X that fails after a fresh acquire of X leaves X held and heart-beating')`:
1. `stopLockHeartbeat(); await acquireProjectLock('X'); seen.length = 0` (the N-a prologue).
2. `lockReplies.set('X', ['hold-409', 'ok'])`; `lockedByOther.add('X')` only for the heartbeat (the adapter's heartbeat branch keeps today's rule; the acquire branch consults `lockReplies` first).
3. `await vi.advanceTimersByTimeAsync(LOCK_HEARTBEAT_MS + 10)` → the heartbeat 409s → the re-acquire `POST /projects/X/lock` is sent and held; `expect(seen).toContain('post /projects/X/lock')`. **Then `lockedByOther.delete('X')`** (review S-6): the heartbeat has already 409'd, and if X stayed in the set, the heartbeat step 4 restarts would 409 on every tick until step 6, each triggering another re-acquire that consumes `lockReplies` — the test would then pass only because no tick lands inside step 5's 50 ms wait, which is timing, not a pin. With the delete, step 6's own `lockedByOther.delete('X')` becomes a no-op and is kept for clarity.
4. `await acquireProjectLock('X')` → consumes `'ok'`, bumps the generation, `readOnly false`, heartbeat restarted for X.
5. Release the held reply as a 409; `await new Promise(r => setTimeout(r, 50))`.
6. Assert: `useUIStore.getState().readOnly === false`; `readOnlyReason` is not `'locked-by-user'`; `lastHeldLockProject() === 'X'`; the heartbeat is alive — `lockedByOther.delete('X')`, advance another `LOCK_HEARTBEAT_MS + 10`, `expect(seen.slice(n)).toContain('post /projects/X/lock/heartbeat')`.

Without the `gen` check the stale failure applies `{ok:false}` (read-only) and calls `stopLockHeartbeat()`: step 6's first and last assertions fail. With it, the test is green on arrival (the guard is correct); the mutant is the proof, as in the P28 re-gate.

**R7 — a stale failed acquire in an X → Y → Z turn** (guard: `if (gen !== _lockGen) return …readOnly` in `acquireProjectLock`'s `catch`, `:357-359`).

`it("R7: X → Y → Z in one turn with Y's acquire *refused* last: the tab stays writable on Z and Z's heartbeat survives")` — the N-b body (`:235-262`) with two changes: `lockedByOther.add('Y')` **and** `holdY` set, so Y's reply is a 409 that lands after Z's success; then after `release()`:
- `useUIStore.getState().readOnly === false` and `readOnlyReason !== 'locked-by-user'`;
- `lastHeldLockProject() === 'Z'`; `currentProject === 'Z'`;
- `expect(seen.slice(n)).not.toContain('delete /projects/Y/lock')` (a refused acquire holds nothing to give back; distinguishes R7 from N-b);
- Z's heartbeat is alive: advance `LOCK_HEARTBEAT_MS + 10` → `post /projects/Z/lock/heartbeat` seen.

Without the guard the late 409 runs `stopLockHeartbeat()` and `_applyLock({ok:false})`: the tab goes read-only and Z's heartbeat dies. **R3** (the `gen` check on the re-acquire *success*) stays recorded as equivalent (`_heartbeatProject` already rejects it); no test is added for it.

**Mutation targets.** R4: delete `gen !== _lockGen` at `:311` (keep `_heartbeatProject`); R7: delete `if (gen !== _lockGen) return …` at `:358`. Both must turn the new tests red and leave every existing test green (so the tests are the only pin). Row 4s stress ×10 applies to `ChatPanel.reboundLock.test.tsx` (fake timers and held promises).

## 4. Files

**Backend:** `services/network_undo.py` (step 0 `rekey_context`; undo keeps the record; `/undo/info` field), `routers/snapshots.py` (step 0 `rekey_context`; the counter after `:745`), `services/project_context.py` (`network_revision` field; `eh_study_record` field and `RESULT_STATE_KEYS`; the `:468` comment), `services/dirty_state.py` (`bump_revision`, `revision`), `services/study_state.py` (`edited_since`, `finished_study_record`), `services/chat_tools_schema.py` (`tool_edits_network`, `_SERVICE_CALL_NETWORK_WRITES`, `_SERVICE_CALL_NON_NETWORK_WRITES`; `TOOLS` untouched), `services/chat_service.py` (B2, in the `mark_dirty` block), `services/pypsa_service.py` (§2.2a; B3), `services/adequacy/eh_study_runner.py` (capture in the worker under `lock`), `services/adequacy/eh_review.py` (field), `routers/results.py` (`get_eh_study` field), `routers/projects.py` (metadata; hydrate / restore; save), `main.py` (B1). Tests: `tests/test_undo_rekeys_the_session_context.py` (new, step 0), `tests/test_eh_study_edited_since.py`, `tests/test_eh_study_record_survives_reactivation.py` (new), one line in `tests/test_eh_review_route.py`, the docstring at `tests/test_project_state.py:42-52`.

**Frontend:** `api/simulation.ts`, `api/network.ts`, `hooks/useNetworkRevisionInvalidation.ts` (new), `components/ChatLaunchGreeting.tsx`, `pages/hubDesign/cards/ResultsCard.tsx`, `pages/hubDesign/HubDesignPanel.tsx`; tests `ChatLaunchGreeting.solvedState.test.tsx`, `cards/ResultsCard.test.tsx`, `hooks/useNetworkRevisionInvalidation.test.tsx` (new), `HubDesignPanel.flow.test.tsx`, `components/ChatPanel.reboundLock.test.tsx`; `scripts/smoke-guided.mjs`.

## 5. Gate (deferred spec §0.2 rows, plus)

- **Row 2 additions (eleven):** `tests/test_undo_rekeys_the_session_context.py tests/test_eh_study_edited_since.py tests/test_eh_study_record_survives_reactivation.py tests/test_adequacy_study_scoping.py tests/test_unsaved_results.py tests/test_chat_edits_are_captured.py tests/test_chat_tools_endpoint_map.py tests/test_project_locks.py tests/test_solver_run_api.py tests/test_project_state.py tests/test_active_pointer_paths.py` (the 14-file set already holds `test_eh_review_route.py`, `test_energy_hub_study_isolation.py`, `test_live_network_untouched.py`).
- **Row 4s:** `src/components/ChatPanel.reboundLock src/components/ChatLaunchGreeting src/pages/hubDesign src/hooks/useNetworkRevisionInvalidation` ×10.
- **Row 5 — smoke `--phase P33b`, base `P28`** (`PHASES` at `smoke-guided.mjs:177` gains `P33b`; the dispatcher `:3167-3179` maps it to `phaseP33b`, which calls `phaseP28(browser, { p33b: true })` and keeps every P28 part). The base is P28 rather than the plan table's P26 because P28 (C) is the exact scenario and P28 already carries P26 (every step). Changes inside P28 (C) (`:3041-3100`), applied in both phases unless marked:
  - **(C0, 10a)** on entering (C) the H2 project was studied in P26 and left behind two template creations and two activations. P33b: `GET /api/results/eh_study` must be `done` **without** re-running — the `if (status !== 'done') run` fallback becomes a `check`. P28 keeps the fallback (it still documents the old drift).
  - **(C1)** the O1 done sentence, `eh_review.stale === false`, `eh_study.edited_since_study === false` (P33b), `rev0 = /undo/info.network_revision`.
  - **(C2, reordered)** the foreground Solve now runs **before** any edit: `stale === true` → the P28_STALE sentence within 5 s of a reload — the P28 assertion, unchanged and still true, since no edit has happened.
  - **(C3, P33b)** `PUT` one bus (`x + 0.001`) → **without a reload**, within 5 s: the greeting reads the edited sentence (`chat-launch-solve`), `hub-results-edited` is visible with `EDITED_TEXT`, `hub-results-stale` is absent; `GET /eh_study.edited_since_study === true`, `GET /eh_review` has `stale === true` and `edited_since_study === true`; `/undo/info.network_revision === rev0 + 1`.
  - **(C4, P33b; needs step 0)** `POST /api/network/undo` → `GET /api/network/buses` reads the pre-edit `x` (the smoke backend runs with sessions — `PYPSAGUI_LOCAL_MODE: '1'` still resolves a session row, which P27b's lock flows depend on — so without step 0 this `check` fails first); `/eh_study` still `done` (10a-undo), `edited_since_study === true`, revision `rev0 + 2`; the greeting still reads the edited sentence (the monotonic limitation, logged by `info`).
  - **(C5, P33b)** `ui-mode-expert` → the Expert EH panel (`results-tab-adequacy`, `ehReportRequest`) renders no `hub-results-edited` and no text containing "edited since"; back to Guided.
  - **(E2, P33b, D-1)** `POST /api/projects/<h2>` (save) → `await stop('uvicorn'); startBackend(); await waitFor(health)` (the P27b idiom, `:2370-2373`) → `POST …/activate` → `/eh_study` 200 `done` with `edited_since_study === true`; `/undo/info.network_revision === rev0 + 2`; reload the page → the hub rail opens at Results, the greeting reads the edited sentence, the card shows `hub-results-edited`. Then run the study from the Goal card → `edited_since_study === false`, no banner, the O1 done sentence.
  - P28 (D) unchanged. P28's own constants: `P28_STALE` unchanged; new `P33B_EDITED` and `P33B_EDITED_CARD`. The reorder **removes P28's own edit step** — the bus edit at `:3066-3074` and its `info` line "after the bus edit: eh_review.stale=false … (the review flags a later solve, not an edit)" — from both phases, since once edits are tracked that line would document a drift this phase closes; step 8 records the removal in the P28 phase note (plan §"P28 phase note") in one line.
- **Row 7:** `git diff <base> -- 'pypsa-gui/frontend/src/**' | grep -c uiMode` reviewed; the only `uiMode` reads are the hook's `enabled` flags.

What the gate must prove, in one line each: an HTTP undo and a Saved-snapshot restore are read by the next request (step 0); an edit after a study is said on both surfaces within one poll and nowhere else; a Solve and the study itself never say it; a project re-activated, undone, saved and restarted still knows its study; the two lock races have a pin that a one-line mutant turns red.

## 6. Risks

| Risk | Mitigation |
|---|---|
| A write path that is not a seam (a hand-edited `network.nc`; a future router under a new prefix; an in-process mutation by a service) does not bump | the same blind spot as the undo stack, the dirty flag and the foreign-lock gate (OPEN-ITEMS 6); the chat side is derived from `TOOL_ROUTES` plus one pinned list, both parametrized; the sentence says "edited", so a missed bump under-reports rather than lies |
| **Known limitation — a study input that is not a network edit under-reports.** `PUT /api/simulation/solver_config` (`voll` feeds the EH ENS solve, `eh_study.py:629`, `:653`), `/finance`, `/commercial/value_flows`, `PUT /api/projects/{p}/stress_scenarios`, `/worksheet`, `/asset_health`, uploads and layout are not seams and never bump | by design: the sentence says "network", so it stays true; widening the predicate to those routes would make the word wrong (they are not Asset writes). If wanted later, a separate `inputs_edited_since_study` with its own sentence — not this phase |
| Monotonic counter says "edited" after an undo that restored the studied network; false positives of the same kind: `PUT /api/network/meta` (a title), carrier colours, `recalculate_lengths`, a no-op `_bulk` | true (an edit seam was crossed); recorded as a limitation; the remedy is one click and the sentence is true. Nothing bumps falsely from the study, the FMEA sweep or a Solve: `set_network` is not on the sweep's path, `/api/simulation/*` and `/api/results/*` are outside `_UNDO_PREFIXES`, and no read-only POST exists under `/api/network/` or `/api/io/` on HEAD |
| B2 bumps on a chat edit tool that raised before mutating, or timed out before mutating | over-reporting in the direction `mark_dirty` already chose; a rare case; the timeout path is covered because the bump is before the handler |
| One clustering bumps twice (B1 or B2, then B3) | harmless; no test asserts `+1` on clustering (§1.3 B3) |
| A lost `+= 1` between the event loop (B1) and an SSE thread (B2) | a lost increment cannot make the counter equal a captured value a later edit should have moved away from, so no sentence turns false; `edited_since` reads an int without the lock. Two tabs share one resident ctx and one counter. **Multi-replica deployments have a per-process counter** (the same blind spot as undo depth): sticky sessions assumed |
| `results_state.pkl` grows by one report copy for EH projects (the record's `report` beside the stored report) | the report is JSON-sized (KB), the pkl already carries dispatch frames; if D-1 is declined nothing is written |
| A restored finished record is read as `running` by the mesh | `record_is_running` answers `False` for `thread is None`; pinned |
| The reorder in `reset_network` lets a finished study leak into the fresh ctx | the copy is cleared; `test_adequacy_study_scoping.py` plus `test_the_fresh_context_still_has_no_study` |
| `undo/info` gains a key an older FE does not know | additive; the FE reads what it knows |
| The hook invalidates on the first sample after mount and causes a request storm | the first sample is never a transition; two queries at most, deduplicated |
| Expert behaviour changes through the hook | the hook is enabled only in Guided surfaces; snapshots pin markup |
| The P28 smoke's (C) reorder weakens P28 | the P28_STALE assertion is kept verbatim; the Solve-before-edit order is the only one under which it is true once edits are tracked |

## 7. Decisions taken by this spec's author

| Question | Decision |
|---|---|
| Counter vs fingerprint | counter (§1.2) |
| Field names | `network_revision` (captured and current), `edited_since_study` (derived boolean); not `network_changed` |
| Where the counter and helpers live | `ProjectContext` field; `dirty_state.bump_revision` / `revision`; `study_state.edited_since` / `finished_study_record`; no new module |
| HTTP predicate | the undo prefixes minus `/undo/info`, on 2xx only; undo included |
| Chat predicate | derived from `TOOL_ROUTES`, plus one hand list for the tools that have no route (`_SERVICE_CALL_NETWORK_WRITES`), pinned by construction against its complement; bump **before** the handler, in the `mark_dirty` block, so a raise or a timeout cannot skip it |
| Capture point | in the study worker under the ctx lock, immediately before `run_eh_study` (not on the request thread: the B1 window; not inside `run_eh_study`: the fake-run harness); the claim is "edited since the study started" |
| Step 0 scope | undo and Saved-snapshot restore (the two in-place replaces of a resident project); `/api/io/import/*` is the "New" class and goes to OPEN-ITEMS 12; bundle import and template create are not lossy and are noted there |
| Unavailable | `null` for a record without a revision; never selects a sentence or a banner |
| FE refresh trigger | `/undo/info` (already polled at 3 s by three readers) rather than a new poll of the record or per-site invalidation |
| Precedence | edited > solved-since > dispatch-stale > done; one banner on the card |
| `flowState` | unchanged |
| `set_network` | keeps its in-place clear, bumps the revision |
| 10a in memory | copy-then-clear in `reset_network`; undo captures and restores finished records |
| Smoke base | P28 (superset of P26), with (C) reordered Solve-before-edit |
| R3 | left as recorded (equivalent); no test |

## 8. Owner decisions (decided 2026-10-06)

Each was put to the owner with the recommendation below; the owner chose the recommended option in every case. Nothing in §1–§5 is conditional any more.

| # | Question | Options | Decision (2026-10-06) |
|---|---|---|---|
| **D-1** | Persist the finished EH study record (and the revision) with the project, so a re-opened or restarted project still knows its study? This reverses the recorded "no persistence group" rule for one key, with the revision making the restored record honest. | (a) yes — §2.2(b) and §1.3 persistence; (b) in-memory only — §2.2(a), the honest alternative (rail at Site after a restart, "No study has run yet"). | **(a) — persist.** 10a's complaint is "a user who re-opens a project loses the Guided study-done state"; (b) fixes the smoke's path and undo but not the user's. §2.2(b) applies as amended for B-3 (a declared field, assigned `None` at hydrate); §5 (E2) stays. |
| **D-2** | The two sentences. | greeting: `The network has been edited since the last study — run it again in Hub design.`; card: `The network has been edited since this study, so these results describe the design as it was. Run again to refresh.` — or the owner's wording, kept to what the counter carries ("edited", not "changed"/"differs"). | **As written.** Both are true under every sequence the tests cover, including an undo and an edit during a running study (the claim is "since the study started", §1.3). |
| **D-3** | Precedence when more than one is true (edited, solved-since, dispatch-stale); and the review's S-1 — the table silently moved solved-since above dispatch-stale, which O1 had below both done arms. | (a) edited > solved-since > dispatch-stale, one banner; (b) show both facts (two banners / a combined sentence); (c) keep O1's order (edited > dispatch-stale > solved-since). | **(a), recorded as an owner-approved change from O1 for the hub-done case.** The user's next action is the same; one line is what a Guided surface should say; the study-specific sentence wins over the foreground-Solve sentence when both are true. §1.4 states the one combination that changes, why it is only reachable for an Unavailable record, and that no existing test's expectation moves (the new case pins it). The P28 record's "precedence unchanged" line is superseded for that combination. |
| **D-4** | Expert EH reference-design panel. | (a) no change (§0.1 rule); (b) one muted line "edited since this study" under the report header, Expert-only, snapshot updated. | **(a) — unchanged this phase.** (b) is a one-line follow-up if Expert users ask. |
| **D-5** (from review B-2) | HTTP undo under session routing lands the re-imported ctx in `scratch:<session>`, not in the slot later requests read (§0a): fix it in this phase, or drop the undo claims? | (a) **step 0** — `apply_undo` (and Saved-snapshot restore, which has the same defect) re-key the rebound ctx into the project's slot, with an HTTP read-back red test; the undo rule, the `+2` assertions and (C4) then stand; (b) drop the undo rule, the `+2` cases and (C4), and record the defect as an OPEN-ITEM. | **(a) — step 0 first.** The smoke's undo step is the one sequence a Guided user actually performs, and the fix is one line at each of two sites. Scope: undo and Saved-snapshot restore (both reproduced); `/api/io/import/*` under a session pointer is a different class and is recorded as OPEN-ITEMS 12; bundle import and template create are not lossy (§0a table). |

## 9. Review response

Every finding of [`../qa/2026-10-06-guided-p33b-spec-review.md`](../qa/2026-10-06-guided-p33b-spec-review.md), verified against the code on `bb8b2e7b8` before the spec was changed. "Applied" means the spec now says what the finding asked for; where the spec departs from the finding's suggested wording, the reason is given.

| Finding | Verified | Change |
|---|---|---|
| **B-1** chat predicate misses five `_SERVICE_CALL` write tools | yes — `safety_tier_for` over `TOOL_ROUTES` on HEAD gives 22 service-call write/destructive tools, of which the five named touch the live network and the other 17 do not | §1.3 B2: predicate = `TOOL_ROUTES`-derived **or** `_SERVICE_CALL_NETWORK_WRITES`; the complement `_SERVICE_CALL_NON_NETWORK_WRITES` named; "no hand list" replaced by "one hand list, for the tools that have no route, pinned by construction". §1.5: `batch_create_components` +1 in the dispatch test; new `…classified_exactly_once` (every service-call writer in exactly one set). §1.6: the `apply_demand_from_excel` mutant and the both-lists mutant. §7 row updated. |
| **B-2** HTTP undo lands in the scratch slot | yes — reproduced on `bb8b2e7b8` (x+2.5 read after a 200 undo; two ctxs for one project); the same for Saved-snapshot restore | New §0a (step 0, D-5): the mechanism, the reproduction table, all seven `reset_network` callers classified, the one-line fix at two sites, five red tests, mutants, size S. §1.1's undo fact row and §2.1's "undo drops the record" corrected as the review asked (the prev-clear drops it; under sessions the undo itself is lost). §2.2's undo rule now says it builds on step 0 and why the restore is still needed. §1.5 `…undo_bumps…` reads on a fresh request. §5 (C4) marked as needing step 0 with the read-back `check` first. Plan: step 0 row, dependency "0 → 1, 2". |
| **B-3** `eh_study_record` in `RESULT_STATE_KEYS` without a field; `pop` at hydrate | yes — `test_project_state.py:29-57` pins the exact field set and the partition; both hydrate paths null every `RESULT_STATE_KEYS` member first | §2.2(b): `eh_study_record: Any = None` declared on `ProjectSolverState`; hydrate assigns `None` after mapping, never pops; the `:468` comment and the `test_project_state.py:42-52` docstring rewritten to the new rule. §2.3 lists `test_project_state.py` as a stays-green pin; §4 and §5 row 2 carry it. |
| **S-1** the greeting table reorders O1 and labels it "—" | yes — `ChatLaunchGreeting.tsx:69` enters the done arms only when dispatch is not stale; P28 record `:190` says precedence unchanged | §1.4: the row is marked as a change; a paragraph states the new precedence as an owner-approved change from O1 (D-3), names the one combination that changes, and names the tests. **Departure from the finding:** after reading `ChatLaunchGreeting.solvedState.test.tsx:197-262`, no existing test's expectation moves — `:240` is review-not-stale and keeps its sentence under both orders, and no case covers review-stale ∧ dispatch-stale. The spec says so and adds the case that pins the changed combination rather than claiming a test moves. |
| **S-2** capture window between the request thread and the copy | yes — the copy is taken under `lock` in `run_eh_study` (`eh_study.py:1187-1189`), after the record is published | §1.3 Capture: moved into the study worker under the same `lock`, immediately before `run_eh_study`; the residual microsecond window and the "since the study started" claim are stated. **Departure:** not inside `run_eh_study` beside the copy, because the suite's `fake_run` replaces `run_eh_study` and every fake-run record would then read `null`. New test `…an_edit_during_a_running_study…` pins the direction; the §1.6 capture mutant reworded. |
| **S-3** three facts/anchors wrong | yes — `eh_study.py:1181` pops the store at start; `projects.py:1574` is `save_project`'s `clear_undo` block; the hydrate helper has four callers | §1.1 report-clear row; §1.3 persistence table (restore anchors `snapshots.py:681/690/745`; the four hydrate callers with `active_project.py:121` named as (E2)'s path); §2.1 step 3. |
| **S-4** B2 placement must survive the timeout path | yes — `chat_service.py:4779-4800` returns from the `TimeoutError` branch before any after-handler code | §1.3 B2: the bump is **before** the handler, in the `mark_dirty` block (the review's second option; simpler than a `finally` and the rationale already exists there). New test `…raises_or_times_out_still_bumps`; §1.6 mutant; §6 row; §7 row. |
| **S-5** B3 double-counts clustering | yes — `set_network` has one production caller, `clustering.py:309` | §1.3 B3 says one clustering is `+2`, harmless, and that no test asserts `+1`; B3 kept for the direct in-process callers so the rule has no exception. §6 row. |
| **S-6** R4 harness timing-dependent | yes — the heartbeat branch keeps the `lockedByOther` rule after step 4 | §3 R4 step 3: `lockedByOther.delete('X')` right after the held send is observed, with the reason. |
| **S-7** the one-line addition is in a `live_solve` test | yes — `test_eh_review_route.py:95` | §1.5: the same `is False` assertion in the fake-run `done` case of the new file, named as the solver-independent pin. |
| **N-1** non-network study inputs under-report | yes | §6: a "known limitation" row listing the routes, with "do not widen the predicate" and the possible later `inputs_edited_since_study`. |
| **N-2** false positives are the tolerable kind | yes | §6: the examples and the "nothing bumps falsely from the study, the sweep or a Solve" reasoning folded into the monotonic row. |
| **N-3** persistence consistent on every writer | yes | no change needed; the `!=` choice was already §1.3's. |
| **N-4** concurrency, multi-replica | yes | §6: a row for the lost-increment argument and "sticky sessions assumed". |
| **N-5** FE hook, single `prev` | yes | §1.4: one sentence on why a per-project map is not needed. |
| **N-6** 10a root cause confirmed | — | no change. |
| **N-7** wording under every sequence | — | the "since the study started" claim is now stated in §1.3 and D-2. |
| **N-8** D-5 needed; D-3 absorbs S-1 | — | §8 as decided. |
| **N-9** smoke anchors; the P28 edit step | yes — `smoke-guided.mjs:3066-3074` | §5: the edit step's removal is stated and assigned to the step-8 note. |
| **N-10** sizing | — | plan: step 0 (S) added; M still holds; dependency line updated. |

Found while scoping D-5, outside the review: `/api/io/import/{netcdf,csv,excel,matpower}` under a session with an active project imports into the unbound scratch ctx and never clears the pointer, so the next request reads the project's resident network (reproduced: a 7-bus import, 200, then `GET /api/network/buses` returns 1 bus). Recorded as OPEN-ITEMS 12; the fix is the `/api/network/reset` rule (`network.py:633-635`), not re-keying, so it is not part of step 0.
