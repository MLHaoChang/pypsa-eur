// Permanent regression coverage for Task 7's cross-file risk point: App.tsx's
// global keydown effect used to special-case `showShortcuts` in its Escape
// handling because ShortcutsHelp had no Escape of its own. That special case
// was deleted when ShortcutsHelp moved onto Dialog (Dialog now owns Escape
// via a document-capture listener that calls stopPropagation). Nothing else
// in the permanent suite would catch a regression here — e.g. if Dialog's
// Escape listener were ever moved off capture phase, or its stopPropagation
// call were dropped, the removed App.tsx branch's absence would become a
// live bug again: either ShortcutsHelp stops closing on Escape, or it closes
// *and* also collapses whatever sibling Escape branch App.tsx owns, in the
// same keypress.
//
// This does not render the real App.tsx: App.tsx pulls in react-query,
// routing, auth gating, and a dozen page/layout components purely to mount,
// which would make a test of one small effect fragile and slow for
// unrelated reasons. Instead this harness reproduces the *registration
// shape* of the two listeners that matter — App.tsx's window-level, bubble
// phase 'keydown' listener with its remaining Escape branches (verbatim from
// App.tsx as of this commit: compareRailOpen, then activeSlidePanel) — next
// to the real ShortcutsHelp and the real Dialog. That is the actual
// mechanism under test: capture-phase-with-stopPropagation on `document`
// (inside Dialog) vs. bubble-phase on `window` (this harness), not any
// App.tsx-specific plumbing.
//
// Verified this test fails for the right reason, not just any reason: with
// Dialog.tsx's listener temporarily changed to bubble phase (dropping the
// trailing `true` from `document.addEventListener('keydown', onKeyDown,
// true)`), the "does not also fire siblings" assertion below failed exactly
// as expected — compareRailOpen flipped to false on the same Escape that
// closed the dialog. Reverted before committing; see task-7-report.md for
// the exact command and output.
import { describe, it, expect } from 'vitest'
import { useEffect, useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ShortcutsHelp from './ShortcutsHelp'

function Harness() {
  const [showShortcuts, setShowShortcuts] = useState(true)
  const [compareRailOpen, setCompareRailOpen] = useState(true)
  const [activeSlidePanel, setActiveSlidePanel] = useState<string | null>('results')

  // Mirrors App.tsx's global keydown effect's Escape handling, verbatim,
  // post-Task-7 (no showShortcuts branch — that's the point).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (compareRailOpen) { setCompareRailOpen(false); return }
        if (activeSlidePanel) setActiveSlidePanel(null)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [compareRailOpen, activeSlidePanel])

  return (
    <div>
      <div data-testid="compareRailOpen">{String(compareRailOpen)}</div>
      <div data-testid="activeSlidePanel">{String(activeSlidePanel)}</div>
      <ShortcutsHelp open={showShortcuts} onClose={() => setShowShortcuts(false)} />
    </div>
  )
}

describe('ShortcutsHelp Escape vs. App.tsx global Escape handling', () => {
  it('closes on Escape without also firing App.tsx\'s sibling Escape branches on the same keypress', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByTestId('compareRailOpen').textContent).toBe('true')

    await user.keyboard('{Escape}')

    // Dialog's own capture-phase Escape closed it...
    expect(screen.queryByRole('dialog')).toBeNull()
    // ...and that same keypress must not also have reached App.tsx's
    // window-bubble listener — this is the double-handling regression this
    // test exists to catch.
    expect(screen.getByTestId('compareRailOpen').textContent).toBe('true')
  })

  it('leaves App.tsx\'s sibling Escape branches working once the dialog is closed', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.keyboard('{Escape}') // closes ShortcutsHelp only
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByTestId('compareRailOpen').textContent).toBe('true')
    expect(screen.getByTestId('activeSlidePanel').textContent).toBe('results')

    await user.keyboard('{Escape}') // now reaches the window listener: closes the compare rail first
    expect(screen.getByTestId('compareRailOpen').textContent).toBe('false')
    expect(screen.getByTestId('activeSlidePanel').textContent).toBe('results')

    await user.keyboard('{Escape}') // compare rail already closed: closes the slide panel
    expect(screen.getByTestId('activeSlidePanel').textContent).toBe('null')
  })
})
