<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Live-voice 03: design plan (conversation UI)

Issue: [03-conversation-ui.md](03-conversation-ui.md). Base: master `0ab07339`.
Build starts after #1556 (11c-2) merges; until then only new modules and tests.
Chat is off here until the owner adds the key (KEY-LATER-1): every test runs on
stubs (`llm_fake`, mocked speech routes, a fake `getUserMedia`).

## What is already there (not rebuilt)

- Companion Live presence, typed Live composer, reviewed dictation, server
  notices in the caption (LC-07/07c), confirmation and choice cards rendered
  beside the companion (`AssistantDock.tsx:372`).
- Backend: durable thread with generation CAS, `refs.discussed`
  (`discuss_project_run`), speech start/end/segment/usage, `speech/output/claim`
  and `speech/barge-in` routes, mounted-view ACKs (`harness/navigation.py`),
  navigation epoch (`utils/navigationEpoch.ts`, newer user navigation wins).
- #1556 adds the monitor line (progress, Stop monitoring, Cancel) in ChatPanel.
  03 reuses it; no second progress card.

## Gaps this issue closes

1. Durable current view: `refs.current` exists in the thread shape but nothing
   writes it, so current view and discussed result are not kept distinct.
2. Dock header has no project, current-view or discussed-run context.
3. Live state is only off/starting/listening: no thinking or speaking, no
   playback client for `output/claim`, no Mute, no Stop speaking (barge-in),
   no End separate from closing the companion.
4. Opening the console ends Live (`useLiveSpeech(liveOpen)`); the spec says the
   session stays and its state moves into the console header.
5. One caption beside the companion: what was heard (final text) plus the
   latest outcome, including "File ready" with its download.
6. No download card in the console for agent-export deliveries.
7. Autoplay blocked and denied/stale microphone grants have no visible path.
8. No Chromium test proves that microphone tracks and audio close on every exit.

## Slices (one PR each, under 1,500 changed lines)

| Slice | Reviewer | What | Main files |
|---|---|---|---|
| 03a | GK B | Durable current view | `services/durable_conversation.py` (`set_current_view`), `harness/loop.py` (record at turn admission), tests |
| 03b | UX + GK B | Dock context bar and download card | new `components/AssistantContextBar.tsx`, new `components/DeliveryCard.tsx`, `AssistantDock.tsx`, `ChatPanel.tsx` (mount only) |
| 03c | UX + GK B | Live lifecycle, playback, controls | `utils/liveSpeechSession.ts`, new `utils/livePlayback.ts`, `hooks/useLiveSpeech.ts`, `CompanionLauncher.tsx` |
| 03d | UX | Console keeps Live; one caption | `AssistantDock.tsx`, `CompanionLauncher.tsx`, `CompanionOutcome.tsx`, `AssistantContextBar.tsx` |
| 03e | UX | Chromium lifecycle suite and shots | new `scripts/live-conversation-e2e.mjs`, shot script |

Order: 03a and the new modules of 03b/03c (with their unit tests) can be written
before #1556 merges; mounting into `ChatPanel.tsx` and the dock waits for it.

### 03a Durable current view (backend, GK B)

- `durable_conversation.set_current_view(thread_id, generation, view)` stores
  `{panel, results_tab, section, run_id, asset_id, window, mode, at}` in
  `refs.current`. Fields come from the turn's `ui_context`, filtered to that
  allow-list; nothing else is copied (no free text).
- Written when a turn (typed or admitted utterance) is admitted, under the
  thread lock, generation-checked. A stale generation writes nothing.
  It does not bump the generation and never touches `refs.discussed`.
- `resume_episode` keeps both; current never fills discussed, nor the reverse.
- The reference ladder keeps reading `turn_ui_context` for the on-screen rung;
  `refs.current` is the durable record a resumed episode starts from.
- Tests (`tests/test_lv03_current_view.py`): typed and utterance turns write it;
  stale generation 409 writes nothing; unknown keys dropped; discussed
  unchanged; resume copies both distinctly; non-owner thread refused byte-equal
  like the other thread routes; replica pin respected.

### 03b Dock context bar and download card (UI)

- Header line under "Assistant": `Project <name> · Viewing <panel › tab>` then
  `Discussing run <ISO time> · <ResultState word> · baseline run <ISO time>`.
  "›" appears only where the item is a link (Viewing is plain text; the run is
  a link through `showResultsTab(..., {from})`). An out-of-date run reads
  "Out of date". With no discussed run the second line is omitted.
