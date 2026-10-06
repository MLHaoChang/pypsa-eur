# QA gate: P32 (chat-created projects open in Guided; D-8 = (a))

**Date:** 2026-09-29/30. **Reviewer:** independent QA gate (did not write the code).
**Scope:** `git diff 6135c7601..HEAD` on `claude/epic-allen-k2t1c4` (HEAD `266c14a92`). The diff is `e7f07b37b` (code, tests, smoke, spec addendum) and `266c14a92` (phase note). Frontend only.
**Contract:**
- the deferred spec `2026-09-28-guided-mode-deferred.md` §7.1 (a–i) and spec-review condition 12;
- the parent spec `2026-09-27-guided-mode.md` §10 addendum D-8 and its pointers at §1 and §3.4;
- the plan's "P32 phase note".

**Scratch:** `/tmp/claude-0/-home-user-pypsa-eur/93e65137-63f8-5e19-b114-b55788c10af8/scratchpad/qa32/`, written `qa32/` below.

## Verdict: **GO**. No blockers.

All gate rows are green.

The change is two lines in the `project_rebound` handler (`pypsa-gui/frontend/src/components/ChatPanel.tsx:2362-2363`):
- The lines sit inside the `d.to && d.to !== currentProject` branch, after `setCurrentProject`, `setProjectName` and `touchTab`.
- They fire only for `create_project_from_template` (kind `'template'`) and `import_project_bundle` (kind `'file'`).
- The explicit-choice rule stays in `noteNewProjectCreated` (`store/uiStore.ts:662-676`). That function re-reads `ui-mode-explicit` from storage, so a choice made in another tab is adopted rather than overridden.

The fence holds, the dock stays open, and an explicit-Expert user is never flipped. The smoke confirms all three in a real browser. I killed 7 of my 9 mutants. The 2 survivors are test-coverage gaps, not defects (N1).

## Gate rows

