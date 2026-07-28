// Behavioural contract for the Dialog primitive. Every test here maps to a
// success criterion in
// docs/superpowers/specs/2026-07-28-modal-a11y-primitive-design.md.
// The focus-management tests are the point of the file: nothing in this
// frontend trapped focus before this component existed.
import { describe, it, expect, vi } from 'vitest'
import { useEffect, useRef, useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Dialog } from './Dialog'

function TwoButtonDialog({ onClose = () => {}, ...rest }: { onClose?: () => void; [k: string]: unknown }) {
  return (
    <Dialog open onClose={onClose} title="Test dialog" {...rest}>
      <button>first</button>
      <button>last</button>
    </Dialog>
  )
}

// A panel child that manages its own initial focus, declared as a genuinely
// separate component nested inside Dialog's children — not inlined in the
// dialog wrapper itself. This mirrors real call sites like NewProjectWizard's
// BlankTab: the child's mount effect commits BEFORE Dialog's own (children
// commit before parents), so it has already focused itself by the time
// Dialog's initial-focus effect runs.
function AutofocusingChild({ label }: { label: string }) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { ref.current?.focus() }, [])
  return <input ref={ref} aria-label={label} />
}

function DialogWithAutofocusingChild({ open = true, onClose = () => {} }: { open?: boolean; onClose?: () => void }) {
  return (
    <Dialog open={open} onClose={onClose} title="Test dialog">
      <button>first</button>
      <AutofocusingChild label="child input" />
      <button>last</button>
    </Dialog>
  )
}

// The OTHER way a panel child claims initial focus: React's `autoFocus`
// ATTRIBUTE, used at real call sites (SnapshotsPanel's snapshot Label input,
// ScenariosPanel's scenario-name input). This is deliberately NOT the
// useEffect shape above, and the two must stay distinguishable: React applies
// autoFocus via commitMount in the LAYOUT phase, not as a passive effect, so
// it beats even Dialog's own layout effect (layout work also runs
// children-before-parents). A test written in the passive-effect shape
// demonstrably does not catch a restore-capture that reads
// document.activeElement during Dialog's layout effect.
function AutoFocusAttributeChild({ label }: { label: string }) {
  return <input autoFocus aria-label={label} />
}

// Mirrors CreateSnapshotModal's markup shape specifically: a close button
// ahead of the field (so the autofocused input is NOT the first focusable,
// and Dialog's un-guarded fallback would land somewhere else), then the
// `<label><input autoFocus/></label>`, then Cancel/Save.
function DialogWithAutoFocusAttribute({ open = true, onClose = () => {} }: { open?: boolean; onClose?: () => void }) {
  return (
    <Dialog open={open} onClose={onClose} title="Test dialog">
      <button>close</button>
      <label>
        <span>Label</span>
        <AutoFocusAttributeChild label="attr input" />
      </label>
      <button>Cancel</button>
      <button>Save</button>
    </Dialog>
  )
}