- Source: `GET /durable-conversations/{id}` refs, refetched when a turn ends or
  the generation changes.
- `DeliveryCard`: "File ready: <name> · Download" with "Link valid until
  <ISO date>" (7 days, LC48A-TTL-1); a 410 `delivery_expired` reads "Link
  expired: export again". Reuses `downloadHref()` from `CompanionOutcome.tsx`.
- Tests: vitest for both components (labels reuse the panel word maps with a
  parity test), refs refetch on generation change, expired link.

### 03c Live lifecycle, playback and controls (UI)

- `SpeechState` gains `thinking` (a turn is streaming) and `speaking` (a claimed
  answer is playing). Listening, thinking and speaking are the only lit states.
- `livePlayback.ts` claims output (`speech/output/claim`), plays it in one audio
  element, and stops within one frame on Stop speaking or barge-in
  (`speech/barge-in`). Stop speaking leaves the turn and any job running.
- Controls on the Live panel: Mute (track disabled, mic dot hollow, caption
  "Muted"), Stop speaking (shown only while speaking), Open console, End
  (`speech/end`, all tracks stopped). Cancel task is not a new button: the
  caption links "Task running ›" to #1556's monitor line, so one entry point.
- Autoplay blocked: caption "Sound is blocked in this tab: answers show as
  text" with a "Play answers" button (a user gesture retries). Denied or stale
  microphone grant: "Microphone is off for this site" with "Type instead".
- Tests: state machine (vitest, fake timers): every exit (End, unmount, hidden
  tab, project switch, rebind, stale generation) stops tracks and audio;
  barge-in drops queued playback; mute sends nothing.

### 03d Console keeps Live; one caption (UI)

- Opening the console no longer ends Live. The companion hides; the context
  bar shows `Live · <state>` with Mute, Stop speaking and End.
- Closing the console returns the state to the companion.
- The companion caption has two lines: `Heard: “<final text>”` (partials never
  shown as heard) and the latest outcome with its link or download. It clears
  when the outcome is older than the next utterance.

### 03e Chromium suite and shots

- Playwright with a fake media device and mocked speech routes: start Live,
  ask, navigate (Guided and Expert), approve a card beside the companion,
  open/close the console, End. Asserts every `MediaStreamTrack.readyState` is
  `ended` and no `<audio>` plays after each exit.
- Shots per state in light and dark, Guided and Expert.
- Real-device gates (spec acceptance 8, 12) stay open: they need the live probe
  (issue 01, LV01-PROBE-LATER-1). The issue stays not-done until they run.

## Journey fit

The conversation UI is an overlay on every journey stage, never a stage of its
own. It sits on the page the user is on (Live) or in the dock (Compose).

| Stage | What the conversation does there |
|---|---|
| Template | "Start a data-centre project": create, then the context bar names the new project (validated rebind). |
| Setup | Reads and edits navigate to the field's Guided home (`openFlowTargetInPlace`); write cards sit beside the companion on that panel. |
| Readiness | "What's missing?": the answer links "Missing: <input> ›"; Viewing updates. |
| Run | Start-solve card beside the companion; the state moves to thinking, then the job hands off. |
| Wait | #1556's monitor line in the console, "Task running ›" in the caption; reads keep answering; Stop speaking ≠ Stop monitoring ≠ Cancel job. |
| Results | Navigation opens the asked tab with the run selected; Discussing names that run; a finished job never jumps panels on its own. |
| Decide/compare | Baseline run in the context bar; comparisons open in Results with "Back to <origin>". |
| Report | "File ready ›" in the caption and a Download card in the console. |

Guided and Expert show the same controls and context; Guided never switches to
Expert to open a target.

## Mock shots

`.scratch/live-voice/shots/03-plan/` (`mock.html` is the source):
`live-light`, `live-dark` (Live on the page: confirmation card and caption beside
the companion, controls) and `console-light`, `console-dark` (console open
during Live: context bar, live bar, #1556 monitor line, download card).

## Risks

- `harness/loop.py` and `ChatPanel.tsx` are hot files; 03a's loop change is one
  call at admission.
- Playback timing (≤250 ms stop) is checked only with mocks until issue 01's
  probe runs.
