# Floating companion launcher

Status: done
Type: task
Blocked by: none

The closed assistant is a floating character with Compose, Dictate, and Live, as
specified in [the live conversation spec](../spec.md) under Companion launcher.
Compose opens the existing assistant console and focuses its composer. Dictate
opens the same console and the reviewed dictation panel from PR #106. Live is
visible on the companion and its click does not open the console, start the
microphone, or start a paid media session. Connecting that session, lighting
the character, and doing the work on the current page belong to
[issue 03](03-conversation-ui.md).

Provider presets beyond the launcher are in the companion plan, not a new wire.

Done when the collapsed launcher overlays the canvas, Compose and Dictate do
only the actions above, Live leaves the console closed, reduced motion disables
decorative animation, and the active profile chip opens the existing profile
picker. See
[the companion plan](../../../docs/superpowers/plans/2026-10-10-assistant-companion.md).

## Comments

2026-10-11: Verified each done criterion against existing tests (frontend, `npx vitest run`):

- Collapsed launcher overlays the canvas: `CompanionLauncher` is `fixed bottom-6 right-6`;
  `AssistantDock.test.tsx` "reserves no column width while collapsed", "shows the launcher when
  collapsed", "hides the floating companion while the console is open", "keeps ChatPanel mounted
  while collapsed"; `ProjectsHomePage.assistant.test.tsx` (landing page keeps the companion);
  `ToasterHost.test.tsx` (`COMPANION_TOAST_OFFSET`).
- Compose does only compose: `AssistantDock.test.tsx` "expands when the launcher is clicked"
  (`assistantEntry='compose'`); `ChatPanel.companionEntry.test.tsx` "compose opens the typed composer
  and leaves dictation closed" (composer focused, no `getUserMedia`, entry consumed).
- Dictate does only dictate: `AssistantDock.test.tsx` "offers reviewed dictation from the collapsed
  companion"; `ChatPanel.companionEntry.test.tsx` "speak opens the review panel and does not start a
  recording or a chat turn".
- Live leaves the console closed: `AssistantDock.test.tsx` "does not open the console when Live is
  clicked" and the Live utterance/queue/walk tests (console stays closed).
- Profile chip opens the existing picker: `ChatPanel.companionEntry.test.tsx` "profiles focuses the
  existing model select" plus the failed-list/empty-list/dock-closed entry-clearing cases.
- Reduced motion: `CompanionLauncher.test.tsx` drops `companion-idle` and sets `data-motion="off"`.

Real gaps found and closed by new tests only (no component change was needed): (1) nothing asserted
that Live does not start the microphone, a `MediaRecorder`, an `RTCPeerConnection` or any network
request on click, now `AssistantDock.test.tsx` "does not start the microphone, a recording or a media
session when Live is clicked"; (2) the profile chip's dock-open + `assistantEntry='profiles'` hand-off
was untested at the dock, now "opens the console on the profile list from the profile chip"; (3)
reduced motion was only checked through the class, now a source check in `CompanionLauncher.test.tsx`
that every `animation`/`transition` in `index.css` sits behind `prefers-reduced-motion:
no-preference` and the actions stay visible otherwise. Result: 9 companion-related files, 65 tests
passed. Live's later in-place behaviour (session binding, caption, harness forwarding) is issue 03/02
scope and is unchanged by this check.

