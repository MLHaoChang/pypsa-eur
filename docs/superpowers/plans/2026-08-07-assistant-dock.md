# Assistant Dock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the assistant from closing itself when it navigates, by moving it out of the single-valued `activeSlidePanel` slot into its own always-mounted dock.

**Architecture:** The assistant becomes a fourth column in the three-column body, rendered as a sibling of the panel container rather than one of its cases. It carries its own open/collapsed state, persisted to localStorage on the same hand-rolled pattern the rest of `uiStore` uses. `'chat'` is deleted from the `SlidePanel` union so `setSlidePanel('chat')` stops compiling — the eviction bug becomes structurally impossible rather than fixed by convention.

**Tech Stack:** React 18 + TypeScript, Zustand, Tailwind, vitest 4.1.10 + jsdom (`globals: false` — import `describe`/`it`/`expect` explicitly), Testing Library.

**Source spec:** `docs/superpowers/specs/2026-08-05-assistant-presence-and-deixis-design.md`, section "The dock, and removing `'chat'` from the union". This plan implements **only** that section. `ui_context`, deixis, the launch greeting and speech reciprocity are step (c2) and are explicitly out of scope.

## Global Constraints

- `npx` is NOT on PATH. Every frontend command runs as
  `pixi run bash -c 'cd pypsa-gui/frontend && <cmd>'`.
- Frontend tests use `globals: false` — import `describe`, `it`, `expect`,
  `beforeEach`, `vi` from `vitest` explicitly in every test file.
- `tsc -b` must exit 0. The union change is a compile-time breaking change **by
  design**: every stale `'chat'` site must be found by the compiler, not by grep.
- The full vitest suite must pass. Baseline at branch point: 82 files, 660 tests.
- Never use `git stash` — the stash ref is shared between worktrees. Revert with
  `git checkout <path>`.
- Run `git diff --cached --name-only` immediately before every commit; abort if
  it lists a file you did not personally edit. There is an untracked
  `pypsa-gui/backend/tests/test_zzz_probe_queue.py` belonging to another session
  — never add it.
- Never `cd` into or touch `/Users/orange/Desktop/Code Test/pypsa-eur`.
- No backend changes in this plan. No `chat_service.py`, no `chat_tools.py`.

---

### Task 1: Dock state in `uiStore`

**Files:**
- Modify: `pypsa-gui/frontend/src/store/uiStore.ts`
- Test: `pypsa-gui/frontend/src/store/uiStore.assistantDock.test.ts` (create)

**Interfaces:**
- Produces: `assistantDockOpen: boolean` on the store, plus actions
  `setAssistantDockOpen(open: boolean): void` and `toggleAssistantDock(): void`.
  Tasks 2 and 3 consume all three.

The store predates zustand's `persist` middleware — every preference is written
to its own localStorage key by hand (see `persistRecents`, `storedSidebarMode`).
Follow that pattern exactly; do not introduce `persist`.

Default is **closed**. The collapsed strip is still visible, so the assistant is
always reachable; defaulting open would show an empty panel to every existing
user, because the launch greeting is step (c2) and does not exist yet.

- [ ] **Step 1: Write the failing test**

```ts
import { describe, it, expect, beforeEach } from 'vitest'
import { useUIStore } from './uiStore'

const KEY = 'network-diagram:assistant-dock'

describe('assistant dock state', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false })
  })

  it('defaults to closed', () => {
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
  })

  it('setAssistantDockOpen writes through to the store and localStorage', () => {
    useUIStore.getState().setAssistantDockOpen(true)
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(localStorage.getItem(KEY)).toBe('open')
  })

  it('toggleAssistantDock flips the current value', () => {
    useUIStore.getState().toggleAssistantDock()
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    useUIStore.getState().toggleAssistantDock()
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(localStorage.getItem(KEY)).toBe('closed')
  })

  it('survives a localStorage that throws', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError')
    })
    expect(() => useUIStore.getState().setAssistantDockOpen(true)).not.toThrow()
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    spy.mockRestore()
  })
})
```

