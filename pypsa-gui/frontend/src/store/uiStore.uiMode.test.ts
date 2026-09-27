import { afterEach, describe, expect, it, vi } from 'vitest'

// Guided-mode spec §3.1 / §3.2 / §3.7 — `uiMode` and `uiModeExplicit`.
//
// The initial mode is decided ONCE, at module load, from what is already in
// localStorage (FIRST_RUN is a module-level constant). So every case here
// seeds storage first and then imports a FRESH copy of the store with
// `vi.resetModules()` + dynamic import — importing the shared copy would test
// whatever state the first import happened to see.

const MODE_KEY = 'network-diagram:ui-mode'
const EXPLICIT_KEY = 'network-diagram:ui-mode-explicit'

async function freshStore(seed: Record<string, string> = {}) {
  vi.resetModules()
  localStorage.clear()
  for (const [k, v] of Object.entries(seed)) localStorage.setItem(k, v)
  const mod = await import('./uiStore')
  return mod.useUIStore
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('initial uiMode — the §3.2 truth table', () => {
  it('stored mode + explicit flag → stored mode, explicit', async () => {
    const s = await freshStore({ [MODE_KEY]: 'guided', [EXPLICIT_KEY]: '1', 'network-diagram:current-project': 'P' })
    expect(s.getState().uiMode).toBe('guided')
    expect(s.getState().uiModeExplicit).toBe(true)

    const t = await freshStore({ [MODE_KEY]: 'expert', [EXPLICIT_KEY]: '1' })
    expect(t.getState().uiMode).toBe('expert')
    expect(t.getState().uiModeExplicit).toBe(true)
  })

  it('stored mode without the flag → stored mode, implicit', async () => {
    // An existing user (legacy key present) whose stored mode is guided keeps it.
    const s = await freshStore({ [MODE_KEY]: 'guided', 'network-diagram:current-project': 'P' })
    expect(s.getState().uiMode).toBe('guided')
    expect(s.getState().uiModeExplicit).toBe(false)

    // A store with nothing else but a stored expert keeps expert: a stored
    // value always wins over the first-time rule.
    const t = await freshStore({ [MODE_KEY]: 'expert' })
    expect(t.getState().uiMode).toBe('expert')
    expect(t.getState().uiModeExplicit).toBe(false)
  })

  it('nothing stored, first-time user → guided, implicit, and persisted once', async () => {
    const s = await freshStore()
    expect(s.getState().uiMode).toBe('guided')
    expect(s.getState().uiModeExplicit).toBe(false)
    // Persisted so that the first legacy key written later cannot turn this
    // user into an "existing user" who flips to Expert on the next load.
    expect(localStorage.getItem(MODE_KEY)).toBe('guided')
    expect(localStorage.getItem(EXPLICIT_KEY)).toBeNull()
  })

  it('nothing stored, existing user → expert, implicit, nothing written', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'P' })
    expect(s.getState().uiMode).toBe('expert')
    expect(s.getState().uiModeExplicit).toBe(false)
    expect(localStorage.getItem(MODE_KEY)).toBeNull()
  })

  it('unreadable storage → expert, implicit', async () => {
    vi.resetModules()
    localStorage.clear()
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('SecurityError') })
    vi.spyOn(Storage.prototype, 'key').mockImplementation(() => { throw new DOMException('SecurityError') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('SecurityError') })
    vi.spyOn(Storage.prototype, 'length', 'get').mockImplementation(() => { throw new DOMException('SecurityError') })
    const { useUIStore } = await import('./uiStore')
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().uiModeExplicit).toBe(false)
  })
})

describe('first-time user detection (isLegacyKey)', () => {
  it('a store holding only theme-schema is still a first-time user', async () => {
    const s = await freshStore({ 'network-diagram:theme-schema': '2' })
    expect(s.getState().uiMode).toBe('guided')
  })

  it('network-diagram:current-project makes an existing user', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'demo' })
    expect(s.getState().uiMode).toBe('expert')
  })

  it('pypsa-guide-seen:* makes an existing user', async () => {
    const s = await freshStore({ 'pypsa-guide-seen:x': '1' })
    expect(s.getState().uiMode).toBe('expert')
  })

  it('results:active-tab makes an existing user', async () => {
    const s = await freshStore({ 'results:active-tab': 'dispatch' })
    expect(s.getState().uiMode).toBe('expert')
  })

  it('unrelated keys (chat:*, pypsa-gui:map:*) do not', async () => {
    const s = await freshStore({ 'chat:draft': 'x', 'pypsa-gui:map:zoom': '3' })
    expect(s.getState().uiMode).toBe('guided')
  })

  it('an invalid stored mode is treated as absent', async () => {
    const s = await freshStore({ [MODE_KEY]: 'wizard', 'network-diagram:current-project': 'P' })
    expect(s.getState().uiMode).toBe('expert')
    expect(s.getState().uiModeExplicit).toBe(false)
  })
})

