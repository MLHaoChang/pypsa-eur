# QA gate: P28 — honest state (A3, A4, C6, C10, plus P32 N1 and N6)

**Date:** 2026-09-30. **Reviewer:** independent QA gate (did not write the code).
**Scope:** `git diff 531ffe1da..HEAD` on `claude/epic-allen-k2t1c4` (HEAD `927dfab34`, 8 commits, backend and frontend).
**Contract:** deferred spec `2026-09-28-guided-mode-deferred.md` §0, §3 (P28), §8 and §9 (condition 11); the plan's "P28 phase note" and the P32 result (N1, N6 carried in).
**Scratch:** `/tmp/claude-0/-home-user-pypsa-eur/93e65137-63f8-5e19-b114-b55788c10af8/scratchpad/qa28/` (written `qa28/` below). No source or test in the repo was edited; mutations and repro tests ran in a `git archive` copy at `qa28/wt/`.

## Verdict: **NO-GO** — one blocker (B1), small and wording-only

Every gate row is green and the engineering is sound: the profile rule, the bound-profile hydration, the per-profile readiness, `/health` byte stability, the C10 listener and the N6 lock move all hold, and I killed 18 of my 20 mutants (the two survivors are an unreachable input and a deliberate probe; see Mutations). The one blocker is the phase's own subject: the new Guided "stale" sentence states a fact the signal behind it does not carry, and the smoke that pins it proves it false.

GO once B1 is fixed (one string in `ChatLaunchGreeting.tsx`, its test constant and the smoke constant). The owner decision (O1) is separate and does not block.

## Blockers

### B1 — the stale sentence says "the network changed since"; the signal only means "a later solve cleared the stored report"

- **Where:** `pypsa-gui/frontend/src/components/ChatLaunchGreeting.tsx:67-70`:
  `reviewStale ? 'A study has run, but the network changed since — run it again in Hub design.' : …`
- **Why it is false:** `eh_review.stale` is set by `services/adequacy/eh_review.py:455-460` only when the stored report is gone, and the store is wiped by any foreground `POST /simulation/run` (`routers/simulation.py:819-834`, `eh_reference_design_report=None`) whether or not the network changed. The implementer's own spec correction 2 establishes this (an edit does not set `stale`; only a solve does), but the sentence kept the premise the correction disproved.
- **Repro (the P28 smoke itself):** `smoke-guided.mjs` part (C) edits a bus, **reverts the edit** (`PUT … { x: bus.x }`), then runs `POST /api/simulation/run` on the unchanged network, and asserts the greeting reads "…the network changed since…". Run: `qa28/smoke-P28.log`, screenshot `qa28/p28/41-p28-c-guided-stale.png`.
- **The screenshot shows the contradiction on one screen:** the Hub design Results card says, correctly, "These results are from an earlier study; the network was solved since. Run again to refresh." The greeting beside it says "the network changed since".
- **Fix (cheap):** word the sentence after the signal, as the hub card already does, e.g. "A study has run, but the network was solved since — run it again in Hub design." Update `P28_STALE` in `smoke-guided.mjs` and `STALE` in `ChatLaunchGreeting.solvedState.test.tsx`. The string is spec §3.2's; the change is a one-line phase-note deviation justified by the spec correction 2.

## Owner decision O1 — an edit after a hub study does not make the greeting stale

**The gap.** After a finished hub study, the user edits the network. `eh_review.stale` stays false and live dispatch stays `none` (the hub solves a copy), so the Guided greeting keeps saying "A study has run on this network — its results are in Hub design." The smoke logs exactly this (`after the bus edit: eh_review.stale=false; greeting "A study has run on this network — …"`).

**Is it a real honesty gap?** Yes, but a narrow one, and not the greeting's alone:
- "on this network" is a present-tense claim about a network that has since changed. A Guided user who edits then asks the assistant is told the old results apply.
- The Hub design Results card has the same blind spot: it has no edit signal either. Fixing only the greeting would make the two disagree the other way.
- A second, smaller case is now worse than before P28: a hub study done **and** live dispatch `stale` (an earlier foreground solve, then an edit). Before P28 the greeting said "Solved earlier, but the results are stale — the network changed since."; now the "study done" branch wins and says the results are in Hub design. My probe mutant Q16 (let `dispatch === 'stale'` also select the stale sentence) survives the suite, so no test pins the current choice.

