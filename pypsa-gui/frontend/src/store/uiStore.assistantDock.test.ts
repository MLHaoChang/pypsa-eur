import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useUIStore } from './uiStore'

const KEY = 'network-diagram:assistant-dock'

describe('assistant dock state', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false })
  })

  it('defaults to closed', () => {
    // Checks the store's actual module-load-time default, not whatever
    // beforeEach just forced getState() to. getInitialState() returns the
    // same object reference captured once when create() ran, so it stays
    // true to the real initialiser (storedAssistantDockOpen()) even after
    // setState calls mutate the live state — this fails if the initialiser
    // is ever flipped to default open.
    expect(useUIStore.getInitialState().assistantDockOpen).toBe(false)
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

  // storedAssistantDockOpen() runs eagerly at module scope (inside the
  // initial-state object literal passed to create()). If its try/catch were
  // ever stripped, a blocked/throwing localStorage would throw during
  // module evaluation and break app boot before any component renders. The
  // three cases above only ever mock setItem, so this path had no coverage.
  // Force a fresh module instance (vi.resetModules + dynamic import, same
  // pattern as src/auth/config.test.ts) with getItem throwing during that
  // eager evaluation, and confirm it still resolves to the closed default
  // instead of propagating.
  it('falls back to closed when localStorage.getItem throws at import time', async () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('SecurityError')
    })
    vi.resetModules()
    const fresh = await import('./uiStore')
    expect(fresh.useUIStore.getInitialState().assistantDockOpen).toBe(false)
    spy.mockRestore()
    vi.resetModules()
  })

  // The read-back half of persistence, which nothing above covers. Every
  // other case here loads the module with the key ABSENT (or with getItem
  // throwing) — so they all exercise the default branch and none of them
  // ever sees a stored value come back as `true`. A bug confined to that
  // branch — writing 'Open' where the reader compares to 'open', or the
  // writer and reader disagreeing about the key name — would leave all four
  // green while silently discarding the user's saved preference on every
  // launch. `KEY` above is an independent literal, so a key-name drift in
  // uiStore.ts fails here rather than cancelling itself out.
  //
  // The value has to be in localStorage BEFORE the module's initial-state
  // literal is evaluated (storedAssistantDockOpen() runs eagerly at module
  // scope), hence resetModules + dynamic import, same as the case above.
  it('opens on load when localStorage holds the stored open value', async () => {
    localStorage.setItem(KEY, 'open')
    vi.resetModules()
    const fresh = await import('./uiStore')
    expect(fresh.useUIStore.getInitialState().assistantDockOpen).toBe(true)
    vi.resetModules()
  })
})
