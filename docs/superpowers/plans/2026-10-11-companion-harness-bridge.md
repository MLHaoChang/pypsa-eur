# Companion harness bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A live sentence on the floating companion uses the existing chat harness, so the companion can navigate, create, confirm, and announce a download on the page the user is already looking at.

**Architecture:** Live does not grow a second tool catalogue or a browser-side dispatcher. When a model key is ready, every live sentence is the same `dispatchSend` the console already uses (`companion:live-utterance` → `ChatPanel`). `ChatPanel` stays mounted while the dock is collapsed, so SSE navigation, confirmations, and tool results already run. This plan makes the outcomes visible beside the companion: one confirmation card, the latest assistant sentence, and a download when a tool result carries one. The two local shortcuts (results walk, open project) stay for the no-key demo and still forward the sentence when a key exists. Writes keep the existing confirmation token, ACL, locks, and budgets.

**Tech Stack:** React 19, TypeScript, Zustand, Vitest, Testing Library. The harness is the existing FastAPI chat stream. No new provider class and no LiveKit in this plan.

## Global Constraints

- One shared harness and one tool catalogue. "Another agent" means another LLM profile, not a second planner. From `.scratch/live-voice/spec.md`: "Every authorized/project-eligible harness tool remains reachable through the existing toolsets; there is no reduced voice-only engineering catalogue. Live voice does not open this console. It uses the same tools on the page the user is already on."
- "The same harness works behind that presence: it opens the requested project, panel, or result, creates what the user asked for, and says when a file is ready to download." Confirmation "is a card beside the companion, on the page being changed."
- Reviewed dictation stays separate. Dictate opens the console panel. A transcript is inserted only after the user accepts it. Dictate does not start `getUserMedia` or LiveKit.
- No paid API calls. Tests use a seeded chat store or a fake stream. Do not add API keys. When `useChatReadiness().ready !== true`, the caption says the model key is not configured and no other tool is claimed to have run.
- Confirmation, locks, budgets, ACL, and the existing profile-switch session rules stay authoritative.
- ChatPanel stays mounted while the dock is collapsed. Do not key the dock ErrorBoundary on `assistantDockOpen`.
- Do not duplicate tool handlers in the browser. Navigation continues through `applyUiNavigate` in `pypsa-gui/frontend/src/components/uiNavigate.ts`, the same function a `ui_open_panel` ui_event uses.
- AR, in `.scratch/live-voice/application-coverage.md`, "is a requested label without an exact match in this inventory." Do not invent a module named Ardat. Mapping AR is issue 08, not this plan.
- This plan does not implement LiveKit, Deepgram, speech synthesis, or the 14 family journeys in the coverage contract. Those stay issues 01, 03, 05, and 08. This plan is the typed live path those issues attach to.
- The results-tab walk keeps its dev `/__pace` body contract (`paced:<ms>`). Do not fall back to a page timer in dev when the body is wrong.

---

## File structure

- Modify `pypsa-gui/frontend/src/components/AssistantDock.tsx` — send every ready live sentence; host the card and the outcome while the dock is closed.
- Modify `pypsa-gui/frontend/src/components/AssistantDock.test.tsx` — non-walk send, no-key honesty, card and download beside the companion.
- Modify `pypsa-gui/frontend/src/components/ChatPanel.tsx` — export the confirmation card; render it in the column only while the dock is open.
- Create `pypsa-gui/frontend/src/components/CompanionOutcome.tsx` — latest assistant sentence and download link, read from `useChatStore`.
- Create `pypsa-gui/frontend/src/components/CompanionOutcome.test.tsx` — caption and download from store messages.
- Modify `pypsa-gui/frontend/src/components/liveWalk.ts` — no behaviour change. The walk remains the no-key local path.

Out of scope for these files: `harness/catalogue.py`, new tools, voice transport, and `capability-inventory.json`.

---

### Task 1: Every ready live sentence enters the existing chat stream