Add `vi` to the vitest import when you write the last case.

- [ ] **Step 2: Run it and confirm it fails**

```
pixi run bash -c 'cd pypsa-gui/frontend && npm run test -- src/store/uiStore.assistantDock.test.ts'
```

Expected: failures naming `assistantDockOpen` / `setAssistantDockOpen` as undefined.

- [ ] **Step 3: Implement**

Near the other preference keys at the top of the file:

```ts
const ASSISTANT_DOCK_KEY = 'network-diagram:assistant-dock'

function storedAssistantDockOpen(): boolean {
  try {
    return localStorage.getItem(ASSISTANT_DOCK_KEY) === 'open'
  } catch { /* noop */ }
  return false
}

function persistAssistantDockOpen(open: boolean) {
  try { localStorage.setItem(ASSISTANT_DOCK_KEY, open ? 'open' : 'closed') } catch { /* noop */ }
}
```

Add to the state interface, beside `activeSlidePanel`:

```ts
  // The assistant is NOT a SlidePanel. It has its own open/closed state so it
  // can stay on screen while it navigates you somewhere — see Task 3's
  // regression test and the 2026-08-05 presence spec.
  assistantDockOpen: boolean
```

Add to the actions interface:

```ts
  setAssistantDockOpen: (open: boolean) => void
  toggleAssistantDock: () => void
```

Add to the initial state (beside `activeSlidePanel: null`):

```ts
  assistantDockOpen: storedAssistantDockOpen(),
```

Add the implementations beside `setSlidePanel`:

```ts
  setAssistantDockOpen: (open) => {
    persistAssistantDockOpen(open)
    set({ assistantDockOpen: open })
  },
  toggleAssistantDock: () => {
    const next = !get().assistantDockOpen
    persistAssistantDockOpen(next)
    set({ assistantDockOpen: next })
  },
```

If the store's `create` call does not already destructure `get`, add it.

- [ ] **Step 4: Run the test, confirm it passes**

- [ ] **Step 5: `tsc -b` and the full suite**

```
pixi run bash -c 'cd pypsa-gui/frontend && npx tsc -b && npm run test'
```

Both must be clean. (`npx` works *inside* the pixi shell; it is only absent from the bare PATH.)

- [ ] **Step 6: Commit**

```bash
git add pypsa-gui/frontend/src/store/uiStore.ts pypsa-gui/frontend/src/store/uiStore.assistantDock.test.ts
git diff --cached --name-only
git commit -m "feat(ui): add assistant dock open/closed state to uiStore"
```

---

### Task 2: The `AssistantDock` component

**Files:**
- Create: `pypsa-gui/frontend/src/components/AssistantDock.tsx`
- Test: `pypsa-gui/frontend/src/components/AssistantDock.test.tsx` (create)

**Interfaces:**
- Consumes: `assistantDockOpen`, `setAssistantDockOpen`, `toggleAssistantDock`
  from Task 1.
- Produces: default export `AssistantDock`, taking no props. Task 3 mounts it in
  `App.tsx`.

**The load-bearing design decision:** `<ChatPanel />` is rendered
**unconditionally** and hidden with CSS when the dock is collapsed — never
unmounted. This mirrors the canvas column in `App.tsx`, which is kept mounted
with `display:none` so its state survives. Unmounting `ChatPanel` is what
destroys a turn in progress; see the SSE-cleanup comment at
`ChatPanel.tsx:1606`, which exists only because the panel used to disappear
mid-answer.

Mock `ChatPanel` in this test file — it opens SSE streams and is exercised by
its own suite. This test is about the dock's shell.

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useUIStore } from '../store/uiStore'

vi.mock('./ChatPanel', () => ({
  default: () => <div data-testid="chat-panel-stub">chat</div>,
}))

import AssistantDock from './AssistantDock'