describe('setUiMode', () => {
  it('an explicit set persists both keys', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'P' })
    s.getState().setUiMode('guided', { explicit: true })
    expect(s.getState().uiMode).toBe('guided')
    expect(s.getState().uiModeExplicit).toBe(true)
    expect(localStorage.getItem(MODE_KEY)).toBe('guided')
    expect(localStorage.getItem(EXPLICIT_KEY)).toBe('1')
  })

  it('an implicit set writes the mode only and never clears an existing explicit flag', async () => {
    const s = await freshStore({ [MODE_KEY]: 'expert', [EXPLICIT_KEY]: '1' })
    s.getState().setUiMode('guided')
    expect(localStorage.getItem(MODE_KEY)).toBe('guided')
    expect(localStorage.getItem(EXPLICIT_KEY)).toBe('1')
    expect(s.getState().uiModeExplicit).toBe(true)

    const t = await freshStore({ 'network-diagram:current-project': 'P' })
    t.getState().setUiMode('guided')
    expect(localStorage.getItem(MODE_KEY)).toBe('guided')
    expect(localStorage.getItem(EXPLICIT_KEY)).toBeNull()
    expect(t.getState().uiModeExplicit).toBe(false)
  })

  it('readStoredUiMode reads what is persisted', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'P' })
    expect(s.getState().readStoredUiMode()).toBeNull()
    s.getState().setUiMode('expert', { explicit: true })
    expect(s.getState().readStoredUiMode()).toBe('expert')
  })

  it('survives a storage that throws on write', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'P' })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('QuotaExceededError') })
    expect(() => s.getState().setUiMode('guided', { explicit: true })).not.toThrow()
    expect(s.getState().uiMode).toBe('guided')
  })
})

describe('noteNewProjectCreated — G4 literal', () => {
  for (const kind of ['blank', 'template', 'file', 'clone', 'study'] as const) {
    it(`${kind}: implicit expert → guided (persisted, still implicit)`, async () => {
      const s = await freshStore({ 'network-diagram:current-project': 'P' })
      expect(s.getState().uiMode).toBe('expert')
      s.getState().noteNewProjectCreated(kind)
      expect(s.getState().uiMode).toBe('guided')
      expect(s.getState().uiModeExplicit).toBe(false)
      expect(localStorage.getItem(MODE_KEY)).toBe('guided')
      expect(localStorage.getItem(EXPLICIT_KEY)).toBeNull()
    })

    it(`${kind}: an explicit expert is never flipped`, async () => {
      const s = await freshStore({ [MODE_KEY]: 'expert', [EXPLICIT_KEY]: '1' })
      s.getState().noteNewProjectCreated(kind)
      expect(s.getState().uiMode).toBe('expert')
      expect(s.getState().uiModeExplicit).toBe(true)
      expect(localStorage.getItem(MODE_KEY)).toBe('expert')
    })
  }
})

describe('switching to guided prunes a hidden slide panel (§3.7)', () => {
  it('a hidden panel becomes hubDesign when a project is open', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'P' })
    s.setState({ currentProject: 'P', activeSlidePanel: 'simparams' })
    s.getState().setUiMode('guided', { explicit: true })
    expect(s.getState().activeSlidePanel).toBe('hubDesign')
  })

  it('a hidden panel closes when no project is open', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'P' })
    s.setState({ currentProject: null, activeSlidePanel: 'simparams' })
    s.getState().setUiMode('guided', { explicit: true })
    expect(s.getState().activeSlidePanel).toBeNull()
  })

  it('results, hubDesign and no panel stay as they are', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'P' })
    for (const p of ['results', 'hubDesign', null] as const) {
      s.setState({ currentProject: 'P', activeSlidePanel: p, uiMode: 'expert' })
      s.getState().setUiMode('guided', { explicit: true })
      expect(s.getState().activeSlidePanel).toBe(p)
    }
  })

  it('switching to expert closes and opens nothing', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'P' })
    s.setState({ currentProject: 'P', activeSlidePanel: 'hubDesign', uiMode: 'guided' })
    s.getState().setUiMode('expert', { explicit: true })
    expect(s.getState().activeSlidePanel).toBe('hubDesign')
    s.setState({ activeSlidePanel: null })
    s.getState().setUiMode('expert', { explicit: true })
    expect(s.getState().activeSlidePanel).toBeNull()
  })
})
