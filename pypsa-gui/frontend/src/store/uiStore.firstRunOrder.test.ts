import { afterEach, describe, expect, it, vi } from 'vitest'

// Guided-mode spec §3.2 "Race". `storedTheme()` WRITES
// `network-diagram:theme-schema` while `create()` evaluates the initial
// state, and the first-time branch of `initialUiMode` writes
// `network-diagram:ui-mode`. If the first-time check ran after either write,
// a brand-new user would already look like an existing one. So FIRST_RUN is a
// module-level constant declared above `create(...)`, and this test pins the
// order of both writes against it through `__firstRunProbe`, the store's
// import-time call log.

afterEach(() => {
  vi.restoreAllMocks()
})

describe('FIRST_RUN is decided before any import-time storage write', () => {
  it('detectFirstRun runs first; theme-schema and ui-mode are written after it', async () => {
    vi.resetModules()
    localStorage.clear()
    const setItem = vi.spyOn(Storage.prototype, 'setItem')

    const mod = await import('./uiStore')

    const written = setItem.mock.calls.map(c => c[0])
    expect(written).toContain('network-diagram:theme-schema')
    expect(written).toContain('network-diagram:ui-mode')

    const events = mod.__firstRunProbe.events
    expect(events[0]).toBe('detectFirstRun')
    // Every import-time write the spy saw is in the probe, in the same
    // order, and all of them come after detectFirstRun.
    const probedWrites = events.filter(e => e.startsWith('setItem:')).map(e => e.slice('setItem:'.length))
    expect(probedWrites).toEqual(written)
    expect(events.indexOf('setItem:network-diagram:theme-schema')).toBeGreaterThan(events.indexOf('detectFirstRun'))
    expect(events.indexOf('setItem:network-diagram:ui-mode')).toBeGreaterThan(events.indexOf('detectFirstRun'))
  })

  it('an empty store resolves guided although theme-schema is written in the same import', async () => {
    vi.resetModules()
    localStorage.clear()
    const mod = await import('./uiStore')
    expect(localStorage.getItem('network-diagram:theme-schema')).toBe('2')
    expect(mod.useUIStore.getState().uiMode).toBe('guided')
  })
})