**Options.**
1. *Accept* — leave it; record the gap. Cheapest; the greeting stays confidently wrong after edits.
2. *A note in the greeting* — reword the done sentence so it makes no currency claim: "The last study's results are in Hub design." (drops "on this network"). Also let `dispatch === 'stale'` select the stale sentence when the hub study is done (Q16's change; the data is already on the page). FE only, no new signal.
3. *A backend network-revision signal* — record a network revision (an edit counter or topology/parameter hash) on the study record at study start; `eh_review` (and `/eh_study`) report `network_changed: bool` against the current revision. The greeting and the Hub design card both read it. Additive field, no new module, but it touches every write path's revision bump; its own phase.

**Recommendation: option 2 now (with B1's fix, same file, same commit), option 3 scheduled** as a later backend item that the Hub design card needs anyway. Option 2 makes every sentence true with data the page already has; option 3 is the only way to say "the network changed" honestly, and should be done once for both surfaces rather than bolted onto the greeting.

## Gate rows

| Row | Command (cwd) | Result |
|---|---|---|
| 1 | Implementer's full suite (`scratchpad/p28/be-full.log`): 6673 passed, 31 skipped, 11 deselected. Checked against HEAD: `pytest tests/ -m "not slow" --collect-only -q …` (`pypsa-gui/backend`) → **6704/6715 collected (11 deselected)** = 6673 + 31. The log was started before the backend commit `f3677a6a4` landed (00:40, log ended 00:55 after 45 min), but the count matches HEAD exactly and row 2 re-runs every changed backend test at HEAD. | accepted (`qa28/collect.log`) |
| 2 | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest <14-file set> tests/test_chat*.py tests/test_llm_settings_api.py -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`) | **1608 passed, 19 skipped**, exit 0 (`qa28/row2.log`) |
| 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`) | exit 0, 0 errors (`qa28/tsc.log`) |
| 4 | `npx vitest run` (`pypsa-gui/frontend`) | **242 files / 2738 tests passed**, exit 0 (`qa28/vitest.log`) |
| 4s | ×10: `npx vitest run src/components/ChatPanel src/components/ChatLaunchGreeting src/store/chatStore src/store/uiStore src/App. src/hooks/useChatProfiles src/components/ApiKeySetup src/components/ProjectMismatchBanner src/utils/projectActions` | **10/10 green**, 594 tests each (`qa28/stress.log`, `stress-*.log`) |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P28 --out qa28/p28` | **PASS**, 43 screenshots (`qa28/smoke-P28.log`) |
| 5 | same, `--phase P32` / `P27b` / `P26` | **PASS** 14 / **PASS** 54 (project 2 greeting is the C6 sentence; FMEA settled 8 / 4 / 6) / **PASS** 38 (`qa28/smoke-*.log`, `qa28/smokes.log`) |
| 7 | `git diff 531ffe1da -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` | 28, all test-store setup plus the C10 listener; no new product `uiMode` branch. The greeting's Guided arms use the existing `guided` flag; the Expert arms are unchanged and Expert never fetches `eh_review` (test + mutant Q12). |

After the smokes no uvicorn, vite, stub or chromium process is running and ports 8000, 5173 and 11999 are free. The working tree is clean apart from this file.

### Screenshots read
- `39-p28-a-bound-send-enabled.png`: Expert, Island Microgrid; dropdown **Smoke stub 2**, the turn "hello from P28" / "Saved." rehydrated, "typed after reload" in the input, **Send enabled**, no gate (active profile is the keyless Anthropic one).
- `40-p28-b-picked-no-key-offer.png`: H2 project, Expert; dropdown Smoke stub 2 after the cross-wire confirm; greeting "Not solved yet." with **no key offer**; Send enabled.
- `41-p28-c-guided-stale.png`: Guided, Hub design at Results with the amber card "the network was solved since"; the greeting says "the network changed since" (B1).
- `42-p28-d-guided-c6.png`: fresh project, Guided at Site; greeting "No study has run yet — start in Hub design." The key offer and the inline key form show because Claude Sonnet (keyless) is active — correct.

## Security

- **Session ownership intact.** `be4d5ed2f` and `83bde2cac` are ancestors of HEAD. The diff does not touch `services/chat_service.py`, `session_owner_allows`, the owner recorded on creation in `/history` (`routers/chat.py:820-823`) or `/stream` (`:1124-1131`), or the `/confirm`, `/rewind`, `/abort` checks (`:1338`, `:1384`, `:1412`).
- **Can another user read someone else's `bound_profile_id`?** Only in the sense they already could read more: `/history` returns the active project's `turns`, and every turn record carries its `profile_id`. For a freshly minted session `bound_profile_id` is that same recorded id. For an already-live session (`:849-854`) it is the live binding, which can differ from the transcript only between a same-wire rebind and its turn being written. The value is a profile id from the member-level menu, not a secret. `/history` does not check session ownership (pre-existing: it also rebuilds a live session's message list); P28 adds no new reach. No leak worth blocking.
- **Per-profile `chat_ready` in hosted mode.** Profiles and their keys are instance-wide (`llm_config` in app data; key writes are super-admin only, `routers/chat.py:237`). No per-user or per-org key exists, so the boolean says nothing about another user. It reveals, to any authenticated member, which configured profiles have a key env set — the same fact `/health` already gives for the active one. No key name or value: `test_profiles_carry_no_key_name_and_only_the_four_fields`, and my mutant QB3 (add `key_env`) is killed. Anonymous callers get 401 (`test_profiles_refuse_a_caller_with_no_user`).
- **`/health` byte-stable.** The only change is `chat_ready = _profile_chat_ready(active_profile)`. I checked the helper against the old inline expression over 27 combinations of auth × key_env × env value: identical type and value. The key set is pinned (`test_health_key_set_is_unchanged`; my mutant QB4 killed by it and by `test_chat_sse.py`).

## N6 lock correctness

- **Can a chat rebind steal another user's lock?** No. `moveProjectLock` (`utils/projectActions.ts:538-542`) calls the same `acquireProjectLock`/`releaseProjectLock` as `switchToProject`. The server refuses a foreign acquire (409 → read-only, `locked-by-user`) and makes a non-holder's release a no-op (`services/project_locks.py:89-94`). The backend's foreign-lock write gate (`main.py`, `_foreign_lock_gate_exempt`) remains the authority either way.
- **`to: null`.** Releases the old lock, stops its heartbeat, claims nothing; the tab ends unbound and writable (an unbound network has no lock). Consistent. Covered by the test and my mutant Q8.
- **Races (non-blocking, reproduced in `qa28/QA28.race.test.tsx`, run in the scratch copy):**
  - *N-a: a heartbeat answered after the rebind.* Heartbeat for X in flight → rebind X→Y, Y held by Bob → read-only → X's heartbeat success arrives → `startLockHeartbeat`'s `.then(res => _applyLock({ ok: true … }))` (`projectActions.ts:270`) has no `_heartbeatProject` guard, so the tab flips to **writable** on Y. Output: `readOnly= false reason= writable project= Y`. UI-only (the backend gate still refuses the writes), needs the rebind inside one heartbeat RTT, and the same unguarded line exists for `switchToProject`. The 409 branch also sends `acquireLock(X)` (`:279`) before its guard, so it can re-claim X after the release for up to the 120 s TTL.
  - *N-b: two rebinds in one turn with out-of-order acquire responses.* X→Y→Z where Y's `POST /lock` answers after Z's: the tab ends heart-beating **Y** (`lastHeld= Y`, current project Z). Y stays locked by this user while the tab lives (the heartbeat's 409 path re-acquires it), and Z's lock lapses after 120 s. `switchToProject` is serialized by `projectSwitchInProgress`; the rebind path fires `void moveProjectLock(...)` (`ChatPanel.tsx:2372`, `:2386`) unserialized. Needs POST Y slower than the second tool's execution plus POST Z, so rare in practice.
  - **Suggested fix for both (one follow-up):** a module-level move generation — `moveProjectLock` bumps it, `acquireProjectLock` ignores a result whose generation is stale — and guard the heartbeat's success `.then` and the 409 re-acquire on `_heartbeatProject === projectId` before sending or applying.

## Other notes (non-blocking)

- **N-c: C-4 path reports "no binding" while the session is bound.** When the recorded profile was deleted, `/history` still binds the freshly minted session to the legacy-resolved profile (`routers/chat.py:797-803`, then `:841-843`), but reports `bound_profile_id: None` (`:846-848`). `/stream` with no `profile_id` keeps that binding (`:1207`), so the first turn after the reload runs on the legacy profile while the gate, dropdown and key offer follow the active one. Repro `qa28/wt/pypsa-gui/backend/tests/test_qa28_repro.py`: `bound_profile_id: None | session.profile_id: anthropic-sonnet | active: ollama-like`. The spec §3.6 row asked for null, and `session_init` corrects it after one turn, so it is not a blocker. Better: report `resolved_profile.id` (configured by construction) on the fresh-mint path; keep null only for a live binding that names a deleted profile (where `/stream` refuses anyway).
- **N-d: `moveProjectLock` has no direct unit test.** Mutant Q7 (release even when `from === to`) survives. Both callers exclude that input today, so it is equivalent now, but a direct test would pin the helper's contract.
- **Two-user harness deferral** — see deviation 5.

## Mutations (`qa28/mutate.py`, log `qa28/mutations.log`)

My own set, independent of the implementer's. **18 of 20 killed.**

| # | Mutation | Result |
|---|---|---|
| Q1 | profile rule drops the bound profile (`picked` only) | killed |
| Q2 | bound wins over the pick | killed |
| Q3 | hydrate does not set `boundProfileId` | killed |
| Q4 | key offer ignores readiness | killed |
| Q5 | key offer treats unknown as ready | killed |
| Q6 | `moveProjectLock` never acquires | killed |
| Q7 | `moveProjectLock` releases when `from === to` | **survived** (unreachable from both callers; N-d) |
| Q8 | `to: null` keeps the lock | killed |
| Q9 | storage listener overrides this tab's explicit choice | killed |
| Q10 | storage listener drops the key filter | killed |
| Q11 | stale sentence ignored | killed |
| Q12 | review fetched before the study is done | killed |
| Q13 | `resetForProjectSwitch` keeps `boundProfileId` | killed |
| Q14 | `session_init` does not update the bound profile | killed |
| Q15 | dropdown ignores the bound profile | killed |
| Q16 | probe: hub done + `dispatch === 'stale'` also selects the stale sentence | **survived** (no test pins either choice; O1 option 2) |
| QB1 | `chat_ready` checks `ANTHROPIC_API_KEY` for every bearer profile | killed |
| QB2 | a live session reports the transcript's profile | killed |
| QB3 | `/profiles` leaks `key_env` | killed |
| QB4 | `/health` gains a field | killed |

## Judgement on the implementer's claims

**Spec corrections.**
1. *`/chat/profiles` answers 200 in local mode, not 401* — **correct.** `main.lifespan` seeds the local identity and the middleware injects it; pinned by `test_local_mode_seeds_its_identity_at_startup_so_profiles_answer`, and the smoke reads the route in local mode. The 401 path is the anonymous hosted caller, also pinned. Spec review condition 11 was wrong on this point; the FE fallback is kept anyway.
2. *An edit does not make the review stale; only a later solve does* — **correct** (`eh_review.py:455-460`, `simulation.py:819-834`; the smoke logs `stale=false` after the edit). The consequence was not carried through to the sentence (B1) or to the edit case (O1).
3. *Re-activating a project does not restore its hub record* — **plausible and consistent with what I saw** (the smoke's part (C) re-runs the study when `/eh_study` is not `done`; it passed on my run). I did not trace the storage of the record. It deserves its own OPEN-ITEMS line: a user who re-opens a project loses the Guided "study done" state.

**Deviations.**
1. *Dropdown shows the bound profile* — **agree.** Showing the active profile while Send follows the bound one would be dishonest, and the cross-wire check must compare against the bound wire. Mutant Q15 killed.
2. *`session_init.profile_id` updates the bound profile* — **agree.** It is the session's real binding; without it the gate follows later active-profile changes until a reload. `profileId` is still never pinned from the frame. Q14 killed.
3. */health fallback on any list error* — **agree.** It is fail-open for non-active profiles and exact for the active one; it also keeps the existing `getChatHealth` mocks untouched as §3.1 requires.
4. *Split key-offer step* — **agree.** After (A)'s turn the greeting is hidden, so a key-offer check there would be vacuous; (B) runs on an empty transcript and has a positive control.
5. *Two-user auth harness deferred* — **accept, with a caveat.** The adapter-level test drives the real handler and lock code, and the backend's refusal is covered by `test_project_locks.py`. But N-a and N-b show that ordering is where this code is weak, and a single-user adapter test does not exercise ordering. When the harness is built, it should include a two-rebind turn.

## Evidence index (`qa28/`)
`collect.log`, `row2.log`, `tsc.log`, `vitest.log`, `stress.log` + `stress-1..10.log`, `smoke-P28.log`, `smoke-P32.log`, `smoke-P27b.log`, `smoke-P26.log`, `smokes.log`, `p28/` (screenshots), `mutate.py`, `mutations.log`, `QA28.race.test.tsx` (N-a, N-b), `wt/pypsa-gui/backend/tests/test_qa28_repro.py` (N-c).
