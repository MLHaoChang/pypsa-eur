import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useUIStore } from './uiStore'

const KEY = 'network-diagram:assistant-dock'
const WIDTH_KEY = 'network-diagram:assistant-dock-width'

describe('assistant dock state', () => {
  beforeEach(() => {
    // Earlier cases spy on Storage.prototype; without this they leak into
    // the width tests below as a SecurityError from an unrelated mock.
    vi.restoreAllMocks()
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false })
  })

  it('defaults to OPEN on a first launch', () => {
    // This assertion was inverted, deliberately and with a comment saying so.
    // It is flipped here because the approved spec
    // (2026-08-05-assistant-presence-and-deixis-design.md) opens with "an
    // assistant that is present when the tool launches", and a dock that
    // starts as a 40px muted icon is not present — it is the opt-in panel
    // the spec exists to replace, wearing a different shape. Reported from
    // the built app: "I do not see the prominent button for the assistant
    // when the app is launched."
    //
    // getInitialState() is the module-load-time default, so this is true to
    // the real initialiser rather than to whatever beforeEach forced.
    expect(useUIStore.getInitialState().assistantDockOpen).toBe(true)
  })

  it('respects a stored preference to keep it collapsed', () => {
    // Default-open must not mean re-open-every-launch. A user who collapsed
    // it asked for the width back, and overriding that on each start is the
    // behaviour that gets a feature switched off permanently.
    localStorage.setItem(KEY, 'closed')
    expect(useUIStore.getState().readStoredDockOpen()).toBe(false)
    localStorage.setItem(KEY, 'open')
    expect(useUIStore.getState().readStoredDockOpen()).toBe(true)
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
  it('falls back to OPEN when localStorage.getItem throws at import time', async () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('SecurityError')
    })
    vi.resetModules()
    const fresh = await import('./uiStore')
    // Follows the default: a blocked localStorage means we cannot know the
    // user collapsed it, and the spec's presence guarantee should not depend
    // on storage being readable. The value of this test is unchanged — that
    // the throw is caught rather than propagated through module evaluation.
    expect(fresh.useUIStore.getInitialState().assistantDockOpen).toBe(true)
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

  // ── width (Improvement: the dock was a fixed 380px with no resize) ──────

  it('defaults to a width wide enough to read a conversation in', () => {
    expect(useUIStore.getInitialState().assistantDockWidth).toBeGreaterThanOrEqual(380)
  })

  it('setAssistantDockWidth persists the width the user asked for', () => {
    useUIStore.getState().setAssistantDockWidth(640)
    expect(useUIStore.getState().assistantDockWidth).toBe(640)
    expect(Number(localStorage.getItem(WIDTH_KEY))).toBe(640)
  })

  it('floors a too-narrow drag rather than storing it', () => {
    useUIStore.getState().setAssistantDockWidth(10)
    expect(useUIStore.getState().assistantDockWidth).toBeGreaterThanOrEqual(320)
  })

  it('never lets a layout constraint become the stored preference', () => {
    // The invariant the compare rail earned across four fix commits: the
    // store holds the width the user ASKED for, written only by a real drag.
    // Clamping by writing the smaller value back is what silently rewrote a
    // 700px preference to 461 when a panel opened. The constraint belongs in
    // the render, recomputed from a measurement, never persisted.
    useUIStore.getState().setAssistantDockWidth(700)
    const availableNow = 500
    expect(useUIStore.getState().constrainDockWidth(700, availableNow)).toBeLessThan(700)
    expect(useUIStore.getState().assistantDockWidth).toBe(700)
    expect(Number(localStorage.getItem(WIDTH_KEY))).toBe(700)
  })
})
