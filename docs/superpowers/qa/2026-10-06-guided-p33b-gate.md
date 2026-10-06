# QA gate: P33b — study freshness after an edit (10b), study record on re-activation (10a), lock races (10c)

**Date:** 2026-10-06. **Reviewer:** independent QA gate (did not write the code).
**Scope:** `git diff 240b2a00e..5ea5d05ff` on `claude/epic-allen-k2t1c4` (12 commits). The backend has not changed since `1de694220`, the commit the implementer's row-1 log ran on: `git diff 1de694220 HEAD -- pypsa-gui/backend` is empty. Only `smoke-guided.mjs` (B4) and docs changed after it.
**Contract:** spec `specs/2026-10-06-guided-p33b-study-freshness.md` (§0a, §1, §2, §3, §5, §8 D-1…D-5), spec review `qa/2026-10-06-guided-p33b-spec-review.md`, plan "P33b plan" and "P33b phase note" (deviations 1–6, probe list).
**Scratch:** `/tmp/claude-0/-home-user-pypsa-eur/93e65137-63f8-5e19-b114-b55788c10af8/scratchpad/qa33b/` (written `qa33b/` below).
- No repo source or test was edited. This file is the only repo write, and it is uncommitted.
- Probes and mutants ran in scratch worktrees `qa33b/wt` (HEAD) and `qa33b/wtbase` (`240b2a00e`), with `node_modules` symlinked. Both are removed now.
- Probe files are kept in `qa33b/`: `test_qa_bleed.py`, `test_qa_bleed_local.py`, `test_qa_probe_p33b.py`, `test_qa_restore_collision.py`, `test_qa_cap.py`. The mutation runner is `qamut.py` and its log is `qamut.log`.
- The smokes ran one at a time. No process is left running.

## Verdict: **NO-GO** — one blocker (B-1)

**B-1 is a data-loss regression on the desktop path.** In local mode:
1. Create project A from a template, then create B from a template.
2. Edit B.
3. Switch back to A. The header offers Undo (depth 1).
4. Press Undo.

A's in-memory network is replaced by **B's** network. A Save, or an autosave or eviction write-back, then writes B's network into A's files. On the base this sequence is harmless (depth 0).

Under sessions, the same happens through template create, through load, and through a restore of another project's Saved snapshot. On the base, load already showed depth 1, but the step-0 defect made the undo a no-op that nobody read.

The root cause is pre-existing: `reset_network` carries `undo=prev.undo` into the new project's context. Steps 0 and 4b make it reachable and effective.

Everything else holds:
- The two sentences are true under every sequence in the smoke. In the screenshots, the greeting and the card agree on one screen.
- The edit seam is complete for HTTP and chat writers. Nothing false-bumps: EH study, FMEA sweep, Solve, polling and GETs were all probed.
- Step 0 reads back correctly under sessions.
- Persistence survives save, restart and eviction write-back.
- The 10c mutants are killed.
- Rows 2, 3, 5 and 7 are green.

There are four should-fixes (S-1 … S-4) and six notes.

## Gate rows

