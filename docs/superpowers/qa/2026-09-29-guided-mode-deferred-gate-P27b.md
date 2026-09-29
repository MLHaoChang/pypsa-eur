# QA gate — P27b (frontend data safety: A2, A5, A6, A1-FE)

**Date:** 2026-09-29. **Reviewer:** independent QA gate (did not write the code).
**Scope:** `git diff 88aabcfe0..HEAD` on `claude/epic-allen-k2t1c4` (HEAD `7b470289d`, 11 commits). The diff touches only the frontend and the plan; the backend is unchanged.
**Contract:** spec `2026-09-28-guided-mode-deferred.md` §0, §2 (P27b), §8, §9; the plan's P27b phase note and the "P27a result" carried items; spec-review conditions 3, 4, 5, 7 and 11; the P27a gate.
**Scratch:** `/tmp/claude-0/-home-user-pypsa-eur/93e65137-63f8-5e19-b114-b55788c10af8/scratchpad/qa27b/`, referred to below as `qa27b/`.

## Verdict: **NO-GO** — one blocker (B1)

Every gate row is green. The mismatch detection, the write block, the chat gate, the keepalive drop, the `to:null` rebind and the A5 / A1-FE work all do what the spec asks, and 16 of my 18 mutants are killed. One blocker remains.

In auth mode, the write block also refuses the multi-user **edit-lock** routes. Auth mode is the default build: `auth/config.ts` sets `authEnabled` unless `VITE_AUTH_ENABLED=false`, and `/api/health` flips it off only in local mode. The two recovery paths then fail as follows:

- **Switch** leaves the tab read-only. The UI says another user holds the lock.
- A mismatch that lasts longer than one heartbeat (45 s) makes the tab lose its lock. Reload does not get it back.

The spec requires that the block "cannot deadlock recovery" and that Switch works. Right now Switch changes the project but leaves the tab unable to edit. The fix is small; see B1.

## Blockers

### B1 — the write block refuses the edit-lock routes, so in auth mode Switch lands read-only and a mismatched tab loses its lock

**Where**
- The allowlist at `pypsa-gui/frontend/src/utils/projectMismatch.ts:43-54` has no entry for the lock routes:
  - `POST /projects/<x>/lock` and `DELETE /projects/<x>/lock` (`api/projects.ts:387-398`)
  - `POST /projects/<x>/lock/heartbeat`
- `ProjectMismatchBanner.tsx:52` calls `switchToProject(backend)`. That function runs `releaseProjectLock(currentProject)` and `acquireProjectLock(activated)` (`utils/projectActions.ts:509-511`) while `projectMismatch` is still set. The banner clears the mismatch only after `switchToProject` returns (`ProjectMismatchBanner.tsx:54`).
- The acquire is refused on the client with a 409. `acquireProjectLock` (`projectActions.ts:322-334`) treats any failure as "locked by another user":
  - It sets `readOnly: true, reason: 'locked-by-user'`.
  - It stops the heartbeat.
  - It logs "… is being edited by another user — opened read-only."
- The heartbeat (`projectActions.ts:266-300`, every 45 s) is also refused with a 409. It then tries one re-acquire, which is refused too, so it sets read-only, stops pinging and logs "Lost the edit lock". Reload (`ProjectMismatchBanner.tsx:32-44`) never re-acquires the lock, so the tab stays read-only after the mismatch clears.

**Repro.** Both probes run on a scratch copy of the frontend; the tree is untouched.
- `qa27b/fe/src/qa/probeLock.test.tsx`
  - Setup: `setAuthEnabled(true)`, the mismatch set to `{tab:'X', backend:'Y'}`, and the real `ProjectMismatchBanner`, real `switchToProject` and real `client`. An adapter answers every request with 200.
  - Action: click `project-mismatch-switch`.
  - Result: the adapter sees only `GET /projects/` twice, `GET /simulation/lock_status` and `POST /projects/Y/activate`. The console shows `[project_mismatch] POST /projects/Y/lock refused` and `DELETE /projects/X/lock refused`. The final state is `currentProject='Y'`, `projectMismatch=null`, **`readOnly=true`, `readOnlyReason='locked-by-user'`**. The test fails on "lands writable on Y".
- `qa27b/fe/src/qa/probeHeartbeat.test.tsx`
  - Setup: `acquireProjectLock('X')` succeeds, then the mismatch is set.
  - Action: advance 45 s, clear the mismatch (as Reload does), then advance two more heartbeats.
  - Result: `readOnly` is true after the first heartbeat and **still true** after the Reload and the two further beats.

**How it happens in practice.** In hosted mode the active-project pointer is stored per session (`services/active_project.py`: `sessions.active_project_id`). Two tabs of one browser session share it. Opening project Y in tab B leaves tab A mismatched on X, and that is the main way a mismatch happens in hosted mode. The smoke does not catch this because it runs in local mode, where there is no lock.

