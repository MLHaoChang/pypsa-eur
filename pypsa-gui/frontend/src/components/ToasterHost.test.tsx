// P31 C2: the "Created '<name>' from template" toast sat over the assistant's
// Send button. The dock is a right-hand column (not a bottom drawer), so the
// toaster's right edge moves left of the open dock on the pages that mount it
// (/app and /projects).
import { afterEach, describe, expect, it } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ToasterHost, { TOAST_GAP } from './ToasterHost'
import { useUIStore } from '../store/uiStore'

const initial = useUIStore.getState()

function container(): HTMLElement {
  const el = document.querySelector<HTMLElement>('[data-rht-toaster]')
  if (!el) throw new Error('no toaster container')
  return el
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ToasterHost />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  useUIStore.setState({
    assistantDockOpen: initial.assistantDockOpen,
    assistantDockWidth: initial.assistantDockWidth,
  })
})

describe('ToasterHost (P31 C2)', () => {
  it('on the workbench with the dock open, the toasts sit left of the dock', () => {
    useUIStore.setState({ assistantDockOpen: true, assistantDockWidth: 420 })
    renderAt('/app?project=x')
    expect(container().style.right).toBe(`${420 + TOAST_GAP}px`)
    expect(container().style.bottom).toBe(`${TOAST_GAP}px`)
  })

  it('follows the store: collapse, reopen and a resize move the offset', () => {
    useUIStore.setState({ assistantDockOpen: true, assistantDockWidth: 420 })
    renderAt('/app')
    act(() => { useUIStore.setState({ assistantDockOpen: false }) })
    expect(container().style.right).toBe(`${TOAST_GAP}px`)
    act(() => { useUIStore.setState({ assistantDockOpen: true }) })
    expect(container().style.right).toBe(`${420 + TOAST_GAP}px`)
    act(() => { useUIStore.setState({ assistantDockWidth: 600 }) })
    expect(container().style.right).toBe(`${600 + TOAST_GAP}px`)
  })

  it('on /projects (the dock is mounted there too) the toasts sit left of it', () => {
    // The template toast is raised on /projects, beside the same dock, just
    // before the navigation to /app (the P31 smoke caught this).
    useUIStore.setState({ assistantDockOpen: true, assistantDockWidth: 420 })
    renderAt('/projects')
    expect(container().style.right).toBe(`${420 + TOAST_GAP}px`)
  })

  it('on a page without the dock (login, admin) the toasts keep the corner', () => {
    useUIStore.setState({ assistantDockOpen: true, assistantDockWidth: 420 })
    renderAt('/login')
    expect(container().style.right).toBe(`${TOAST_GAP}px`)
    cleanup()
    renderAt('/admin/users')
    expect(container().style.right).toBe(`${TOAST_GAP}px`)
  })

  it('keeps the bottom-right position and the shared toast style', () => {
    useUIStore.setState({ assistantDockOpen: false })
    renderAt('/app')
    expect(container().style.position).toBe('fixed')
    expect(container().style.left).toBe(`${TOAST_GAP}px`)
    expect(container().style.top).toBe(`${TOAST_GAP}px`)
  })
})