describe('Dialog', () => {
  it('renders nothing when closed', () => {
    render(
      <Dialog open={false} onClose={() => {}} title="Test dialog">
        <button>first</button>
      </Dialog>,
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('exposes role=dialog and aria-modal when open', () => {
    render(<TwoButtonDialog />)
    const dlg = screen.getByRole('dialog')
    expect(dlg.getAttribute('aria-modal')).toBe('true')
  })

  it('has an accessible name from the title prop', () => {
    render(<TwoButtonDialog />)
    expect(screen.getByRole('dialog', { name: 'Test dialog' })).toBeTruthy()
  })

  it('accepts a caller-supplied aria-label instead of title', () => {
    render(
      <Dialog open onClose={() => {}} aria-label="Named by caller">
        <button>first</button>
      </Dialog>,
    )
    expect(screen.getByRole('dialog', { name: 'Named by caller' })).toBeTruthy()
  })

  it('moves focus into the dialog on open', () => {
    render(<TwoButtonDialog />)
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true)
  })

  it('wraps Tab from the last focusable back to the first', async () => {
    const user = userEvent.setup()
    render(<TwoButtonDialog />)
    const first = screen.getByRole('button', { name: 'first' })
    const last = screen.getByRole('button', { name: 'last' })
    last.focus()
    await user.tab()
    expect(document.activeElement).toBe(first)
  })

  it('wraps Shift+Tab from the first focusable back to the last', async () => {
    const user = userEvent.setup()
    render(<TwoButtonDialog />)
    const first = screen.getByRole('button', { name: 'first' })
    const last = screen.getByRole('button', { name: 'last' })
    first.focus()
    await user.tab({ shift: true })
    expect(document.activeElement).toBe(last)
  })

  it('closes on Escape without any global key handler', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<TwoButtonDialog onClose={onClose} />)
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('closes on backdrop click by default', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<TwoButtonDialog onClose={onClose} />)
    await user.click(screen.getByRole('dialog'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not close on backdrop click when dismissOnBackdrop is false', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<TwoButtonDialog onClose={onClose} dismissOnBackdrop={false} />)
    await user.click(screen.getByRole('dialog'))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not close when the click originates inside the panel', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<TwoButtonDialog onClose={onClose} />)
    await user.click(screen.getByRole('button', { name: 'first' }))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('restores focus to the invoking element on close', async () => {
    const user = userEvent.setup()

    function Harness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button onClick={() => setOpen(true)}>open me</button>
          <Dialog open={open} onClose={() => setOpen(false)} title="Test dialog">
            <button>inside</button>
          </Dialog>
        </>
      )
    }

    render(<Harness />)
    const trigger = screen.getByRole('button', { name: 'open me' })
    await user.click(trigger)
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true)
    await user.keyboard('{Escape}')
    expect(document.activeElement).toBe(trigger)
  })

  // Regression coverage for a fix-round finding: Dialog's own "focus the
  // first focusable descendant" used to run unconditionally, so a panel
  // child managing its own initial focus (e.g. a form's name field) would
  // have that focus silently stolen back to whatever came first in DOM order
  // — often just a close button. Dialog now skips its own focus-move when
  // the panel already contains document.activeElement.
  it('does not steal focus from a panel child that already autofocused itself on mount', () => {
    render(<DialogWithAutofocusingChild />)
    expect(document.activeElement).toBe(screen.getByLabelText('child input'))
  })

  // Regression coverage for a second finding surfaced while fixing the one
  // above: naively capturing "the element to restore focus to" in the same
  // effect as the guarded focus-move broke restoration whenever a child
  // autofocused itself, because by the time that effect ran,
  // document.activeElement was already the child (not the real invoking
  // control) — so closing the dialog tried to refocus a since-unmounted
  // panel element instead of the button that opened it. The capture now runs
  // in a layout effect, which fires before any child's (passive-effect)
  // autofocus regardless of nesting depth.
  it('restores focus to the invoking element on close even when a panel child autofocused itself', async () => {
    const user = userEvent.setup()

    function Harness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button onClick={() => setOpen(true)}>open me</button>
          {open && <DialogWithAutofocusingChild onClose={() => setOpen(false)} />}
        </>
      )
    }

    render(<Harness />)
    const trigger = screen.getByRole('button', { name: 'open me' })
    await user.click(trigger)
    expect(document.activeElement).toBe(screen.getByLabelText('child input'))
    await user.keyboard('{Escape}')
    expect(document.activeElement).toBe(trigger)
  })

  // Regression coverage for a whole-branch review finding. The layout-effect
  // capture above is correct for children that autofocus in a passive effect,
  // but `autoFocus` is not a passive effect: React commits it in the layout
  // phase, and the layout phase walks children before parents, so the input
  // had already focused itself before Dialog's own useLayoutEffect ran.
  // `restoreRef` then captured the input instead of the invoking control, and
  // closing the dialog refocused a detached node — focus fell to <body>.
  // Dialog now captures the restore target BEFORE commit (render phase), so
  // neither mechanism can get in ahead of it. Keep this test in the
  // `autoFocus`-attribute shape: the passive-effect test above passes against
  // the broken version, so only this shape guards the layout-phase path.
  it('restores focus to the invoking element on close even when a panel child uses the autoFocus attribute', async () => {
    const user = userEvent.setup()

    function Harness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button onClick={() => setOpen(true)}>open me</button>
          {open && <DialogWithAutoFocusAttribute onClose={() => setOpen(false)} />}
        </>
      )
    }

    render(<Harness />)
    const trigger = screen.getByRole('button', { name: 'open me' })
    await user.click(trigger)
    // The attribute won initial focus, and Dialog's steal-guard left it alone
    // rather than pulling focus back to the leading close button.
    expect(document.activeElement).toBe(screen.getByLabelText('attr input'))
    await user.keyboard('{Escape}')
    expect(document.activeElement).toBe(trigger)
  })
})
