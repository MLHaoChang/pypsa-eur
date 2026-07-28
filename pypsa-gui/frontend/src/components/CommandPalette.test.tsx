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
// input is immediately typable on open with no click — which would break
// if Dialog's initial-focus effect were removed altogether, or if this
// panel's header ever grew a focusable element ahead of the input (e.g. a
// filter toggle) without also giving that new element's own claim
// priority. Keyboard-filling text (rather than only asserting
// `document.activeElement`) is kept anyway since it exercises the actually
// load-bearing user behaviour — being able to type immediately — not just
// the DOM's internal focus pointer.
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import CommandPalette from './CommandPalette'
import { useUIStore } from '../store/uiStore'

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

    // Two actions rows are always present regardless of query/project/asset
    // data: "Save project" (first) and "Open project overview" (second).
    const firstRow = screen.getByRole('button', { name: /Save project/i })
    const secondRow = screen.getByRole('button', { name: /Open project overview/i })
    expect(firstRow.className).toMatch(/bg-accent\/10/)
    expect(secondRow.className).not.toMatch(/bg-accent\/10/)

    await user.keyboard('{ArrowDown}')

    expect(document.activeElement).toBe(input)
    expect(firstRow.className).not.toMatch(/bg-accent\/10/)
    expect(secondRow.className).toMatch(/bg-accent\/10/)
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