describe('AssistantDock', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false })
  })

  it('shows the launcher when collapsed', () => {
    render(<AssistantDock />)
    expect(screen.getByTestId('assistant-dock-launcher')).toBeInTheDocument()
  })

  it('keeps ChatPanel mounted while collapsed', () => {
    render(<AssistantDock />)
    expect(screen.getByTestId('chat-panel-stub')).toBeInTheDocument()
  })

  it('expands when the launcher is clicked', async () => {
    const user = userEvent.setup()
    render(<AssistantDock />)
    await user.click(screen.getByTestId('assistant-dock-launcher'))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(screen.getByTestId('assistant-dock-body')).toBeVisible()
  })

  it('collapses again from the header control', async () => {
    const user = userEvent.setup()
    useUIStore.setState({ assistantDockOpen: true })
    render(<AssistantDock />)
    await user.click(screen.getByTestId('assistant-dock-collapse'))
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(screen.getByTestId('chat-panel-stub')).toBeInTheDocument()
  })
})
```

The second and fourth cases are the point of the component: the panel is present
in the DOM whether the dock is open or closed.

- [ ] **Step 2: Run it, confirm it fails**

Expected: module-not-found for `./AssistantDock`.

- [ ] **Step 3: Implement**

```tsx
import { MessageSquare, PanelRightClose } from 'lucide-react'
import { useUIStore } from '../store/uiStore'
import ChatPanel from './ChatPanel'
import ErrorBoundary from './ErrorBoundary'

/**
 * The assistant's own column, mounted beside the main area rather than inside
 * the SlidePanel slot.
 *
 * Why this is not a SlidePanel: `activeSlidePanel` holds ONE value, so while
 * the assistant occupied it the assistant was mutually exclusive with every
 * view it exists to explain — and `applyUiNavigate` calling
 * `setSlidePanel('results')` closed the assistant in the act of obeying you.
 *
 * ChatPanel is rendered unconditionally and hidden with CSS when collapsed.
 * Unmounting it mid-turn is what produced "still streaming, no tokens on
 * screen"; keeping it mounted is the fix, not an optimisation.
 */
export default function AssistantDock() {
  const { assistantDockOpen, setAssistantDockOpen } = useUIStore()

  return (
    <div
      className={`flex flex-col min-h-0 border-l border-border bg-bg shrink-0 ${
        assistantDockOpen ? 'w-[380px]' : 'w-10'
      }`}
      data-testid="assistant-dock"
    >
      {assistantDockOpen ? (
        <div className="flex items-center gap-2 px-3 h-9 border-b border-border bg-bg-2 shrink-0">
          <span className="font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-accent">
            ASSISTANT
          </span>
          <span className="flex-1" />
          <button
            onClick={() => setAssistantDockOpen(false)}
            title="Collapse the assistant"
            data-testid="assistant-dock-collapse"
            className="text-muted hover:text-text p-1 rounded hover:bg-panel transition-colors"
          >
            <PanelRightClose size={14} />
          </button>
        </div>
      ) : (
        <button
          onClick={() => setAssistantDockOpen(true)}
          title="Open the assistant"
          data-testid="assistant-dock-launcher"
          className="flex items-center justify-center h-10 w-full text-muted hover:text-accent hover:bg-panel transition-colors"
        >
          <MessageSquare size={16} />
        </button>
      )}

      {/* Never unmounted — see the class docstring. */}
      <div
        className={`flex-1 min-h-0 overflow-hidden ${assistantDockOpen ? '' : 'hidden'}`}
        data-testid="assistant-dock-body"
      >
        <ErrorBoundary label="The assistant crashed">
          <ChatPanel />
        </ErrorBoundary>
      </div>
    </div>
  )
}
```

Before writing this, check how `ErrorBoundary` is exported in `App.tsx` — it is
declared there today. If it is not exported from a shared module, either export
it from its current home or inline an equivalent boundary here; do not duplicate
the class body. Record which you did in your report.

Check `lucide-react` actually exports `PanelRightClose` before using it; if not,
pick another icon already used in the codebase.

- [ ] **Step 4: Run the test, confirm it passes**

- [ ] **Step 5: `tsc -b` and the full suite**

- [ ] **Step 6: Commit**

```bash
git add pypsa-gui/frontend/src/components/AssistantDock.tsx pypsa-gui/frontend/src/components/AssistantDock.test.tsx
git diff --cached --name-only
git commit -m "feat(ui): add AssistantDock shell with always-mounted ChatPanel"
```

---

### Task 3: Remove `'chat'` from `SlidePanel` and wire the dock in

**Files:**
- Modify: `pypsa-gui/frontend/src/store/uiStore.ts:30` (the union)
- Modify: `pypsa-gui/frontend/src/App.tsx` (`PANEL_META`, `fullPageContent`, the body layout)
- Modify: `pypsa-gui/frontend/src/layout/Sidebar.tsx:1340-1345`
- Modify: `pypsa-gui/frontend/src/components/ChatPanel.tsx` (`:4`, `:112`, `:198`, `:1591-1612`)
- Modify: `pypsa-gui/frontend/src/components/ChatPanel.test.tsx:256-266` (the stale comment)
- Test: `pypsa-gui/frontend/src/components/AssistantDock.eviction.test.tsx` (create)

**Interfaces:**
- Consumes: `AssistantDock` from Task 2, dock actions from Task 1.

This is the task that fixes the reported bug. Do the regression test first — it
must fail against the current code for the right reason.

- [ ] **Step 1: Write the failing regression test**

```tsx
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useUIStore } from '../store/uiStore'

