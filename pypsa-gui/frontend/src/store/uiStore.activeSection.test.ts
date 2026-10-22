import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const KEY = (p: string) => `network-diagram:active-section:${p}`

async function freshStore(seed: Record<string, string> = {}) {
  vi.resetModules()
  localStorage.clear()
  for (const [k, v] of Object.entries(seed)) localStorage.setItem(k, v)
  return (await import('./uiStore')).useUIStore
}

beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

describe('the section a project was left in is remembered (design §7)', () => {
  it('a section picked in a project is stored under that project', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'P' })
    s.getState().setActiveSection('commercial')
    expect(localStorage.getItem(KEY('P'))).toBe('commercial')
  })

  it('a reload opens the project at the stored section', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'P', [KEY('P')]: 'assets' })
    expect(s.getState().activeSection).toBe('assets')
  })

  it('switching projects restores each project\'s own section', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'A', [KEY('B')]: 'loads' })
    s.getState().setActiveSection('profiles')
    s.getState().setCurrentProject('B')
    expect(s.getState().activeSection).toBe('loads')
    expect(localStorage.getItem(KEY('A'))).toBe('profiles')
    s.getState().setCurrentProject('A')
    expect(s.getState().activeSection).toBe('profiles')
  })

  it('a project never visited opens with no section, and keeps nothing for it', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'A' })
    s.getState().setActiveSection('assets')
    s.getState().setCurrentProject('C')
    expect(s.getState().activeSection).toBeNull()
    expect(localStorage.getItem(KEY('C'))).toBeNull()
  })

  it('ignores a stored value that is not a flow section', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'P', [KEY('P')]: 'bogus' })
    expect(s.getState().activeSection).toBeNull()
  })

  it('still works, and does not throw, when storage throws', async () => {
    const s = await freshStore({ 'network-diagram:current-project': 'P' })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('blocked') })
    expect(() => s.getState().setActiveSection('scenarios')).not.toThrow()
    expect(s.getState().activeSection).toBe('scenarios')
    expect(() => s.getState().setCurrentProject('Q')).not.toThrow()
    expect(s.getState().activeSection).toBeNull()
  })
})