**Fix (suggested).**
1. Add the lock routes to `mismatchAllows`: `POST|DELETE /projects/<x>/lock` and `POST /projects/<x>/lock/heartbeat`. They move lease metadata, not the live network, so they are safe for the same reason the layout PUT is.
2. Add cases for them in `client.mismatch.test.ts`.
3. Add an auth-mode case to `App.recovery.test.tsx` (or a new test): banner Switch → `readOnly === false`.

Optionally, Reload could call `acquireProjectLock(tab)` so that a lock lost for any other reason is also recovered.

## Notes (not blocking)

1. **A study tab polls `/network/meta` every second, indefinitely.** `refetchInterval` in `hooks/useProjectMismatchDetection.ts:42-49` does not know `isStudy`. A planning → dynamics study tab's `currentProject` never equals the backend's binding, so `suspect` stays true and the interval stays at `confirmMs` (1 s). The detection effect then correctly ignores every sample. Measured with fake timers (`qa27b/fe/src/qa/probePoll.test.tsx`, `qa27b/poll.out`), meta fetches over 60 s:

   | Tab state | Fetches |
   |---|---|
   | Agreeing | 13 |
   | Mismatched | 14 |
   | Unbound backend | 13 |
   | **Study tab** | **61** |

   The route is a cheap in-memory read, so this is not a storm, but it is five times the intended rate. The fix is to pass `isStudy` into the interval and return `idleMs` for a study tab.