vi.mock('./ChatPanel', () => ({
  default: () => <div data-testid="chat-panel-stub">chat</div>,
}))

import AssistantDock from './AssistantDock'

describe('the assistant survives its own navigation', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: true, activeSlidePanel: null })
  })

  it('stays mounted and open when a ui_event navigates to a full-screen tab', () => {
    render(<AssistantDock />)
    expect(screen.getByTestId('chat-panel-stub')).toBeInTheDocument()

    // What applyUiNavigate does on `ui_open_panel` → results. `results` is a
    // FULL_SCREEN_TAB, the case that used to take over the whole main area.
    useUIStore.getState().setSlidePanel('results')

    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(screen.getByTestId('chat-panel-stub')).toBeInTheDocument()
    expect(screen.getByTestId('assistant-dock-body')).toBeVisible()
  })
})
```

- [ ] **Step 2: Run it**

This one may already pass once Task 2 exists, because the dock does not read
`activeSlidePanel` at all — that independence *is* the fix. Record the result
honestly either way. Its value is as a regression guard: it fails against any
future change that reconnects the two.

- [ ] **Step 3: Remove `'chat'` from the union**

`uiStore.ts:30` — delete `| 'chat'` from `SlidePanel`.

Then run `tsc -b` and let the compiler enumerate the breakage. Fix every site it
names. The expected set:

`App.tsx` — delete the `chat:` entry from `PANEL_META` (including its two-line
"Chatbot integration v6" comment) and the `case 'chat':` arm of
`fullPageContent`. Remove the now-unused `ChatPanel` import.

`Sidebar.tsx:1340-1345` — repoint the nav item at the dock:

```tsx
      {/* The assistant is not a slide panel — it has its own dock so it can
          stay open while it navigates you somewhere. */}
      <SItem icon={<MessageSquare size={15} />} label="Assistant"
        title="Conversational assistant. Ask questions about the open network, drive tools, confirm destructive actions through a card."
        active={assistantDockOpen}
        onClick={() => { toggleAssistantDock(); onCloseModal?.() }}
      />