**Files:**
- Modify: `pypsa-gui/frontend/src/components/AssistantDock.tsx` (`onLiveSubmit`, about lines 55–89)
- Test: `pypsa-gui/frontend/src/components/AssistantDock.test.tsx`

**Interfaces:**
- Consumes: `useChatReadiness().ready`, `window` event `companion:live-utterance` with `detail: string`. `ChatPanel` already listens and calls `dispatchSend` when it is not `notReady` and not streaming.
- Produces: a ready live submit always dispatches that event once, including the results walk and "open the project". The dock stays closed. A not-ready submit dispatches nothing except the two local shortcuts, and the caption for any other sentence is `Add an API key for the assistant first. Tab changes and opening a project still work without one.`

- [ ] **Step 1: Write the failing test**

In `AssistantDock.test.tsx`, inside `describe('assistant dock prominence')`, add:

```tsx
it('sends a live sentence other than the walk through the chat stream and leaves the console closed', async () => {
  const user = userEvent.setup()
  const heard: string[] = []
  const onUtterance = (ev: Event) => {
    heard.push((ev as CustomEvent<string>).detail)
  }
  window.addEventListener('companion:live-utterance', onUtterance)
  render(<AssistantDock />)
  await user.click(screen.getByTestId('companion-live'))
  await user.type(screen.getByTestId('companion-live-input'), 'List the generators')
  await user.click(screen.getByTestId('companion-live-send'))
  expect(useUIStore.getState().assistantDockOpen).toBe(false)
  expect(heard).toEqual(['List the generators'])
  expect(screen.getByTestId('companion-live-caption').textContent).toContain('Sent to the assistant.')
  window.removeEventListener('companion:live-utterance', onUtterance)
})
```

The existing `useChatReadiness` mock in this file returns `ready: true`. Add a second file-level mock is unnecessary. For the no-key case, add a sibling test file `AssistantDock.noKey.test.tsx` that mocks `ready: false` (a second `vi.mock` in the same file cannot override the first):

```tsx
vi.mock('../hooks/useChatProfiles', () => ({
  useChatProfiles: () => ({
    data: { profiles: [{ id: 'claude', label: 'Claude', wire: 'anthropic' as const }], active_profile_id: 'claude' },
    isError: false,
  }),
  useChatReadiness: () => ({ effectiveProfileId: 'claude', ready: false }),
}))
```

And the test:

```tsx
it('does not send a general live sentence when the model key is missing', async () => {
  const user = userEvent.setup()
  const heard: string[] = []
  window.addEventListener('companion:live-utterance', (ev: Event) => {
    heard.push((ev as CustomEvent<string>).detail)
  })
  render(<AssistantDock />)
  await user.click(screen.getByTestId('companion-live'))
  await user.type(screen.getByTestId('companion-live-input'), 'List the generators')
  await user.click(screen.getByTestId('companion-live-send'))
  expect(heard).toEqual([])
  expect(useUIStore.getState().assistantDockOpen).toBe(false)
  expect(screen.getByTestId('companion-live-caption').textContent).toContain('Add an API key')
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd pypsa-gui/frontend && npx vitest run src/components/AssistantDock.test.tsx src/components/AssistantDock.noKey.test.tsx`

Expected: the ready test fails because a non-walk sentence already sets "Sent to the assistant." when `ready === true` (this assertion may already pass; keep it as the lock). The no-key test fails because the current caption is `Ask to walk through the key results in each tab, or to open a project.`

- [ ] **Step 3: Write the minimal implementation**

In `onLiveSubmit`, replace the final `ready !== true` branch with:

```tsx
setPresence('listening')
setLiveCaption('Add an API key for the assistant first. Tab changes and opening a project still work without one.')
```

Leave the `ready === true` branch as the dispatch. Do not add a local handler for "list the generators" or any other tool name.

- [ ] **Step 4: Run the tests to verify they pass**