2. **`structuralSharing: false` is redundant.** Mutant M14 removes it and the suites stay green. The effect already depends on `seq` (the query's `dataUpdateCount`), which increases on every fetch even when the data is identical. The option costs a re-render of every `nk(project,'meta')` observer each sample (StatusBar, TopBar, AppHeader, Sidebar, OverviewPanel, the greeting). It is harmless at a 5 s cadence, and I recommend dropping it. Mutant M5 (removing the `p.at !== seq` "distinct sample" guard) also survives. It is defence in depth, since the effect runs once per sample in practice, but no test pins it.
3. **Baseline meta cadence.** The App hook adds a 5 s observer next to TopBar (10 s), AppHeader (30 s) and Sidebar (30 s), about 0.37 requests/s per tab instead of about 0.17. This is acceptable for an in-memory route, and each request also re-checks the session ACL.
4. **Detection latency means writes can land in the wrong project for up to ~6 s.** In that window, before two samples are taken (5 s idle + 1 s confirm; the smoke measured 5.5 s, the implementer 6.3 s), edits from the stale tab still land in the backend's project. Saves are still caught by the backend identity guard. This is a limit of the design, and much better than before.
5. **While mismatched, the tab shows the backend's data under its own project name.** Reads are never blocked, so the canvas and tables show the other project's content. Screenshot `qa27b/smoke-P27b/51-p27b-chat-gated.png` shows H2-hub buses (`grid 220 kV`, `hub`, `h2`) under "P27b Data Center". The banner makes this clear enough.
6. **`/chat/<sid>/confirm` is allowlisted (per spec).** The Guided write-confirmation Approve button stays live while mismatched. Approving a write proposed in a turn that started before the mismatch runs that write on the backend's project. The in-flight turn's other tools do the same regardless. The spec allowlists `/chat/*`, so this is out of scope for P27b; consider gating the confirm card on `projectMismatch` in P28.
7. **Liveness of `useLiveStudyRunning`.** It learns about a sweep from the shared `fmea_modes` cache. A sweep started somewhere the hub cards cannot see (for example by the assistant) is picked up only on the next fetch of that key (mount or focus). Until then the Guided buttons stay enabled, and the backend's P27a 409 is what catches the edit. This is acceptable.
8. **Overlapping switches.** The `projectSwitchInProgress` fence is a boolean, not a counter. Overlapping switches (tab B then tab C quickly) can drop the fence early. The two-sample rule still covers the window, and the flag existed before this phase.
9. **The `to:null` decision is safe.** `setCurrentProject(null)` is an existing, supported state, and `ImportExport.tsx:133-142` already does exactly this for a raw network import, for the same autosave reason. The hub, Guided and Expert views already handle `currentProject === null`:
   - `useLiveStudyRunning` is disabled when there is no project.
   - The detection hook clears for `!tab`.
   - The recovery effect's new line only returns early.

   A `null → null` frame is a no-op (tested). Mutant M10 is killed.
10. **The phase note lists 7 deviations, not 9.** I judged items 1–7 below. Items 8 and 9 do not exist. The three "smoke findings fixed during the phase" are tooling notes, not deviations.

## Safety and design review

### Write paths while mismatched

| Path | Where | Behaviour while mismatched |
|---|---|---|
| Every axios `post/put/patch/delete` | `client` (all API modules; `projects.ts`, `llmSettings.ts`, `localSettings.ts`, `AuthProvider.tsx` and `LoginPage.tsx` import `axios` only for `isAxiosError`) | Refused on the client with a quiet 409 (`client.ts:151-165`), except the allowlist below |
| Allowlist | `projectMismatch.ts:43-54` | `POST /projects/<x>/activate`, `/chat/*`, `/local-settings/*`, `/auth/*`, and `PUT /projects/<tab>/layout`. **Missing: the lock routes (B1)** |
| Chat stream (raw `fetch`) | `api/chat.ts:104` | Send is disabled and `onSend` returns early (`ChatPanel.tsx:2498`). The card queue is dropped with a toast (`:2549-2556`) |
| Unload keepalive DELETE (raw `fetch`) | `pendingEdgeDeletes.ts:98-111`, called from `TopologyCanvas.tsx:2103` | Dropped with one WARN |
| `flushPendingEdgeDeletes` (save path) | `pendingEdgeDeletes.ts:121` | Dropped. The 5 s timer commit goes through axios and is blocked there |
| Uploads (raw `fetch` POST / DELETE) | `api/uploads.ts:81-136` | Allowed by design: scoped to a project by name in the URL |
| Layout keepalive PUT (raw `fetch`) | `topologyLayoutStore.ts:118` | Allowed by design: the tab's own project, by name |
| `sendBeacon` | `topologyLayoutStore.ts` comment only | Not called |
| SSE | `simulation.ts:1816`, `SolveQueuePanel.tsx:209` | GET streams only; no write |
| WebSocket | none | — |
| `/api/health` raw fetches | `AuthModeProvider`, `AuthMismatchGate` | GET |
| Autosave / manual Save | `Sidebar.tsx:731-745` | Autosave is silent with a WARN; manual Save shows an error toast and the sentence as the button title |

### Deadlocks, false positives, multiple tabs

- **Can recovery deadlock?**
  - Reload is a `GET /projects/{name}`, so it is never blocked. The smoke (a) exercises it: banner gone, edit PUT 200, Send enabled again.
  - Switch goes through `activate`, which is allowlisted. It works in local mode (tested) but lands read-only in auth mode (B1).
  - Opening a third project from the Sidebar: the outgoing save is refused quietly (`saveProjectQuietly` never throws), activate passes, and the next meta sample clears the mismatch. In auth mode the same lock problem applies.
- **False positives on a legitimate switch or open.**
  - The fence covers Sidebar open, ProjectTabs, CommandPalette, ScenariosPanel and the banner. Template creation (`useCreateFromTemplate`) and the chat `project_rebound` frame are not fenced, but the backend binds a few milliseconds before the response or frame (`projects.py:1375-1390`; `chat_service.py:3490-3503`), so the two-sample rule covers them.
  - In the P27b smoke (b), the Guided template switch to project 2 shows no banner (`54-p27b-project-2.png`).
  - First load after login: a new session has a null binding, so the mismatch cannot be raised and the recovery reload runs.
  - The `?project=` open goes through the fenced switch.
- **Multiple tabs.** Tabs alternate the banner as each one reloads its own project, which the spec's risk table expects. In hosted mode this case triggers B1.
- **Polling storms.** None:
  - The `fmea_modes` latch ends. With 1 or 3 observers, a 20 s running period followed by 20 s done gives 12 fetches (initial + 10 + one extra tick). Idle over 60 s gives 1 fetch (`qa27b/fe/src/qa/probeFmeaPoll.test.tsx`, `qa27b/fmeapoll.out`).
  - Mutants M12 (no extra tick) and M13 (latch never released) are killed.
  - Meta polling: see notes 1–3.
- **Expert unchanged.** Row 7 finds 3 `uiMode` lines: two test-store setups and one unchanged context line (`DelegateButton`'s `const mode`). No `uiMode` branch was added, and no `*.expertUnchanged.*` snapshot changed; they are green in row 4. Expert gains the banner, the write block and the Properties toast wording. The first two are specified for both modes. The toast wording (`blockerMessage` instead of `e.message`) is the carried P27a item.

## Deviations (phase note 1–7)

| # | Deviation | Judgement |
|---|---|---|
| 1 | The ImproveCard switch case asserts "at most one read, never a poll" instead of "no fetch" | **Accept.** A1-FE requires one read of the new project's modes to know whether a sweep is running |
| 2 | Changed assertion: "not polled" becomes "≤ 1 read over 10 s" | **Accept**, for the same reason, and the justification is recorded |
| 3 | The ImproveCard mock spreads `importOriginal` | **Accept.** The shared interval is real, which is what M12 and M13 rely on |
| 4 | The A5 cases went into the existing `useStudyFinishedInvalidation.test.tsx` | **Accept.** The file already existed; the spec's "new file" was stale |
| 5 | `stop('uvicorn')` instead of `stop('backend')` | **Accept.** That is the process name the script already uses |
| 6 | The settle loop stays; the one-shot read is logged | **Accept.** It follows spec §2.2. The loop stays until ten stable runs. My run agreed on all three templates (8/4/6, the implementer's run gave the same), so the backend `finalising` fallback is **not needed** on current evidence |
| 7 | No backend change | **Confirmed.** `git diff --stat 88aabcfe0..HEAD -- pypsa-gui/backend` is empty |

Carried from P27a:
- The Properties refusal toast now shows the backend sentence (tested). The P27a smoke now asserts "is running".
- `project_rebound {to:null}` is handled, with a vitest (`ChatPanel.rebound.test.tsx`).

Spec-review conditions:
- 3 (allowlist, Reload refusal): met, and both refusal shapes are pinned.
- 4 (chat and keepalive gating): met.
- 5 (fence and two samples): met.
- 7 (restart mechanics: `stop`, re-`start`, health poll, activate, no reload): met.
- 11 (local-mode codes): P28's concern; `/local-settings` is allowlisted.

## Mutations (my own, on a scratch copy: `qa27b/fe`, `qa27b/mutate.py`, `qa27b/mutations.log`)

| # | Mutant | Result |
|---|---|---|
| M1 | Allowlist: activate removed | killed |
| M2 | Allowlist: layout PUT removed | killed |
| M3 | Switch fence ignored | killed |
| M4 | Two-sample rule: the first disagreeing sample raises | killed |
| M5 | Distinct-sample guard (`p.at !== seq`) removed | **survived** (note 2) |
| M6 | Chat Send gate dropped | killed |
| M7 | Card queue not dropped | killed |
| M8 | Keepalive drop bypassed | killed |
| M9 | Save-path flush drop bypassed | killed |
| M10 | `to:null` rebind ignored | killed |
| M11 | Autosave guard ignores the mismatch | killed |
| M12 | No extra tick | killed |
| M13 | Latch never released | killed |
| M14 | `structuralSharing:false` removed | **survived** (note 2: redundant) |
| M15 | CardShell footer delegate not disabled | killed |
| M16 | Banner Switch does not clear the mismatch | killed |
| M17 | DELETE not treated as a write | killed |
| M18 | Sidebar identity 409 does not raise the mismatch | killed |

16 of 18 killed. Neither survivor hides a safety defect.

## Evidence (gate rows)

| Row | Command (cwd) | Result |
|---|---|---|
| 1 | `git diff --stat 88aabcfe0..HEAD -- pypsa-gui/backend` (repo root) | empty. The P27a full suite (6660 passed) stands |
| 2 | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest <70 files: the 14-file set + tests/test_chat*.py + test_chat_tool_dispatch_loop_seam.py test_save_guards_seam.py test_adequacy_study_swap_guard.py test_adequacy_swap_guard_callsites.py test_stub_openai_endpoint.py> -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`) | **1598 passed, 19 skipped**, exit 0 (`qa27b/row2.log`, list in `qa27b/row2.files`) |
| 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`) | 0 errors (`qa27b/tsc.log`) |
| 4 | `npx vitest run` (`pypsa-gui/frontend`) | **238 files, 2668 passed**, exit 0 (`qa27b/vitest.log`) |
| 4s | `for i in $(seq 10); do npx vitest run src/App src/pages/hubDesign src/pages/results/FmeaTab src/hooks/useStudyFinishedInvalidation src/api/client src/layout/Sidebar src/components/ChatPanel src/utils/pendingEdgeDeletes src/utils/projectActions src/layout/PropertiesPanel src/hooks/useStartFmeaSweep; done` (`pypsa-gui/frontend`) | **10 / 10 green**, 58 files, 695 tests each (`qa27b/stress.summary`) |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P27b --out qa27b/smoke-P27b` (`pypsa-gui/frontend`) | **PASS**, 54 screenshots. Banner after 5.5 s. (c) one-shot reads 8 / 4 / 6 = settled 8 / 4 / 6 |
| 5 | Same command with `--phase P27a`, `P26`, `P25`, `P22.9` | **PASS** each (14 / 38 / 12 / 10 screenshots). No uvicorn, vite or chromium processes left |
| 7 | `git diff 88aabcfe0 -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` | 3 (see above). Signed |

Screenshots read:
- `49-p27b-mismatch-banner`: banner text and both buttons.
- `51-p27b-chat-gated`: the gate sentence and Send disabled.
- `54-p27b-project-2`: no banner after the legitimate template switch.

## To reach GO

Fix B1 (allowlist the three lock routes and add a test for them, plus an auth-mode Switch case that ends writable). Then re-run rows 3, 4, the 4s subset, and smoke P27b. Notes 1 and 2 are recommended in the same change but are not required.