```

Pull `assistantDockOpen` and `toggleAssistantDock` from the same `useUIStore`
destructure the component already performs for `activeSlidePanel`/`setSlidePanel`.

`ChatPanel.tsx:198` — remove `|| panel === 'chat'` from the slide-panel branch,
and add an explicit branch above it so the agent can still open the assistant:

```tsx
  } else if (panel === 'chat') {
    ui.setAssistantDockOpen(true)
  } else if (
```

Keep the `Chat: 'chat', chat: 'chat'` aliases at `:112` — they now resolve to
the dock rather than to a slide panel.

- [ ] **Step 4: Mount the dock in `App.tsx`**

In the three-column body, as a sibling of the panel container so it is outside
everything `activeSlidePanel` governs — this is what makes it survive
`FULL_SCREEN_TABS`:

```tsx
          {/* Zone 3 — Right properties panel. Only renders alongside the
              Topology Canvas — hidden while a tab panel occupies the right half. */}
          {!activeSlidePanel && <PropertiesPanel />}

          {/* Zone 5 — The assistant. Outside the panel container on purpose:
              it must stay on screen while a full-screen tab owns the main
              area, because it is what put that tab there. */}
          <AssistantDock />
        </div>
```

Import `AssistantDock` at the top of `App.tsx`.

- [ ] **Step 5: Correct the comments that the change falsifies**

`ChatPanel.tsx:4` says the panel "Lives in the SlidePanel slot (kind='chat')".
That is no longer true — rewrite it to name the dock.

`ChatPanel.tsx:1591-1612` — the SSE-cleanup comment explains itself entirely in
terms of "this panel is mounted only while `activeSlidePanel === 'chat'`". The
guard must stay (the dock can still unmount on a project switch or a crash), but
the reasoning is now wrong. Rewrite it to say the panel is mounted for the app's
lifetime inside `AssistantDock`, that navigation no longer unmounts it, and that
the guard remains for the paths that still can.

`ChatPanel.test.tsx:256-266` — the comment block states ChatPanel's ONLY mount
is `activeSlidePanel === 'chat'`. Rewrite it to describe what the test now
guards. Do not delete the test; `scriptNavigateMidTurn` still exercises a real
path.

A stale comment that asserts a fact the code no longer has is worse than no
comment — it is how the model-currency bug survived. Read each one and rewrite
it to what is now true; do not merely delete the sentence.

- [ ] **Step 6: `tsc -b` — must exit 0 with `'chat'` gone from the union**

- [ ] **Step 7: Full suite**

```
pixi run bash -c 'cd pypsa-gui/frontend && npm run test'
```

Expect `ChatPanel.test.tsx` to need updating where it drove the panel through
`setSlidePanel('chat')`. Update those to set `assistantDockOpen` instead. Report
the final file/test counts against the 82/660 baseline.

- [ ] **Step 8: Commit**

```bash
git add pypsa-gui/frontend/src
git diff --cached --name-only
git commit -m "fix(ui): move the assistant into its own dock so it survives its own navigation

'chat' was one of fourteen SlidePanel members and activeSlidePanel holds one
value, so the assistant was mutually exclusive with every view it exists to
explain. applyUiNavigate calling setSlidePanel('results') closed the assistant
in the act of obeying the user. Removing 'chat' from the union makes that
structurally impossible rather than fixed by convention."
```

---

## Self-review notes

**Spec coverage.** This plan implements the spec's dock section and its union
removal, including the stated regression test ("the dock stays mounted across a
`ui_open_panel` navigation to a full-screen tab"). The spec's `ui_context`,
`_ASSISTANT_STANCE`, launch orientation and speech reciprocity are step (c2) and
are deliberately absent.

**Known cost, from the spec, unchanged.** Every full-screen tab and canvas view
now lays out against a main area narrower by the dock's width. Collapsed the
cost is 40px; open it is 380px. Task 3 Step 7's suite run is the check; anything
that breaks visually rather than in a test needs a human look, so flag it for
UAT rather than declaring it fine.

**Deferred deliberately.** The microphone is not surfaced in the collapsed
strip. Voice input is confirmed working in the packaged build, but placing a
second mic control outside `ChatPanel` means lifting `useSpeechToText` state out
of it — that belongs with the composer work in (c2), not here.