Run the same vitest command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/frontend/src/components/AssistantDock.tsx \
  pypsa-gui/frontend/src/components/AssistantDock.test.tsx \
  pypsa-gui/frontend/src/components/AssistantDock.noKey.test.tsx
git commit -m "fix(chat): send every ready live sentence through the harness"
```

---

### Task 2: One confirmation card beside the companion

**Files:**
- Modify: `pypsa-gui/frontend/src/components/ChatPanel.tsx` (the `ConfirmationCard` function near line 592, and its render near line 3373)
- Modify: `pypsa-gui/frontend/src/components/AssistantDock.tsx`
- Test: `pypsa-gui/frontend/src/components/AssistantDock.test.tsx`

**Interfaces:**
- Consumes: `useChatStore.pending` of type `PendingConfirmationCard` (`tool_name`, `safety_tier`, `confirmation_token`, `args`, `expires_at_epoch_ms`).
- Produces: `export function ConfirmationCard(): JSX.Element | null`. While `assistantDockOpen` is false, `ChatPanel` does not render it and `AssistantDock` renders it once, with `data-testid="companion-confirmation"`. Approve and Deny stay the functions already inside `ConfirmationCard`. Two cards must never be mounted together.

- [ ] **Step 1: Write the failing test**

```tsx
it('shows the pending confirmation beside the companion while the console is closed', () => {
  useUIStore.setState({ assistantDockOpen: false })
  useChatStore.setState({
    pending: {
      tool_use_id: 'tu1',
      tool_name: 'update_component',
      args: { name: 'bus-a' },
      safety_tier: 'write',
      confirmation_token: 'tok',
      ttl_seconds: 60,
      expires_at_epoch_ms: Date.now() + 60_000,
    },
  })
  render(<AssistantDock />)
  const card = screen.getByTestId('companion-confirmation')
  expect(card.textContent).toContain('update_component')
  expect(screen.getByTestId('assistant-dock-body').className.split(/\s+/)).toContain('hidden')
  expect(screen.getAllByTestId('companion-confirmation')).toHaveLength(1)
})
```

Import `useChatStore` from `../store/chatStore`. `getAllByTestId` throws if the node is missing; the length check is the single-card lock. This repo has no `@testing-library/jest-dom`; use `toBeTruthy` and `textContent`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pypsa-gui/frontend && npx vitest run src/components/AssistantDock.test.tsx -t "pending confirmation"`

Expected: FAIL, unable to find `companion-confirmation`.

- [ ] **Step 3: Write the minimal implementation**

Change `function ConfirmationCard` to `export function ConfirmationCard`. Wrap its root element with `data-testid="companion-confirmation"` only when the dock is closed; when the dock is open keep the existing test ids the console tests already query (`chat-confirm-approve` and `chat-confirm-deny`). Read those ids in `ChatPanelSurfaces.test.tsx` before renaming anything.

In `ChatPanel`'s render, gate the card:

```tsx
{assistantDockOpen ? <ConfirmationCard /> : null}
```

`assistantDockOpen` is already available from `useUIStore` in `ChatPanel` (the focus effect reads `assistantEntry`). If it is not in scope at the render site, select it the same way as the other per-field selectors.

In `AssistantDock`, next to the launcher:

```tsx
{!assistantDockOpen ? <ConfirmationCard /> : null}
```

Import `ConfirmationCard` from `./ChatPanel`. The dock test mocks `./ChatPanel`, so add `ConfirmationCard` to that mock and make the mock render a stub when `useChatStore.getState().pending` is set:

```tsx
ConfirmationCard: () => {
  const pending = useChatStore.getState().pending
  if (!pending) return null
  return <div data-testid="companion-confirmation">{pending.tool_name}</div>
},
```

The real single-card behaviour is then locked by a test that does not mock `ChatPanel`: extend `ChatPanelSurfaces.test.tsx` with the dock closed and assert one approve button. If that file's setup is too heavy, the stub test above is the dock contract and the existing surface tests remain the approve contract.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd pypsa-gui/frontend && npx vitest run src/components/AssistantDock.test.tsx src/components/ChatPanelSurfaces.test.tsx`

