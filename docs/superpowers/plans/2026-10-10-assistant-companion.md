<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assistant companion launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the collapsed assistant's reserved column with a floating companion whose Compose, Speak, and profile actions open the existing dock without starting live voice.

**Architecture:** The open assistant stays `AssistantDock` plus `ChatPanel`. When `assistantDockOpen` is false the dock column takes no width and `CompanionLauncher` overlays the canvas. Compose, Speak, and the profile chip write a one-shot `assistantEntry` on the UI store. `ChatPanel` consumes it: compose relies on the existing composer focus, speak opens the reviewed dictation panel and does not call `getUserMedia`, and profiles focuses `chat-model-select`. Extra hosted models that speak OpenAI chat completions are catalogue presets on the existing `openai` wire. No second planner and no new provider class.

**Tech Stack:** React 19, TypeScript, Zustand, Vitest, Testing Library, Tailwind; FastAPI preset catalogue in `pypsa-gui/backend/presets.json` read by `services.llm_config`.

## Global Constraints

- One shared harness and one tool catalogue. "Another agent" means another LLM profile, not a second planner.
- Reviewed dictation (merged PR #106) stays separate from continuous live conversation. Speak opens the existing dictation panel. A transcript is inserted only after the user accepts it.
- Speak does not start a microphone, a recording, or a paid LiveKit session. Live conversation remains an explicit control inside the open dock and is out of scope for this plan (issue 03).
- The companion is an original presence (accent-colored SVG, pencil and microphone). The reference screenshot defines the interaction — a floating character and a two-action pill — and is not an asset to copy.
- Idle motion is decorative, honors `prefers-reduced-motion`, and never uses a listening or recording affordance while the microphone is closed.
- The floating companion is not rendered while the dock is open, so it cannot cover results, confirmation cards, or the composer.
- ChatPanel stays mounted while the dock is collapsed. Do not key the dock's error boundary on `assistantDockOpen`.
- New hosted chat models use `wire: "openai"` and `OpenAICompatProvider`. Do not add a provider class for Kimi or Grok.
- Kimi is already the `moonshot` preset (`https://api.moonshot.ai/v1`, `MOONSHOT_API_KEY`, `token_param: "max_tokens"`). Do not add a second Kimi preset.
- The official `openai` preset remains the only preset with `token_param: "max_completion_tokens"`.
- Cursor's agent APIs are a workspace coding agent, not a chat-completions endpoint. Do not add a Cursor preset, proxy, or provider.
- No paid API calls in these tasks. A preset is not a claim that the vendor has been probed.
- Confirmation, locks, budgets, ACL, and the existing profile-switch session rules stay authoritative. This plan does not change them.

---

## File structure

- Modify `pypsa-gui/frontend/src/store/uiStore.ts` — one-shot `assistantEntry`. Not persisted.
- Create `pypsa-gui/frontend/src/components/CompanionLauncher.tsx` — overlay, character, Compose, Speak, profile chip.
- Create `pypsa-gui/frontend/src/components/CompanionLauncher.test.tsx` — presence, reduced motion, the three callbacks.
- Modify `pypsa-gui/frontend/src/components/AssistantDock.tsx` — zero-width collapsed column; mount the launcher only while closed.
- Modify `pypsa-gui/frontend/src/components/AssistantDock.test.tsx` — launcher contract replaces the 40px gutter tests.
- Modify `pypsa-gui/frontend/src/components/ToasterHost.tsx` — keep toasts off the companion.
- Modify `pypsa-gui/frontend/src/components/ToasterHost.test.tsx` — collapsed offset.
- Modify `pypsa-gui/frontend/src/pages/ProjectsHomePage.assistant.test.tsx` — landing page looks for the companion.
- Modify `pypsa-gui/frontend/src/index.css` — idle bob, disabled under reduced motion.
- Modify `pypsa-gui/frontend/src/components/DictationControls.tsx` — open the panel on request without recording.
- Modify `pypsa-gui/frontend/src/components/DictationControls.test.tsx` — that request.
- Modify `pypsa-gui/frontend/src/components/ChatPanel.tsx` — consume `assistantEntry`.
- Create `pypsa-gui/frontend/src/components/ChatPanel.companionEntry.test.tsx` — compose, speak, and profiles against the real panel.
- Modify `pypsa-gui/backend/presets.json` — `xai` Grok preset.
- Modify `pypsa-gui/backend/tests/test_llm_config.py` — catalogue id set, token param, shared-key lock, wire lock.

---

### Task 1: Floating companion and a zero-width collapsed dock

**Files:**
- Modify: `pypsa-gui/frontend/src/store/uiStore.ts` (type near the `assistantDockOpen` fields, actions near `setAssistantDockOpen`, initial state near line 608)
- Create: `pypsa-gui/frontend/src/components/CompanionLauncher.tsx`
- Create: `pypsa-gui/frontend/src/components/CompanionLauncher.test.tsx`
- Modify: `pypsa-gui/frontend/src/components/AssistantDock.tsx`
- Modify: `pypsa-gui/frontend/src/components/AssistantDock.test.tsx`
- Modify: `pypsa-gui/frontend/src/components/ToasterHost.tsx`
- Modify: `pypsa-gui/frontend/src/components/ToasterHost.test.tsx`
- Modify: `pypsa-gui/frontend/src/pages/ProjectsHomePage.assistant.test.tsx`
- Modify: `pypsa-gui/frontend/src/index.css`
- Modify: `pypsa-gui/frontend/src/App.tsx` (the Zone 5 comment that says the collapsed width is 40px)

**Interfaces:**
- Consumes: `useUIStore.assistantDockOpen`, `setAssistantDockOpen`, `useChatProfiles`, `useChatReadiness`.
- Produces:
  - `assistantEntry: 'compose' | 'speak' | 'profiles' | null`
  - `setAssistantEntry(entry: 'compose' | 'speak' | 'profiles' | null): void`
  - `COMPANION_TOAST_OFFSET = 128`
  - `CompanionPresence = 'idle' | 'listening' | 'thinking' | 'speaking'`
  - `CompanionLauncher` props: `presence?: CompanionPresence`, `profileLabel: string`, `onCompose: () => void`, `onSpeak: () => void`, `onProfiles: () => void`
  - Test ids: `companion-launcher`, `companion-character`, `companion-compose`, `companion-speak`, `companion-profile`
  - `data-presence` and `data-motion` (`on` | `off`) on `companion-launcher`

- [ ] **Step 1: Write the failing launcher tests**

Create `pypsa-gui/frontend/src/components/CompanionLauncher.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CompanionLauncher from './CompanionLauncher'

function mockMotion(matches: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query.includes('prefers-reduced-motion') ? matches : false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

describe('CompanionLauncher', () => {
  afterEach(() => cleanup())

  it('offers compose and speak without claiming the microphone is open', async () => {
    mockMotion(false)
    const onCompose = vi.fn()
    const onSpeak = vi.fn()
    const onProfiles = vi.fn()
    const user = userEvent.setup()
    render(
      <CompanionLauncher
        profileLabel="Claude"
        onCompose={onCompose}
        onSpeak={onSpeak}
        onProfiles={onProfiles}
      />,
    )
    const root = screen.getByTestId('companion-launcher')
    expect(root.getAttribute('data-presence')).toBe('idle')
    expect(root.getAttribute('data-motion')).toBe('on')
    expect(screen.getByTestId('companion-speak').getAttribute('aria-pressed')).toBe('false')
    await user.click(screen.getByTestId('companion-compose'))
    await user.click(screen.getByTestId('companion-speak'))
    await user.click(screen.getByTestId('companion-profile'))
    expect(onCompose).toHaveBeenCalledOnce()
    expect(onSpeak).toHaveBeenCalledOnce()
    expect(onProfiles).toHaveBeenCalledOnce()
    expect(screen.getByTestId('companion-profile').textContent).toBe('Claude')
  })

  it('drops decorative motion when the user prefers reduced motion', () => {
    mockMotion(true)
    render(
      <CompanionLauncher
        profileLabel="Model"
        onCompose={vi.fn()}
        onSpeak={vi.fn()}
        onProfiles={vi.fn()}
      />,
    )
    expect(screen.getByTestId('companion-launcher').getAttribute('data-motion')).toBe('off')
    expect(screen.getByTestId('companion-character').className.split(/\s+/)).not.toContain('companion-idle')
  })

  it('marks a listening presence only when that presence is passed in', () => {
    mockMotion(false)
    render(
      <CompanionLauncher
        presence="listening"
        profileLabel="Model"
        onCompose={vi.fn()}
        onSpeak={vi.fn()}
        onProfiles={vi.fn()}
      />,
    )
    expect(screen.getByTestId('companion-launcher').getAttribute('data-presence')).toBe('listening')
    expect(screen.getByTestId('companion-speak').getAttribute('aria-pressed')).toBe('true')
  })
})
```

- [ ] **Step 2: Run the launcher tests and confirm they fail**

Run: `cd pypsa-gui/frontend && npm test -- src/components/CompanionLauncher.test.tsx`

Expected: FAIL because `CompanionLauncher` is not defined.

- [ ] **Step 3: Add the one-shot entry to the UI store**

In the `UIStore` type, next to `assistantDockOpen`:

```ts
  assistantEntry: 'compose' | 'speak' | 'profiles' | null
```

Next to `setAssistantDockOpen`:

```ts
  setAssistantEntry: (entry: 'compose' | 'speak' | 'profiles' | null) => void
```

In the store initializer, next to `assistantDockOpen: storedAssistantDockOpen()`:

```ts
  assistantEntry: null,
```

Next to the `setAssistantDockOpen` implementation:

```ts
  setAssistantEntry: (entry) => set({ assistantEntry: entry }),
```

Do not persist `assistantEntry`. A reload must not reopen dictation or steal focus.

Update the comment above `storedAssistantDockOpen` so it no longer describes a 40px gutter as the closed state. Default-open stays as it is. The closed state is the overlay from this task.

- [ ] **Step 4: Implement the launcher**

Create `pypsa-gui/frontend/src/components/CompanionLauncher.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { Mic, Pencil } from 'lucide-react'

export const COMPANION_TOAST_OFFSET = 128

export type CompanionPresence = 'idle' | 'listening' | 'thinking' | 'speaking'

type Props = {
  presence?: CompanionPresence
  profileLabel: string
  onCompose: () => void
  onSpeak: () => void
  onProfiles: () => void
}

function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(() =>
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduce(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduce
}

export default function CompanionLauncher({
  presence = 'idle', profileLabel, onCompose, onSpeak, onProfiles,
}: Props) {
  const reduce = useReducedMotion()
  return (
    <div
      className="fixed bottom-6 right-6 z-40 flex flex-col items-end gap-2"
      data-testid="companion-launcher"
      data-presence={presence}
      data-motion={reduce ? 'off' : 'on'}
    >
      <button
        type="button"
        onClick={onProfiles}
        data-testid="companion-profile"
        className="max-w-[12rem] truncate rounded-full border border-border bg-bg-2 px-3 py-1 text-[10px] text-text shadow-lg"
        title={profileLabel}
        aria-label={`Assistant model: ${profileLabel}. Open the model list.`}
      >
        {profileLabel}
      </button>
      <div
        aria-hidden="true"
        data-testid="companion-character"
        className={reduce ? '' : 'companion-idle'}
      >
        <svg width="72" height="72" viewBox="0 0 72 72" fill="none">
          <rect x="16" y="18" width="40" height="36" rx="14" fill="var(--color-accent)" />
          <rect x="24" y="28" width="24" height="12" rx="6" fill="var(--color-bg)" />
          <circle cx="32" cy="34" r="2" fill="var(--color-accent)" />
          <circle cx="40" cy="34" r="2" fill="var(--color-accent)" />
        </svg>
      </div>
      <div className="flex items-center rounded-full border border-border bg-bg-2 shadow-lg">
        <button
          type="button"
          onClick={onCompose}
          data-testid="companion-compose"
          aria-label="Compose a message"
          title="Open the assistant and type"
          className="px-3 py-2 text-text hover:text-accent"
        >
          <Pencil size={16} />
        </button>
        <span className="h-4 w-px bg-border" aria-hidden="true" />
        <button
          type="button"
          onClick={onSpeak}
          data-testid="companion-speak"
          aria-label="Open reviewed dictation"
          aria-pressed={presence === 'listening'}
          title="Open the assistant and review dictation"
          className="px-3 py-2 text-text hover:text-accent"
        >
          <Mic size={16} />
        </button>
      </div>
    </div>
  )
}
```

Append to `pypsa-gui/frontend/src/index.css`:

```css
@media (prefers-reduced-motion: no-preference) {
  .companion-idle {
    animation: companion-bob 2.4s ease-in-out infinite;
  }
}

@keyframes companion-bob {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-4px); }
}
```

- [ ] **Step 5: Mount it from the dock and stop reserving a column**

In `AssistantDock.tsx`:

- Import `CompanionLauncher` and `{ useChatProfiles, useChatReadiness }`.
- Read the profiles:

```tsx
  const profilesQuery = useChatProfiles()
  const { effectiveProfileId } = useChatReadiness()
  const profileLabel =
    profilesQuery.data?.profiles.find((p) => p.id === effectiveProfileId)?.label
    ?? profilesQuery.data?.profiles.find((p) => p.id === profilesQuery.data.active_profile_id)?.label
    ?? 'Model'
```

- Replace the collapsed branch (`w-10`, `assistant-dock-launcher`, `assistant-dock-mic`) with nothing inside the column. The open header, resize handle, and always-mounted `ChatPanel` stay.
- Collapsed class is `w-0 overflow-visible border-0`. Open class stays `border-l border-border` plus the width style. Do not set an inline width while collapsed.
- When `assistantDockOpen` is false, render:

```tsx
      <CompanionLauncher
        profileLabel={profileLabel}
        onCompose={() => {
          useUIStore.getState().setAssistantEntry('compose')
          setAssistantDockOpen(true)
        }}
        onSpeak={() => {
          useUIStore.getState().setAssistantEntry('speak')
          setAssistantDockOpen(true)
        }}
        onProfiles={() => {
          useUIStore.getState().setAssistantEntry('profiles')
          setAssistantDockOpen(true)
        }}
      />
```

`presence` stays at its default `idle`. Issue 03 is what later passes `listening`, `thinking`, or `speaking`. This task does not import LiveKit.

- [ ] **Step 6: Update the dock tests**

At the top of `AssistantDock.test.tsx`, mock the profile hooks so the dock does not need a query client:

```tsx
vi.mock('../hooks/useChatProfiles', () => ({
  useChatProfiles: () => ({
    data: {
      profiles: [{ id: 'claude', label: 'Claude', wire: 'anthropic' as const }],
      active_profile_id: 'claude',
    },
    isError: false,
  }),
  useChatReadiness: () => ({ effectiveProfileId: 'claude', ready: true }),
}))
```

Replace the collapsed-launcher assertions:

- `assistant-dock-launcher` becomes `companion-compose`. Clicking it sets `assistantDockOpen` true and `assistantEntry` to `'compose'`.
- Delete the test that requires the microphone inside a collapsed strip. Replace it with: while collapsed, `companion-speak` is present; clicking it sets `assistantDockOpen` true and `assistantEntry` to `'speak'`.
- While collapsed, `assistant-dock` class names include `w-0` and do not include `w-10`, and `style.width` is `''`.
- While open, `companion-launcher` is absent (`queryByTestId` returns null) and the resize handle still exists.
- The mount-identity test clicks `companion-compose` instead of `assistant-dock-launcher`.
- `beforeEach` also sets `assistantEntry: null`.

In `ProjectsHomePage.assistant.test.tsx`, the collapsed test expects `companion-compose` instead of `assistant-dock-launcher`. The dock node stays inside the brand-dark surface.

In `ToasterHost.tsx`, import `COMPANION_TOAST_OFFSET` and set `right` to:

```tsx
  const right = !dockOnPage
    ? TOAST_GAP
    : dockOpen
      ? dockWidth + TOAST_GAP
      : COMPANION_TOAST_OFFSET + TOAST_GAP
```

In `ToasterHost.test.tsx`, the collapse step on `/app` expects `${COMPANION_TOAST_OFFSET + TOAST_GAP}px`, not `${TOAST_GAP}px`. Login and admin stay at `TOAST_GAP` whether the stored dock flag is open or closed.

In `App.tsx`, change the Zone 5 comment so the collapsed dock is a zero-width column and the companion is `position: fixed`. The open width is still the stored width.

- [ ] **Step 7: Run the affected frontend tests**

Run:

```bash
cd pypsa-gui/frontend && npm test -- \
  src/components/CompanionLauncher.test.tsx \
  src/components/AssistantDock.test.tsx \
  src/components/AssistantDock.eviction.test.tsx \
  src/components/ToasterHost.test.tsx \
  src/pages/ProjectsHomePage.assistant.test.tsx
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pypsa-gui/frontend/src/store/uiStore.ts \
  pypsa-gui/frontend/src/components/CompanionLauncher.tsx \
  pypsa-gui/frontend/src/components/CompanionLauncher.test.tsx \
  pypsa-gui/frontend/src/components/AssistantDock.tsx \
  pypsa-gui/frontend/src/components/AssistantDock.test.tsx \
  pypsa-gui/frontend/src/components/ToasterHost.tsx \
  pypsa-gui/frontend/src/components/ToasterHost.test.tsx \
  pypsa-gui/frontend/src/pages/ProjectsHomePage.assistant.test.tsx \
  pypsa-gui/frontend/src/index.css \
  pypsa-gui/frontend/src/App.tsx
git commit -m "feat(chat): float the collapsed assistant over the canvas"
```

---

### Task 2: Speak opens reviewed dictation and does not start the microphone

**Files:**
- Modify: `pypsa-gui/frontend/src/components/DictationControls.tsx`
- Modify: `pypsa-gui/frontend/src/components/DictationControls.test.tsx`
- Modify: `pypsa-gui/frontend/src/components/ChatPanel.tsx` (the dock-open focus effect near the `prevDockOpenRef` block, and the `DictationControls` JSX near `data-testid="chat-input"`)
- Create: `pypsa-gui/frontend/src/components/ChatPanel.companionEntry.test.tsx`

**Interfaces:**
- Consumes: `assistantEntry` and `setAssistantEntry` from Task 1. `DictationControls` local `open` state. `installRecordingMocks` from `src/utils/audioRecorder.testHelpers.ts`.
- Produces: `DictationControls` props `panelRequested: boolean` and `onPanelRequestConsumed: () => void`. Speak leaves `dictation-panel` open, `navigator.mediaDevices.getUserMedia` uncalled, and `speech.toggle` uncalled.

- [ ] **Step 1: Write the failing dictation-request test**

Add this test to `DictationControls.test.tsx`. `defaults` must gain `panelRequested: false` and `onPanelRequestConsumed: vi.fn()` so existing mounts still type-check.

```tsx
it('opens the review panel when asked and does not start the microphone', async () => {
  const consumed = vi.fn()
  mount({ panelRequested: true, onPanelRequestConsumed: consumed })
  expect(await screen.findByTestId('dictation-panel')).toBeTruthy()
  expect(speech.toggle).not.toHaveBeenCalled()
  expect(recording.getUserMedia).not.toHaveBeenCalled()
  expect(consumed).toHaveBeenCalledOnce()
})
```

- [ ] **Step 2: Run that test and confirm it fails**

Run: `cd pypsa-gui/frontend && npm test -- src/components/DictationControls.test.tsx`

Expected: FAIL because `panelRequested` is not a prop and the panel stays closed.

- [ ] **Step 3: Honor the request inside DictationControls**

Extend `Props`:

```tsx
  panelRequested: boolean
  onPanelRequestConsumed: () => void
```

Add the prop to the function signature. Add this effect beside the effect that closes the panel when the dock is closed or a turn is streaming:

```tsx
  useEffect(() => {
    if (!panelRequested) return
    if (dockOpen && !streaming) setOpen(true)
    onPanelRequestConsumed()
  }, [panelRequested, dockOpen, streaming, onPanelRequestConsumed])
```

This effect calls `setOpen(true)` only. It does not call `audio.start`, `speech.toggle`, or `mic`. If a turn is streaming, the request is consumed and the panel stays closed. The existing close effect still cancels audio when the dock collapses.

Pass `panelRequested: false` and `onPanelRequestConsumed: () => {}` from every current test `defaults` object.

- [ ] **Step 4: Consume `speak` and `compose` in ChatPanel**

Near the other chat state:

```tsx
  const assistantEntry = useUIStore((s) => s.assistantEntry)
  const [dictationPanelRequested, setDictationPanelRequested] = useState(false)
  const consumeDictationRequest = useCallback(() => {
    setDictationPanelRequested(false)
  }, [])
```

Add this effect next to the existing focus-on-open effect. Do not remove that effect: opening the dock, including from Compose, still focuses `chat-input`.

```tsx
  useEffect(() => {
    if (!assistantDockOpen || assistantEntry == null) return
    const entry = assistantEntry
    useUIStore.getState().setAssistantEntry(null)
    if (entry === 'speak') setDictationPanelRequested(true)
  }, [assistantDockOpen, assistantEntry])
```

On `DictationControls`:

```tsx
  panelRequested={dictationPanelRequested}
  onPanelRequestConsumed={consumeDictationRequest}
```

- [ ] **Step 5: Write the panel-level entry test**

Create `pypsa-gui/frontend/src/components/ChatPanel.companionEntry.test.tsx`. Copy the `vi.mock` blocks for `../api/dictation`, `../hooks/useSpeechToText`, `../api/chat`, `../api/uploads`, `../api/network`, and `../api/simulation` from `ChatPanel.speech.test.tsx`, including `installRecordingMocks()` in `beforeEach`. Add:

```tsx
const recording = { current: null as ReturnType<typeof installRecordingMocks> | null }

beforeEach(() => {
  recording.current = installRecordingMocks()
  useUIStore.setState({
    currentProject: 'Demo', assistantDockOpen: false, assistantEntry: null, activeSlidePanel: null,
  })
  useChatStore.setState({ messages: [], profileId: null, boundProfileId: null })
})
```

Use the same `renderPanel` helper as `ChatPanel.speech.test.tsx`. Then:

```tsx
it('compose opens the typed composer and leaves dictation closed', async () => {
  renderPanel()
  act(() => {
    useUIStore.setState({ assistantDockOpen: true, assistantEntry: 'compose' })
  })
  await waitFor(() => {
    expect(document.activeElement).toBe(screen.getByTestId('chat-input'))
  })
  expect(screen.queryByTestId('dictation-panel')).toBeNull()
  expect(recording.current!.getUserMedia).not.toHaveBeenCalled()
  expect(useUIStore.getState().assistantEntry).toBeNull()
})

it('speak opens the review panel and does not start a recording or a chat turn', async () => {
  renderPanel()
  act(() => {
    useUIStore.setState({ assistantDockOpen: true, assistantEntry: 'speak' })
  })
  expect(await screen.findByTestId('dictation-panel')).toBeTruthy()
  expect(recording.current!.getUserMedia).not.toHaveBeenCalled()
  expect(vi.mocked(createChatStream)).not.toHaveBeenCalled()
  expect(useUIStore.getState().assistantEntry).toBeNull()
})
```

Import `createChatStream` from `../api/chat` so the assertion type-checks against the mock.

- [ ] **Step 6: Run the speech and dictation tests**

Run:

```bash
cd pypsa-gui/frontend && npm test -- \
  src/components/DictationControls.test.tsx \
  src/components/ChatPanel.companionEntry.test.tsx \
  src/components/ChatPanel.speech.test.tsx \
  src/components/ChatPanel.mic.test.tsx
```

Expected: PASS. `getUserMedia` stays uncalled in the new speak test. Existing dictation tests still insert a transcript only from the explicit Insert button.

- [ ] **Step 7: Commit**

```bash
git add pypsa-gui/frontend/src/components/DictationControls.tsx \
  pypsa-gui/frontend/src/components/DictationControls.test.tsx \
  pypsa-gui/frontend/src/components/ChatPanel.tsx \
  pypsa-gui/frontend/src/components/ChatPanel.companionEntry.test.tsx
git commit -m "feat(chat): open reviewed dictation from the companion"
```

---

### Task 3: Profile chip focuses the existing model select

**Files:**
- Modify: `pypsa-gui/frontend/src/components/ChatPanel.tsx` (the entry effect from Task 2, and the ready `chat-model-select`)
- Modify: `pypsa-gui/frontend/src/components/ChatPanel.companionEntry.test.tsx`

**Interfaces:**
- Consumes: `assistantEntry === 'profiles'` from Task 1. The existing `data-testid="chat-model-select"` and `onPickProfile`.
- Produces: focusing that select. No new picker and no change to `onPickProfile` or session rebinding.

- [ ] **Step 1: Write the failing focus test**

In `ChatPanel.companionEntry.test.tsx`, mock the profile list before importing `ChatPanel` is not possible if the import is already first. Put this mock next to the other `vi.mock` calls:

```tsx
vi.mock('../api/llmSettings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/llmSettings')>()
  return {
    ...actual,
    getChatProfiles: vi.fn().mockResolvedValue({
      profiles: [{ id: 'claude', label: 'Claude', wire: 'anthropic', chat_ready: true }],
      active_profile_id: 'claude',
    }),
  }
})
```

Add:

```tsx
it('profiles focuses the existing model select and does not start dictation', async () => {
  renderPanel()
  act(() => {
    useUIStore.setState({ assistantDockOpen: true, assistantEntry: 'profiles' })
  })
  await waitFor(() => {
    expect(screen.getByTestId('chat-model-select').getAttribute('data-profiles-state')).toBe('ready')
  })
  await waitFor(() => {
    expect(document.activeElement).toBe(screen.getByTestId('chat-model-select'))
  })
  expect(screen.queryByTestId('dictation-panel')).toBeNull()
  expect(recording.current!.getUserMedia).not.toHaveBeenCalled()
})
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd pypsa-gui/frontend && npm test -- src/components/ChatPanel.companionEntry.test.tsx`

Expected: FAIL because the model select is not focused. The entry is cleared and dictation stays closed, so the assertion that fails is `document.activeElement`.

- [ ] **Step 3: Focus the select when the entry is profiles**

In `ChatPanel.tsx`:

```tsx
  const modelSelectRef = useRef<HTMLSelectElement>(null)
```

Extend the Task 2 entry effect:

```tsx
  useEffect(() => {
    if (!assistantDockOpen || assistantEntry == null) return
    const entry = assistantEntry
    useUIStore.getState().setAssistantEntry(null)
    if (entry === 'speak') setDictationPanelRequested(true)
    if (entry === 'profiles') {
      requestAnimationFrame(() => modelSelectRef.current?.focus())
    }
  }, [assistantDockOpen, assistantEntry])
```

Put `ref={modelSelectRef}` on every `chat-model-select` element (loading, error, empty, and ready). Only the ready select accepts focus; the test waits until `data-profiles-state` is `ready`, then a second animation frame focuses it. If the first frame runs while the select is still disabled, focus again when the profiles query resolves:

```tsx
  useEffect(() => {
    if (assistantEntry !== 'profiles' || !assistantDockOpen) return
    if (profilesQuery.data == null) return
    requestAnimationFrame(() => modelSelectRef.current?.focus())
  }, [assistantEntry, assistantDockOpen, profilesQuery.data])
```

Clearing `assistantEntry` in the first effect races this one. Keep the entry until the ready select has been focused:

```tsx
  useEffect(() => {
    if (!assistantDockOpen || assistantEntry == null) return
    if (assistantEntry === 'speak') {
      setDictationPanelRequested(true)
      useUIStore.getState().setAssistantEntry(null)
      return
    }
    if (assistantEntry === 'compose') {
      useUIStore.getState().setAssistantEntry(null)
      return
    }
    if (profilesQuery.data == null) return
    const select = modelSelectRef.current
    if (select == null || select.disabled) return
    select.focus()
    useUIStore.getState().setAssistantEntry(null)
  }, [assistantDockOpen, assistantEntry, profilesQuery.data])
```

Delete the two-effect version if you added it in an earlier step. One effect is the contract. Compose still gets its caret from the existing focus-on-open effect.

Do not call `onPickProfile` from the chip. Choosing a row in the select keeps the current session rules.

- [ ] **Step 4: Run the entry tests**

Run: `cd pypsa-gui/frontend && npm test -- src/components/ChatPanel.companionEntry.test.tsx src/components/ChatPanel.profile.test.tsx`

Expected: PASS. Profile switching tests are unchanged.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/frontend/src/components/ChatPanel.tsx \
  pypsa-gui/frontend/src/components/ChatPanel.companionEntry.test.tsx
git commit -m "feat(chat): open the model list from the companion"
```

---

### Task 4: Add Grok as an OpenAI-compatible preset

**Files:**
- Modify: `pypsa-gui/backend/presets.json`
- Modify: `pypsa-gui/backend/tests/test_llm_config.py` (`test_presets_catalogue_shape`, `_SHARED_PROVIDER_KEYS`, `test_an_exact_bearer_preset_is_locked_to_its_own_base_url`, `test_every_shipped_preset_accepts_its_own_declared_wire`)

**Interfaces:**
- Consumes: `OpenAICompatProvider` via `wire: "openai"`. `llm_config.load_presets`.
- Produces: preset id `xai`, label `xAI (Grok)`, base `https://api.x.ai/v1`, key `XAI_API_KEY`, `token_param: "max_tokens"`, `tools: true`, `vision: false`. No Python provider class.

- [ ] **Step 1: Extend the catalogue test before editing the JSON**

In `test_presets_catalogue_shape`, change the id set to:

```python
    assert ids == {"anthropic", "openai", "moonshot", "dashscope",
                   "ollama", "lmstudio", "xai"}
```

Change the non-OpenAI token-param loop to:

```python
    for other in ("moonshot", "dashscope", "ollama", "lmstudio", "xai"):
        assert by_id[other]["token_param"] == "max_tokens"
```

Add, still inside that test:

```python
    assert by_id["moonshot"]["wire"] == "openai"
    assert by_id["moonshot"]["base_url"] == "https://api.moonshot.ai/v1"
    assert by_id["moonshot"]["key_env"] == "MOONSHOT_API_KEY"
    assert by_id["xai"]["wire"] == "openai"
    assert by_id["xai"]["base_url"] == "https://api.x.ai/v1"
    assert by_id["xai"]["key_env"] == "XAI_API_KEY"
    assert by_id["xai"]["auth"] == "bearer"
    assert by_id["xai"]["vision"] is False
    assert by_id["openai"]["token_param"] == "max_completion_tokens"
```

The last OpenAI assertion is already present; keep it once.

Add `"XAI_API_KEY"` to `_SHARED_PROVIDER_KEYS`.

Add `("xai", "openai")` to both parametrize lists: `test_an_exact_bearer_preset_is_locked_to_its_own_base_url` and `test_every_shipped_preset_accepts_its_own_declared_wire`.

- [ ] **Step 2: Run the catalogue test and confirm it fails**

Run: `pixi run -e test gui-tests tests/test_llm_config.py::test_presets_catalogue_shape -q`

Expected: FAIL because `xai` is not in the loaded ids.

- [ ] **Step 3: Add the preset**

Insert this object in `pypsa-gui/backend/presets.json` after the `dashscope` object:

```json
  {
    "id": "xai",
    "label": "xAI (Grok)",
    "wire": "openai",
    "base_url": "https://api.x.ai/v1",
    "auth": "bearer",
    "token_param": "max_tokens",
    "key_env": "XAI_API_KEY",
    "tools": true,
    "vision": false,
    "suggested_models": [
      "grok-4"
    ],
    "help": "Chat completions at api.x.ai. Set XAI_API_KEY. xAI's newer Responses API is a different adapter and is not this preset. Vision is off until a live probe confirms the model accepts images through this wire."
  }
```

Do not add a `cursor` id. Do not change `moonshot` except for the assertions in Step 1.

- [ ] **Step 4: Run the preset lock tests**

Run:

```bash
pixi run -e test gui-tests tests/test_llm_config.py::test_presets_catalogue_shape tests/test_llm_config.py::test_an_exact_bearer_preset_is_locked_to_its_own_base_url tests/test_llm_config.py::test_every_shipped_preset_accepts_its_own_declared_wire -q
```

Expected: PASS. The `xai` bearer case refuses a caller-supplied `base_url`. The declared wire `openai` is accepted. No network call is made.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/presets.json pypsa-gui/backend/tests/test_llm_config.py
git commit -m "feat(chat): catalogue Grok on the OpenAI-compatible wire"
```

---

## Self-review

Spec coverage:

- Floating presence, not a column or a slide tab: Task 1.
- Compose opens the dock and focuses the composer: Tasks 1 and 2. Focus is the existing open effect; Task 2 pins it.
- Speak opens reviewed dictation and does not insert or record: Task 2.
- Live conversation stays inside the open dock and is not started here: Global Constraints. No LiveKit import.
- Idle motion and reduced motion: Task 1. Listening is a prop only; nothing in this plan sets it.
- Companion hidden while the dock is open: Task 1.
- Profile chip opens the existing picker: Tasks 1 and 3.
- Kimi already on the openai wire: Task 4 assertions. No new preset.
- Grok as a catalogue preset: Task 4.
- Cursor is not a chat provider: Global Constraints. No task adds it.

Placeholder scan: every task has a failing test, an implementation, a command, and a commit. Presence values other than `idle` are accepted by the component and left unset on purpose; issue 03 owns the live states.

Type consistency: `assistantEntry` is `'compose' | 'speak' | 'profiles' | null` in Tasks 1–3. Dictation props are `panelRequested` and `onPanelRequestConsumed`. The preset id is `xai`.

## Out of scope

- LiveKit, Deepgram, OpenAI TTS, and the Start conversation control (issue 03, after issues 01, 02, 08, and 09).
- A Responses API adapter for xAI, or any new `LLMProvider` class.
- Paid probes of Moonshot, xAI, DashScope, Ollama, or LM Studio. Catalogue shape is not a live credential test.
- Cursor as a provider, including unofficial proxies.
- Changing the default of `assistantDockOpen` (still open unless the user closed it).