| # | Command (cwd) | Result |
|---|---|---|
| 1 | not rerun. `scratchpad/p33b/row1.log` (header `1de694220`) says `9504 passed, 31 skipped, 11 deselected`. `git diff 1de694220 HEAD -- pypsa-gui/backend` is empty. `pytest tests/ -m "not slow" --collect-only` on HEAD (`pypsa-gui/backend`) gives `9535/9546 collected (11 deselected)`, and 9504 + 31 = 9535 | accepted (log usable, counts match) |
| 2 | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest <14-file set> tests/test_undo_rekeys_the_session_context.py tests/test_eh_study_edited_since.py tests/test_eh_study_record_survives_reactivation.py tests/test_adequacy_study_scoping.py tests/test_unsaved_results.py tests/test_chat_edits_are_captured.py tests/test_project_locks.py tests/test_solver_run_api.py tests/test_project_state.py tests/test_active_pointer_paths.py -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`, HEAD `5ea5d05ff`; file list in `qa33b/row2.files`) | **1255 passed, 17 skipped** (`qa33b/row2.log`). `--collect-only` gives 1272 = 1255 + 17, the implementer's count. Includes `test_guided_mode_prompt.py` |
| 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`) | 0 errors (`qa33b/row3.log`) |
| 4 | `npx vitest run` (`pypsa-gui/frontend`) | 278 files, **3262 / 3263** (`qa33b/row4b.log`, idle machine). The one failure is a 5 s timeout in `src/layout/BottomPanel.test.tsx`. That file is untouched by the phase and passes alone (79/79). A first run under heavy load (smoke + mutants + row 2 in parallel) had 7 BottomPanel timeouts (`row4.log`). The four `*.expertUnchanged` snapshots pass |
| 4s | `npx vitest run src/components/ChatPanel.reboundLock src/components/ChatLaunchGreeting src/pages/hubDesign src/hooks/useNetworkRevisionInvalidation` ×3 (`pypsa-gui/frontend`) | **2 / 3 green**: run 3 failed `useNetworkRevisionInvalidation.test.tsx > a project switch between samples is not a transition`. The hook file alone failed **1 / 10** more runs. **Flaky → S-3** |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P33b --out qa33b/smoke-p33b` (`pypsa-gui/frontend`) | **PASS**, 47 screenshots. (C0) done without a re-run. (C1) rev0 = 0. (C2) the Solve did not bump; `P28_STALE` 0.0 s after the reload. (C3) both surfaces say "edited since" **1.7 s** after the edit, no reload; one banner; route values agree, rev = 1. (C4) undo read back, record kept, rev = 2. (C5) Expert shows nothing new. (E2) restart → done, edited, rev = 2, rail at Results; re-run → `false`, O1 sentence |
| 5 | same, `--phase P24` and `--phase P30` (they share the changed `b4StudyReadFailure`) | **PASS** (21 / 61 screenshots). Both log "Retry recovers … hub-card-results shown (study record: done)" |
| 7 | `git diff 240b2a00e -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` | **11**: ten test-store setups, plus one product read (`HubDesignPanel.tsx` hook `enabled`). No new product branch |
| E | Expert / prompt | No snapshot or `*.expertUnchanged*` in the diff. `chat_tools_schema` adds only helpers outside `TOOLS`; no description or system-prompt line changed. (C5) and the `p33b-c5-expert-unchanged.png` screenshot show no "edited since" |

## Findings

### BLOCKER

**B-1 — a project's Undo applies another project's network (cross-project undo stack), made reachable by steps 0 and 4b.**

*Where:*
- `services/pypsa_service.py:503` — `reset_network` builds the new context with `undo=prev.undo`, the same `_UndoState` object.
- `routers/projects.py:443` — `_register_created_context`, called at `:1146` (import) and `:1465` (template).
- `routers/snapshots.py:700` and `services/network_undo.py:158` — the step-0 `rekey_context`.

*Mechanism:* `undo_service.clear()` runs on A's stack. `reset_network` then hands that same stack object to B's new context. Step 4b now keeps both contexts resident. B's edits push onto the shared stack. Re-activating A is a pointer swap, so A's `/undo/info` shows B's depth, and `POST /api/network/undo` re-imports B's capture into A's context. `apply_undo` then `set_binding(prev_binding)` (A) and, with step 0, re-keys it under A's key. So the next request reads B's network as project A.

*Repro, local mode (the desktop build; `qa33b/test_qa_bleed_local.py`, `local_client`):*
1. Create `LA` from `eh_h2_hub`.
2. Create `LB` from `eh_microgrid`.
3. `PUT` one of LB's buses.
4. Activate `LA`.
5. `/undo/info.depth` reads 1.
6. `POST /api/network/undo` returns 200, and LA's buses are now LB's.
7. Save LA (200), push LA out of the registry, cold-activate LA: **its files hold LB's network**.

Result on HEAD: `{'depth': 1, 'undo': 200, 'A_now_B': True, 'save': 200, 'A_on_disk_is_B': True}`. On `240b2a00e`: `{'depth': 0}` (pass).

*Repro, sessions (`qa33b/test_qa_bleed.py`):* A is `api_project`, then B is reached by template create, by load (`GET /api/projects/B`), or by a restore of B's Saved snapshot. Then edit B, activate A, undo.

| Path | HEAD | `240b2a00e` |
|---|---|---|
| template | A's buses become B's | depth 0 |
| load | A's buses become B's | depth 1, but the undo landed in scratch, so A is unchanged (the step-0 defect masked it) |
| restore | A's buses become B's | depth 0 |

*Why a blocker:* it is silent data loss of a whole project on an ordinary sequence: two templates, one edit, switch back, then Ctrl-Z, which the header advertises. That sequence is P26's own path (two template creations). The spec's §0a judged template / import "not lossy" and step 0 "harmless". Both judgements missed the shared stack.

*Fix direction (not applied):* give a context for a **different** project its own `_UndoState`. In `reset_network`, carry `undo` only for an in-place replace (undo, same-project restore); pass a fresh one for load / template / import / other-project restore. Alternatively, clear and replace `ctx.undo` after the bind in those handlers. Add the two probes above as red tests: local-mode template and sessions template / load / restore. The same audit applies to `chat_state=prev.chat_state` and `network_revision=prev.network_revision` on cross-project swaps. Both are harmless today: chat by design, and the counter is overwritten by `_restore_results_state` or hydrate, except for template create (N-5).

### should-fix

**S-1 — a Saved-snapshot restore can bring back a study record that describes a different network and read it as *not edited*.**

*Where:* `routers/snapshots.py:655` skips files the snapshot lacks. A snapshot taken when the project had no results has no `results_state.pkl`, so the project's later pkl survives the restore. `routers/projects.py:418` `_map_eh_study_mirror` then maps its record onto the restored network. With `edited_since`'s `!=` (`services/study_state.py:181`) and a counter that a restore moved backwards, captured == current is possible.

*Repro (`qa33b/test_qa_restore_collision.py`, sessions, fake run):*
1. Hub project; snapshot S0; edit ×2; save; snapshot S1 (no pkl).
2. Restore S0. The counter goes back to r0.
3. Edit ×2 differently, so the counter is r0 + 2 on network N′.
4. Run the study (captured r0 + 2); save.
5. Restore S1. The network is S1's (≠ N′), the counter is r0 + 2, and the stale pkl's record is mapped.

`/eh_study` 200 `edited_since_study: false` and `/eh_review` `stale: false`, `edited_since_study: false`. The Results card shows N′'s design as current, with no banner, on network S1.

The spec (§2.2) says a restore "replaces the measured network … so the record must go". Under D-1 it comes back.

*Fix:* in `restore_snapshot`, after `_restore_results_state`, null `eh_study` when the snapshot has no pkl. Better, delete or ignore the project's `results_state.pkl` when the snapshot lacks one: the pre-existing class also affects `lopf_results` and the other side results. Pin it with a test.

**S-2 — three P33b rules are correct but unpinned (surviving mutants).**
- `Q4a`: `_restore_results_state` no longer restores the counter (`routers/projects.py:485` removed). It survives because every load test reloads the **same** project, whose carried counter is equal. On a load of B from A, B's record would be compared against A's counter. That can be a false "not edited" (A's counter = B's captured value, while B's saved counter is higher). Pin it with a cross-project load (and import) test.
- `Q3a`: `!=` → `>` survives. The spec's stated reason for `!=` (a restore moves the counter back with the record) has no test.
- `Q2a`: B1 restricted to `/api/network/` (drops `/api/io/`) survives. Low impact (an io import unbinds and clears the record), but the HTTP predicate's second prefix is unpinned. The chat side is pinned by the parametrized `TOOL_ROUTES` test.

**S-3 — `useNetworkRevisionInvalidation.test.tsx` "a project switch between samples is not a transition" is flaky (1 of 3 row-4s runs; 1 of 10 isolated runs).**

*Cause:* switching to `B` enables the query for `nk('B','undoInfo')`, whose mocked fetch resolves `network_revision: 5`. If that fetch lands before `sample('B', 9)`, it becomes B's first sample, and 5 → 9 is a genuine transition (2 invalidations, `:89` fails). The product code is right. The test races its own mock.

*Fix:* mock the fetch per project with the same value as the first planted sample, or await the B fetch before `sample('B', 9)`. Spec §3 / row 4s require determinism.

**S-4 — template create gives the new project the previous project's counter and writes no counter to disk until the first save.**

*Where:* `pypsa_service.py` `reset_network` carries `network_revision=prev.network_revision`. Probe: A at rev 4 → template B reads rev 4. `create_from_template` / `_write_meta` (`routers/projects.py:~1500`) writes `metadata.json` without `network_revision`.

Harmless for the sentences today: the record and the counter are compared within one context, and save writes both. But it is the same "carry across projects" pattern as B-1. And a cold hydrate of an unsaved template project reads 0 while memory said 4. Fold it into the B-1 fix: a cross-project swap starts the counter at 0 or at the disk value.

### notes

- **N-1, the request box (deviation 2) is per-request and thread-safe.** `_request_ctx_box` is a `ContextVar` holding a **fresh one-element list per request**, bound by the middleware (`main.py:775`). The handler's copied context shares that list, the middleware reads it after `call_next`, and nothing is global. The three ctx-swap sites (`_publish_active`, `set_active`, `activate_context`) all `_note_request_ctx`. `rekey_context` keeps the object. No other site sets `_request_ctx` (grep). Residual races, both only under concurrent requests on one session or the foreground, and both one lost or misplaced bump:
  - in local mode the box is unset, so B1 bumps `_active()` after `call_next`, and a concurrent load in another tab could take that bump;
  - a second request on the same session holding the pre-undo context bumps an orphan.
- **N-2, the chat-seam capture window under-reports, contrary to the spec's "only over-reports".** B2 bumps **before** the handler (`chat_service.py:4759`). A study worker that captures and copies between that bump and the handler taking the mutation lock records captured == current with a pre-edit copy, so it reads "not edited". The window is milliseconds and needs a study started over HTTP during a chat edit. Correct the spec / phase-note claim, or bump again after the handler (the counter is monotonic, so a second bump is harmless).
- **N-3, the effective resident cap is RESIDENT_CAP − 1 on a template create under sessions.** During `register`, the new context sits under both `scratch:<s>` and its key until `rekey_context` pops the scratch slot. The cap check (`pypsa_service.py:939`) counts it twice and evicts one unrelated project early. Probe `qa33b/test_qa_cap.py`, cap 3: after three creates only two projects are resident, and `cap-0` was written back. No data loss (eviction is write-back). Fix: call `rekey_context` before `register`, or register via `rekey_context` then `_evict_if_over_cap`.
- **N-4, eviction and persistence probe passed** (`test_probe_eviction_with_templates`, cap 3). Six template projects; ev-0 has a done study and an unsaved edit, and is evicted. A cold activate gives back the unsaved edit (write-back), `network_revision` equal, `started_at` equal, `edited_since_study: true`, and one context per project. Bundle import always creates a fresh row and key, so it cannot displace another session's resident context: no stale overwrite of disk through 4b.
- **N-5, edit-seam probe passed.** Each step below was checked on `/undo/info` (`test_probe_study_sweep_solve_do_not_bump`, `live_solve`, `eh_datacenter`). A real EH study, then a real FMEA sweep, then a foreground Solve, then repeated GETs / `undo/info` / `eh_review`: revision unchanged and `edited_since_study: false` throughout. One bus PUT: +1, and both routes read `true`. Route audit (OpenAPI):
  - every non-GET under `/api/network/` and `/api/io/` is a network writer;
  - no network writer lives outside those prefixes (layout writes `layout.json`, gridspine opens its own network, `/api/simulation/*` and `/api/results/*` are Solve or study);
  - every non-read chat tool whose route writes those prefixes is classified `True`; the five service-call writers are `True`; the 17 others are `False`.

  Two sessions on one project share one counter (+1 seen by both).
- **N-6, the B4 smoke oracle is now self-referential.** `smoke-guided.mjs:1348` reads the record and then expects the card it implies, so neither branch can fail on the 10a question. Acceptable for a regression smoke (P26 / P28 pin 10a elsewhere), but it no longer asserts which card.
- Deviation 1 confirmed: the smoke backend has no session (local mode), so (C4) does not exercise step 0. Step 0 is proven by its own tests (mutants `Q0a`–`Q0c` killed).

## Sentences under every sequence (item 1)

| Sequence | Evidence | Truth |
|---|---|---|
| edit, then undo | smoke (C3)/(C4); `test_probe_undo_and_restore_counter` (+2, record kept) | true ("edited since"; monotonic, as accepted) — but see B-1 if the undo stack is another project's |
| edit during a running study | `test_an_edit_during_a_running_study_reads_edited_when_it_finishes` (row 2); `null` while running | true on HTTP; N-2 window on chat |
| study started, then edited mid-run | same test (capture at worker start) | true |
| re-activation | smoke (C0), (E2); reactivation tests | true |
| restart | smoke (E2): edited after the restart, cleared by a re-run | true |
| snapshot restore (counter moves back) | `test_qa_restore_collision.py` | **false negative possible (S-1)** |
| project switch A→B→A | reactivation tests; hook ignores cross-project samples, refetch-on-mount covers A | true |
| two tabs | probe: shared counter; the hook invalidates in each tab | true |
| Solve after study | smoke (C2): "solved since", rev unchanged | true |
| Expert | smoke (C5), Expert snapshots | unchanged |

Screenshots read: `42-p33b-c3-edited-greeting-and-card.png` (greeting and card both say "edited since", one amber banner), `44-p33b-e2-after-restart-edited.png` (same after the restart, rail at Results), `43-p33b-c5-expert-unchanged.png` (Expert report, no "edited since"). The "Unnamed Network" breadcrumb is pre-existing.

## Mutation table (mine; `qa33b/qamut.py`, log `qa33b/qamut.log`)

| Id | Step | Mutant | Result (first killing test) |
|---|---|---|---|
| Q0a | 0 | `rekey_context` no longer pops the old slot (scratch copy kept) | KILLED — `test_an_http_undo_is_read_by_the_next_request` |
| Q0b | 0 | restore re-keys only outside a request | KILLED — `test_a_saved_snapshot_restore_is_read_by_the_next_request` |
| Q0c | 0 | `apply_undo` without `rekey_context` | KILLED — `test_an_http_undo_is_read_by_the_next_request` |
| Q1a | 1 | `reset_network` clears on `prev.solver_state` (no copy) | KILLED — `…survives_a_template_create_and_reactivation` |
| Q2a | 2 | B1 only `/api/network/` (drops `/api/io/`) | **SURVIVED** (S-2) |
| Q2b | 2 | `tool_edits_network` only for PUT routes | KILLED — `…derived_from_tool_routes_plus_one_pinned_list` |
| Q2c | 2 | `reset_network` does not carry the counter | KILLED — `test_undo_bumps_on_the_context_later_requests_read` |
| Q2d | 2 | `set_network` does not bump (B3) | KILLED — `test_set_network_bumps_the_carried_revision` |
| Q2e | 2 | `dirty_state.revision(ctx)` ignores `ctx` | SURVIVED — equivalent in the worker (copied context resolves the same ctx) |
| Q3a | 3 | `edited_since` uses `>` not `!=` | **SURVIVED** (S-2) |
| Q3b | 3 | `review_latest` coerces `null` to `False` | KILLED — `test_a_record_without_a_revision_reads_unavailable` |
| Q3c | 3 | capture off by one | KILLED — `…carries_the_revision_and_reads_unedited` |
| Q4a | 4 | `_restore_results_state` does not restore the counter | **SURVIVED** (S-2) |
| Q4b | 4 | load / import / restore do not map the mirror | KILLED — `test_the_revision_survives_save_and_reload` |
| Q4c | 4 | mirror mapped over an in-memory record | KILLED — `test_hydrate_keeps_a_newer_in_memory_record` |
| Q4d | 4 | `metadata.json` without `network_revision` | KILLED — `test_the_revision_survives_save_and_reload` |
| Q4e | 4 | only `done` records persisted | KILLED — `test_a_failed_and_an_aborted_record_are_restored_too` |
| Q4b1 | 4b | register only under a session (local mode skipped) | KILLED — `test_local_mode_template_project_keeps_its_study_across_a_switch` |
| Q4b2 | 4b | template create does not register | KILLED — same |
| F5a | 5 | hook fires only on an increasing revision | SURVIVED — a backwards move (restore) is untested; low impact (restore invalidates broadly) |
| F5b | 5 | hook skips the `eh_review` invalidation | KILLED — hook "exactly the two hub keys" |
| F5c | 5 | greeting hook enabled in Expert | KILLED — "Expert with edited_since_study: true → Not solved yet." |
| F5d | 5 | edited arm loses to review-stale | KILLED — "edited and review stale → edited wins" |
| F5e | 5 | card banner also from `review.edited_since_study` | SURVIVED — equivalent (same derived value) |
| F5f | 5 | hook ignores `enabled` | KILLED — "enabled: false never invalidates" |
| R4 | 6 | `gen !== _lockGen` removed from the re-acquire `.catch` (`projectActions.ts:311`) | KILLED — R4 test only (1 failed / 16 passed) |
| R7 | 6 | `if (gen !== _lockGen) return …` removed (`:358`) | KILLED — R7 test only (1 failed / 16 passed) |

27 mutants: 21 killed, 6 survived. Two survivors are equivalent (Q2e, F5e). Four are test gaps (Q2a, Q3a, Q4a → S-2; F5a → note in table).

The implementer's own log (`scratchpad/p33b/mutations.log`) shows 47/47 killed. I spot-checked R4 / R7 by re-running them, and they match.

## What must change for GO

1. **B-1:** a cross-project swap (load, template, import, restore of another project) must not share `_UndoState` with the context it replaces. Add red tests for local-mode template and the sessions template / load / restore sequences (`qa33b/test_qa_bleed*.py` are ready-made).
2. S-1 … S-4 are recommended in the same pass. S-3 is required by the row-4s rule.
