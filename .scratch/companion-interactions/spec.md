# GridSpine companion modes and dimensional presence

Status: ready-for-human — application implementation prepared; exact-head UX review and current-head CI remain pending

Owner requested both implementation and design/spec updates on 2026-10-20. Design handoff: https://github.com/MLHaoChang/pypsa-eur/pull/436. This companion patch is separate from the full workspace/brand integration and integrated-planning native adapters.

## Behavior

- A dimensional glass companion replaces the flat robot. Hover/focus offers Write, Dictate and Live chat; tapping/clicking the character opens the same choices. Keyboard focus, Escape, outside dismissal and touch work without hover-only dependence.
- Write keeps the native `onCompose` entry. Dictate keeps native reviewed dictation; selecting its entry never begins recording or sends. Live chat keeps the existing in-page text composer, profile, request binding and harness admission. No new conversation engine.
- Continuous voice remains unavailable, with a visible explanation. Do not mistake the open live-text presentation for microphone capture/playback or a model connection.
- A short hover greeting differs from restrained live-open breathing/orbit. Actual running/queued/approval/input/error labels remain authoritative and media stays off in this launcher. Reduced-motion preference disables decorative animation, including when it changes at runtime.
- Opening/closing a mode menu does not send, cancel, change a project or decide approvals. The existing live-text close behavior and retained draft remain intact. The UI fits the workbench and a narrow viewport.

## Workflow alignment and integration

This changes only the one assistant overlay, available in Guided and Expert. It introduces no model inputs, flow-section completion claims or navigation to unrelated parameter editors. Existing compose/dictation/in-page-chat callbacks remain the ownership boundary. It follows the current project-flow contract in `.scratch/project-workflow/design.md`.

The standalone design documents in PR #436 cover integrated planning years and example entry; those native workflow changes are not implemented by this companion patch. This also excludes the separate 85-file dirty branding worktree. A future branding merge must reconcile overlaps in `CompanionLauncher.tsx`, its test, `index.css` and the decorative `Pearl.tsx` rather than overwrite this behavior.

## Verification

- Added a failing interaction test first: baseline lacked the accessible character/action disclosure. Existing checks passed around that missing behavior before implementation.
- 82 focused tests in five component/dock/checked-opt-in suites passed; 23 further tests in three readiness/live-admission/dictation suites passed. These eight suites are distinct (105 tests total).
- `npm run build` passed TypeScript and Vite production compilation. Vite reported its ordinary large-chunk warning.
- `scripts/smoke-companion-modes.mjs` uses the actual React component plus built application CSS in an isolated local fixture. Three browser journey groups cover pointer callbacks, stable character placement, native live-text input/draft/work/media distinctions, reduced-motion change, narrow layout, actual touch context, keyboard/Escape and outside dismissal. It does not call an API, model or media service.
- This is not authenticated whole-application E2E, a live voice call or a scientific test. Current-head remote CI and exact-head UX approval must be obtained before a production merge. The session has no callable interface to the coordinator/UX-reviewer session IDs, so no approval or reviewer contact is claimed.

## Reproduce

From `pypsa-gui/frontend`, run the eight named suites (`CompanionLauncher.test.tsx`, `AssistantDock.test.tsx`, `AssistantDock.noKey.test.tsx`, `AssistantDock.eviction.test.tsx`, `AssistantDock.readinessPending.test.tsx`, `ChatPanel.checkedOptIn.test.tsx`, `ChatPanel.liveUtteranceAdmission.test.tsx`, `DictationControls.test.tsx`), then `npm run build`. From the repository root, run `node pypsa-gui/frontend/scripts/smoke-companion-modes.mjs` after the build.

The smoke script uses installed esbuild and Playwright, falling back to the managed Playwright module. `GRIDSPINE_PLAYWRIGHT_MODULE` can point to another installed Playwright package.json; `GRIDSPINE_CHROMIUM_PATH` selects its Chromium executable. The script serves only its isolated local HTML/JS/CSS, closes its browser/server and removes temporary bundles.

No paid model/media calls, backend changes, deployment or full-domain claims are part of this patch. Independent domain review and real-use calibration remain pending.