Expected: PASS. Approve still posts the existing confirm request. Do not add a second confirm client.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/frontend/src/components/ChatPanel.tsx \
  pypsa-gui/frontend/src/components/AssistantDock.tsx \
  pypsa-gui/frontend/src/components/AssistantDock.test.tsx \
  pypsa-gui/frontend/src/components/ChatPanelSurfaces.test.tsx
git commit -m "fix(chat): show the confirmation card beside the companion"
```

---

### Task 3: Latest outcome and download beside the companion

**Files:**
- Create: `pypsa-gui/frontend/src/components/CompanionOutcome.tsx`
- Create: `pypsa-gui/frontend/src/components/CompanionOutcome.test.tsx`
- Modify: `pypsa-gui/frontend/src/components/AssistantDock.tsx`

**Interfaces:**
- Consumes: `useChatStore.messages` (`ChatMessage`: `role`, `content`, `tool_result`).
- Produces:
  - `downloadHref(result: unknown): string | null` — returns `result.download_url` when it is a string that starts with `/api/`, otherwise `null`.
  - `CompanionOutcome` renders `data-testid="companion-outcome"` with the last `role === 'assistant'` message's `content`, and `data-testid="companion-download"` when the last tool message's `tool_result` has that href. Renders nothing when there is no assistant message.
  - Mounted from `AssistantDock` only while `!assistantDockOpen && liveOpen`.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react'
import { useChatStore } from '../store/chatStore'
import CompanionOutcome from './CompanionOutcome'

it('shows the latest assistant sentence and a download from the tool result', () => {
  useChatStore.setState({
    messages: [
      { id: '1', role: 'assistant', content: 'The network file is ready.', ts: 1 },
      { id: '2', role: 'tool', content: '→ build_delivery', tool_name: 'build_delivery', ts: 2,
        tool_result: { download_url: '/api/files/abc' } },
    ],
  })
  render(<CompanionOutcome />)
  expect(screen.getByTestId('companion-outcome').textContent).toBe('The network file is ready.')
  expect(screen.getByTestId('companion-download').getAttribute('href')).toBe('/api/files/abc')
})

it('ignores a download url that is not an api path', () => {
  useChatStore.setState({
    messages: [
      { id: '1', role: 'assistant', content: 'Done.', ts: 1 },
      { id: '2', role: 'tool', content: '→ build_delivery', ts: 2,
        tool_result: { download_url: 'https://example.invalid/file' } },
    ],
  })
  render(<CompanionOutcome />)
  expect(screen.queryByTestId('companion-download')).toBeNull()
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pypsa-gui/frontend && npx vitest run src/components/CompanionOutcome.test.tsx`

Expected: FAIL, cannot resolve `./CompanionOutcome`.

- [ ] **Step 3: Write the minimal implementation**

```tsx
import { useChatStore } from '../store/chatStore'

export function downloadHref(result: unknown): string | null {
  if (!result || typeof result !== 'object' || !('download_url' in result)) return null
  const href = (result as { download_url?: unknown }).download_url
  if (typeof href !== 'string' || !href.startsWith('/api/')) return null
  return href
}

export default function CompanionOutcome() {
  const messages = useChatStore((s) => s.messages)
  const latest = [...messages].reverse().find((m) => m.role === 'assistant')
  const download = [...messages].reverse().map((m) => downloadHref(m.tool_result)).find((href) => href)
  if (!latest) return null
  return (
    <div className="pointer-events-auto max-w-xs rounded-xl bg-surface px-3 py-2 text-sm text-text shadow-lg" data-no-panel-close>
      <p data-testid="companion-outcome">{latest.content}</p>
      {download ? (
        <a data-testid="companion-download" href={download}>Download</a>
      ) : null}
    </div>
  )
}
```

