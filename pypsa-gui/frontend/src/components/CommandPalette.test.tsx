// Permanent regression coverage for this call site's Dialog integration —
// added in a fix round after review flagged that the migration onto Dialog
// (see CommandPalette.tsx) had no committed test, only a throwaway one
// deleted before that commit. Four behaviours, matching what the throwaway
// test verified manually at migration time:
//   1. The search input holds focus when the palette opens.
//   2. Escape calls onClose (Dialog owns Escape now, not the palette).
//   3. ArrowDown moves the highlighted item and the input keeps focus —
//      catches Dialog's Tab-trap regressing into swallowing arrow keys.
//   4. aria-label is mode-aware: "Command palette" / "Switch project".
//
// A note on test 1's actual sensitivity, recorded rather than assumed: the
// fix round that added this file required verifying it against Dialog's
// steal-guard (Dialog.tsx's `!panel.contains(document.activeElement)`
// check) by temporarily removing that guard and re-running this file. The
// result was that all tests here — including this one — still passed with
// the guard gone. Root cause, confirmed by inspection: in this panel's
// markup, the Search icon that precedes the input in the header row isn't
// itself focusable, so the input is *already* the first focusable element
// in DOM order. Dialog's un-guarded fallback ("focus the first focusable
// descendant") therefore lands on the exact same node PaletteShell's own
// mount effect targets — the two mechanisms are indistinguishable from
// outside for this specific call site, unlike e.g. NewProjectWizard's
// BlankTab, where a close button precedes the name field and so genuinely
// competes for the guard's fallback target. The guard's own protective
// behaviour is exercised by Dialog.test.tsx's two dedicated regression
// tests (generic, not call-site-specific), not by this one. What this test
// *does* still protect here: the real, user-facing guarantee that the
// input is immediately typable on open with no click. Note what that does
// and does not detect. Removing Dialog's initial-focus effect alone does
// NOT fail this test — PaletteShell's own mount effect still focuses the
// input — and neither does removing PaletteShell's mount effect alone,
// since Dialog's fallback then lands on the same node. For this markup the
// two mechanisms mutually backstop, so the test fails only when BOTH are
// removed together, or if this panel's header ever grew a focusable
// element ahead of the input (e.g. a filter toggle) without also giving
// that new element's own claim priority. Keyboard-filling text (rather than only asserting
// `document.activeElement`) is kept anyway since it exercises the actually
// load-bearing user behaviour — being able to type immediately — not just
// the DOM's internal focus pointer.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import CommandPalette from './CommandPalette'
import { useUIStore } from '../store/uiStore'
import { fetchLocalSettings, type LocalSettingsState } from '../api/localSettings'

// Partial mock, same shape as pages/LocalSettings.test.tsx: keep everything
// else real, stub only the network call `useLocalSettingsAvailable` wraps.
// Default (below) resolves null — matching what the REAL fetchLocalSettings
// settles to in this jsdom env with no backend (a rejected axios call with
// no `.response`, so `fetchLocalSettings` rethrows, `retry: false` fails the
// query fast, and `data` stays `undefined` -> `useLocalSettingsAvailable()`
// reports false) — so every pre-existing test in this file keeps its old
// behaviour unless it opts into a different mock.
vi.mock('../api/localSettings', async (orig) => ({
  ...(await orig<typeof import('../api/localSettings')>()),
  fetchLocalSettings: vi.fn(),
}))

beforeEach(() => {
  vi.mocked(fetchLocalSettings).mockResolvedValue(null)
})