| Row | Command | Result |
|---|---|---|
| 1 | `git diff --stat 88aabcfe0..HEAD -- pypsa-gui/backend` | empty. The backend is byte-identical to the P27a gate commit, so P27a's 6660-passed full suite stands. |
| 2 | cwd `pypsa-gui/backend`: `PYTHONPATH=… /tmp/claude-0/venv/bin/python -m pytest tests/test_guided_mode_prompt.py tests/test_stub_openai_endpoint.py tests/test_chat_tool_dispatch_loop_seam.py -p no:cacheprovider -W ignore -q -o addopts=""` | **87 passed** |
| 3 | `npx tsc --noEmit -p .` | exit 0, 0 errors (`qa32/tsc.log`) |
| 4 | `npx vitest run` | **241 files / 2701 tests passed** (P27b 2686 + 15 P32 cases), exit 0 (`qa32/vitest.log`) |
| 4s | ×10: `npx vitest run src/components/ChatPanel src/App. src/store/uiStore src/pages/hubDesign src/api/client.mismatch src/components/ProjectMismatchBanner src/hooks/useProjectMismatch` | **10/10 green**, 40 files / 618 tests each run (`qa32/stress.log`) |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P32 --out qa32/p32` | **PASS**, 14 screenshots |
| 5 | same, `--phase P27b` / `P26` / `P25` | **PASS** 54 (FMEA settled 8/4) / **PASS** 38 / **PASS** 12 |
| 7 | Expert unchanged | The product diff adds no `uiMode` branch. The change is a store call that no-ops for an explicit choice. The `*.expertUnchanged.*` snapshots pass in row 4. The explicit-Expert smoke context stays on the canvas with the Properties panel (`qa32/p32/14-p32-explicit-expert.png`). |

No processes are left running: I checked for uvicorn, vite, stub and chromium after the smokes. The working tree is clean apart from this file.

### Screenshots read

- `13-p32-implicit-expert.png`: the switch reads **Guided**; Hub design is open at step 2 (Site) for `p32-implicit-…`. The assistant dock is open on the right, and there is no mismatch banner.
- `14-p32-explicit-expert.png`: the switch reads **Expert**, with the canvas, the Properties panel and the bottom Buses table as before. There is no hub card and the dock is open.

## Contract check

| Item | Finding |
|---|---|
| §7.1(a) addendum | Present in the parent spec (`2026-09-27-guided-mode.md`, last section, "§10 addendum: D-8 (2026-09-28, product owner)"). The wording matches §7.1(a). §1 (`:41`), §3.4 (`:225`) and the §10 "G4 new projects" row carry *Amended/Superseded by* pointers. The old sentences are kept and struck by the pointer rather than literally replaced. This is acceptable: the pointer makes it unambiguous. |
| (b) trigger | The flip runs from the frame, only in the moved branch (`d.to !== currentProject`), and after `setCurrentProject`. My probe with a same-name rebind (`to === currentProject`) did not flip. |
| (c) explicit rule | This change adds no second rule; the existing rule re-reads storage. Two tests pin it: the explicit-Expert test and the other-tab (storage-only) test. My probe adds that another tab's explicit **Guided** choice is adopted as Guided and explicit. |
| (d) §3.7 on the flip | A non-Guided panel goes to `hubDesign`. With no panel open, the store leaves `null` and the App's P23 auto-open opens `hubDesign`; the smoke shows this. The dock stays open, in the same mounted `AssistantDock` outside the panel container. The compare rail is untouched (probe). |
| (e) Expert unchanged | Yes, see row 7. |
| (f) red tests | The tests live in `ChatPanel.rebound.test.tsx`, not `ChatPanel.sendRequest.test.tsx`. This deviation is justified: the rebound harness already mocks the toast. All the listed cases are present, plus extras: open tools, the four network imports, and the other-tab choice. |
| (g) smoke | Matches §7.1(g). The smoke reads "Active project: <name>" from the **toast**, a justified deviation: the transcript tool line reads `🔀 active project: a → b`. It also adds a no-banner check 8 s after the rebind and a no-`[project_mismatch]` check. |
| (h)/(i) | Rows as above. There is no `TOOLS`, description or prompt change, so no cache miss (row 2 green). |
| Condition 12 | §7.1 is fully specified (a–i), and the plan's §0 line reads "P32 scheduled after P27a per D-8 = (a)". |
| Mismatch fence | No false positive. `setCurrentProject(d.to)` runs before the flip, and the flip sends no write. The smoke saw no banner 8 s after the rebind, past the two-sample window, in both contexts. |
| Smoke API activation before each context | Acceptable. Without it, the second context opens the P25 project while the backend is still bound to the first context's new project. The P27b fence then rightly disables Approve: that is the fence working, not a P32 defect. |
| Network-import no-flip | **Agreed.** The four imports send `to: null`. They create no project, and the tab becomes unbound, so Guided would have nothing to show: the auto-open and the §3.7 swap both need `currentProject`. The UI path matches: `ImportExport.tsx:130` flips only for a bundle imported as a new project, never for a raw network import. A later `save_project` that binds the draft is a save, and the addendum excludes saves. |

## Notes (non-blocking)

**N1: two rebinding tools are not pinned as no-flip. Test gap only.**
- The `it.each` "not a new project" list covers `save_project_as`, `save_project`, `activate_project` and `load_project`.
- It misses `rename_project` and `restore_project_snapshot`, both of which are in `PROJECT_REBINDING_TOOLS` (`backend/services/chat_service.py:133-148`).
- Mutants M4 and M5 make either tool flip, and both **survive** the whole suite.
- Suggestion: add both names to the `it.each` at `ChatPanel.rebound.test.tsx` ("is not a new project").

**N2: the "no mismatch is raised by the flip" unit test is weak.**
- The rebound harness does not mount `useProjectMismatch`, so `projectMismatch` can only become non-null if the handler writes it directly.
- The real guard is the smoke's 8-second no-banner check, which passes.
- The phase note's "pinned in the unit test" overstates this test.

**N3: an implicit-Expert user with Results open lands on Results, not Hub design.**
- `setUiMode('guided')` keeps `results` (§3.7). The P23 auto-open then sees a panel already open and skips.
- So the brand-new project opens in Guided on a Results panel, with Results tabs coerced to the Guided pair and no results yet. My probe confirmed this.
- The user does **not** lose the Results panel. Its `ErrorBoundary` remounts for the new project, and the user can reach Hub design from the sidebar.
- This is stated in §7.1(d) ("unless a panel is already open for it"), so it is accepted as specified.
- A UX improvement would be to open `hubDesign` when the flip itself happened.

**N4: the only signal of the mode change is the switch itself.**
- `noteNewProjectCreated` shows no mode toast, as the phase note's deviation (4) says. §7.1's Risks line assumed a `uiModeToast` would also show. It does not; only "Active project: <name>" appears.
- The wizard paths behave the same way, so this is consistent.
- For an implicit-Expert user mid-conversation, whose canvas is replaced by Hub design and whose tool lines re-render in Guided wording, a one-line "Switched to Guided for the new project" toast would reduce surprise. This is for the owner to decide, not a gate item.
- The flip also persists: `ui-mode = guided` holds for later sessions. That is the same as G4 for any new project, and the owner accepted it with D-8.

**N5: §7.1(d) says "the transcript keeps scrolling", but the dock actually follows the rebind to the new project's own chat.**
- The P32 screenshot shows the new project's greeting, not the conversation that created it.
- This is P27a behaviour, the same in the explicit context, and not introduced by P32. It is worth stating in the spec.

**N6: auth-mode edit lock on a chat rebind is out of scope and not investigated.**
- The chat rebind does not call `acquireProjectLock` for `d.to`, whereas `switchToProject` does.
- This is pre-existing for every rebinding tool since P27a, and not changed by P32. It is flagged for whoever owns the auth-mode lock work.

## Mutations (`qa32/mutate.py`, `qa32/mutations.log`)

I applied each mutant to a scratch copy (`qa32/fe`, with node_modules symlinked), then ran `ChatPanel.rebound.test.tsx` plus `src/store/uiStore*`. The baseline was 74/74 green, including my 5 probe cases.

| # | Mutant | Result |
|---|---|---|
| M1 | `noteNewProjectCreated` drops the storage re-read (other-tab explicit) | **killed** (3) |
| M2 | template flips only when `from == null` | **killed** (5) |
| M3 | bundle calls `setUiMode('guided')` directly (bypasses the explicit rule) | **killed** (2) |
| M4 | `rename_project` also flips | **survived** (N1) |
| M5 | `restore_project_snapshot` also flips | **survived** (N1) |
| M6 | the flip closes the assistant dock | **killed** (1) |
| M7 | §3.7 pruning closes the panel instead of `hubDesign` | **killed** (4) |
| M8 | the flip sets `projectMismatch` | **killed** (16; partly through state leaking between tests, see N2) |
| M9 | `noteNewProjectCreated` marks the flip explicit | **killed** (6) |

**7 of 9 killed.** The implementer's own 7 mutants (phase note) are all killed and do not overlap M4, M5 or M9.

## Evidence

- `qa32/tsc.log`, `qa32/vitest.log`, `qa32/stress.log`
- `qa32/smoke-{P32,P27b,P26,P25}.log`, `qa32/smokes.done` (all exit 0)
- `qa32/p32/*.png`, `qa32/p27b/`, `qa32/p26/`, `qa32/p25/`
- `qa32/mutate.py`, `qa32/mutations.log`
- `qa32/fe/src/components/ChatPanel.rebound.test.tsx`: the probe block "QA32 probes", in the scratch copy only