Mount `<CompanionOutcome />` in `AssistantDock` when `liveOpen && !assistantDockOpen`. The walk caption (`companion-live-caption`) stays the local status line. `companion-outcome` is the harness sentence and must not replace the walk caption while a walk is in progress: pass `hidden={walking}` or skip the mount while `walkCancel.current` is set. A boolean `walking` state set true in the walk branch and false in `onPaceMiss` and on the last `onStep` is the readable form.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd pypsa-gui/frontend && npx vitest run src/components/CompanionOutcome.test.tsx src/components/AssistantDock.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/frontend/src/components/CompanionOutcome.tsx \
  pypsa-gui/frontend/src/components/CompanionOutcome.test.tsx \
  pypsa-gui/frontend/src/components/AssistantDock.tsx
git commit -m "feat(chat): show the harness outcome beside the companion"
```

---

### Task 4: Hidden ChatPanel still applies harness navigation

**Files:**
- Test: `pypsa-gui/frontend/src/components/ChatPanel.applicationNavigation.test.tsx`

**Interfaces:**
- Consumes: `APPLY_UI_NAVIGATE_FOR_TEST` / `applyUiNavigate({ kind: 'navigate', panel_id, results_tab })`.
- Produces: a regression test that a results-tab ui_event sets `activeSlidePanel` to `results` and `resultsTabRequest` to the requested tab while `assistantDockOpen` is false. No new navigation function.

- [ ] **Step 1: Write the failing test**

If `ChatPanel.applicationNavigation.test.tsx` already calls `applyUiNavigate` directly, add:

```tsx
it('applies a results tab while the companion console is closed', () => {
  useUIStore.setState({ assistantDockOpen: false, activeSlidePanel: null, resultsTabRequest: null })
  APPLY_UI_NAVIGATE_FOR_TEST({ kind: 'navigate', panel_id: 'results', results_tab: 'economics' })
  expect(useUIStore.getState().assistantDockOpen).toBe(false)
  expect(useUIStore.getState().activeSlidePanel).toBe('results')
  expect(useUIStore.getState().resultsTabRequest).toBe('economics')
})
```

This test is expected to pass on the current code. If it passes on the first run, keep it. It locks the contract the companion relies on: navigation is store state, not a visible console.

- [ ] **Step 2: Run the test**

Run: `cd pypsa-gui/frontend && npx vitest run src/components/ChatPanel.applicationNavigation.test.tsx`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add pypsa-gui/frontend/src/components/ChatPanel.applicationNavigation.test.tsx
git commit -m "test(chat): keep harness navigation working while the console is closed"
```

---

## Spec coverage

| Spec requirement | Task |
| --- | --- |
| Live uses the same tools on the current page | Task 1 |
| Opens a requested project, panel, or result | Existing `applyUiNavigate` plus Task 4 |
| Creates what the user asked for, through the harness | Task 1 sends the sentence; the harness tools already create. This plan does not add tools. |
| Says when a file is ready to download | Task 3 |
| Confirmation card beside the companion | Task 2 |
| Full transcript stays in the console | `CompanionOutcome` shows the latest sentence only. History stays in `ChatPanel`. |
| No reduced catalogue | No catalogue edit |
| AR mapped to real UI vocabulary | Not this plan. Issue 08. AR has no inventory match and must not be given a made-up expansion. |
| Continuous microphone, LiveKit, speech | Not this plan. Issues 01 and 03. |
| One real journey per application family | Not this plan. Issue 05, after issue 08's matrix. |

## Self-review

- Sending a sentence is not the same as executing all 220 tools. The reachability tests in `pypsa-gui/backend/tests/test_assistant_tool_reachability.py` already offer every tool to the model. This plan makes the companion a client of that stream and shows the three outcomes the spec names for the closed console: navigation, confirmation, and download.
- A missing model key still cannot run those tools. The caption says so. The results walk and the project list remain local.
- Do not start a solve, a study, or a paid request to demonstrate this plan.