function renderPalette(mode: 'all' | 'projects' = 'all') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useUIStore.setState({ paletteMode: mode })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CommandPalette />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('CommandPalette on Dialog', () => {
  it('holds focus in the search input on open', async () => {
    const user = userEvent.setup()
    renderPalette()
    const input = screen.getByPlaceholderText(/Type a command/i) as HTMLInputElement

    // Focus must already be in the input by the time this runs — if some
    // other element (e.g. the close button, which is later in DOM order)
    // had won the race, this keystroke would land nowhere near the input
    // and the value assertion below would fail. See the file header for
    // why this does NOT isolate Dialog's steal-guard specifically for this
    // call site — verified empirically, not assumed.
    expect(document.activeElement).toBe(input)
    await user.keyboard('overview')
    expect(input.value).toBe('overview')
  })

  it('closes the palette on Escape', async () => {
    const user = userEvent.setup()
    renderPalette()
    expect(screen.getByRole('dialog')).toBeTruthy()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(useUIStore.getState().paletteMode).toBeNull()
  })

  it('moves the highlighted item on ArrowDown while focus stays in the input', async () => {
    const user = userEvent.setup()
    renderPalette()
    const input = screen.getByPlaceholderText(/Type a command/i)
    expect(document.activeElement).toBe(input)

    // Rows are taken POSITIONALLY, not by command name. An earlier version
    // named "Save project" and "Open project overview" and asserted they were
    // adjacent — so adding any command between them failed a test that is
    // about arrow-key navigation and has no opinion on the catalogue. Adding
    // a command is not a regression; the test saying otherwise was.
    const rows = () => screen.getAllByRole('button')
      .filter(b => b.className.includes('w-full flex items-center gap-2.5'))
    const before = rows()
    expect(before.length).toBeGreaterThan(1)
    expect(before[0].className).toMatch(/bg-accent\/10/)
    expect(before[1].className).not.toMatch(/bg-accent\/10/)

    await user.keyboard('{ArrowDown}')

    // Focus must stay in the input — the highlight is state, not DOM focus.
    expect(document.activeElement).toBe(input)
    const after = rows()
    expect(after[0].className).not.toMatch(/bg-accent\/10/)
    expect(after[1].className).toMatch(/bg-accent\/10/)
  })

  it('has an aria-label of "Command palette" in the default mode', () => {
    renderPalette('all')
    expect(screen.getByRole('dialog', { name: 'Command palette' })).toBeTruthy()
  })

  it('has an aria-label of "Switch project" in projects mode', () => {
    renderPalette('projects')
    expect(screen.getByRole('dialog', { name: 'Switch project' })).toBeTruthy()
  })
})

// Regression coverage for the defect that already escaped a review round on
// this feature: deleting the `if (settingsAvailable)` gate around the
// 'act-settings' push in CommandPalette.tsx's useCommands (mode === 'all'
// block) leaves every other test in this suite — and the rest of the
// frontend suite — green, because nothing else in the app asserts on ⌘K's
// entry list. `pages/LocalSettings.test.tsx` only covers the pane itself
// (`<LocalSettings />` rendered directly), never `useCommands` or
// `CommandPalette`, so it cannot catch a regression reached through this
// door. Same gate as the Sidebar row: `useLocalSettingsAvailable()`, backed
// by the shared `['localSettings']` query.
const SETTINGS_STATE: LocalSettingsState = { key_set: false, key_hint: null, log_path: '/tmp/pypsa-gui.log' }

describe('act-settings entry (⌘K) tracks local-settings availability', () => {
  it('is absent when local settings resolve to null (web deployment)', async () => {
    vi.mocked(fetchLocalSettings).mockResolvedValue(null)
    renderPalette()

    // Confirms the palette actually rendered (and the mocked query had a
    // chance to settle) before asserting the gated entry never appeared —
    // an always-present row is the anchor, same idiom as the ArrowDown test
    // above that keys off "Save project".
    await screen.findByRole('button', { name: /Save project/i })
    expect(screen.queryByRole('button', { name: /Open settings/i })).toBeNull()
  })

  it('is present once local settings resolve to a real state (desktop app)', async () => {
    vi.mocked(fetchLocalSettings).mockResolvedValue(SETTINGS_STATE)
    renderPalette()

    expect(await screen.findByRole('button', { name: /Open settings/i })).toBeTruthy()
  })
})
