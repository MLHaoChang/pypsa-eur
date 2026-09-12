// extrasStore — the persisted list of attributes the user added to a form
// (spec D23). Pure module over localStorage, every access try/catch-wrapped.
//
// jsdom's localStorage is a bare {} whose methods throw, so these tests install
// a real in-memory Storage first. Production code survives the bare {} because
// every access is wrapped; that survival is asserted at the end.
import { beforeEach, describe, expect, it } from 'vitest'
import { creationScope, editScope, loadExtras, saveExtras } from './extrasStore'

export function installStorage(): Map<string, string> {
  const map = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k: string) => map.get(k) ?? null,
      setItem: (k: string, v: string) => { map.set(k, v) },
      removeItem: (k: string) => { map.delete(k) },
      clear: () => map.clear(),
      key: (i: number) => [...map.keys()][i] ?? null,
      get length() { return map.size },
    },
  })
  return map
}

let store: Map<string, string>
beforeEach(() => { store = installStorage() })

describe('scope keys', () => {
  it('mints the creation-form key D23 fixes', () => {
    expect(creationScope('thermal')).toBe('creationform:extras:thermal')
  })

  it('mints a distinct edit-card key per component class', () => {
    expect(editScope('Generator')).toBe('propertiespanel:extras:Generator')
  })
})

describe('round-trip', () => {
  it('reads back what it wrote', () => {
    saveExtras(creationScope('thermal'), ['weight', 'p_min_pu'])
    expect(loadExtras(creationScope('thermal'))).toEqual(['weight', 'p_min_pu'])
  })

  it('stores the versioned envelope, not a bare array', () => {
    saveExtras('s', ['a'])
    expect(JSON.parse(store.get('s') as string)).toEqual({ v: 1, keys: ['a'] })
  })

  it('returns an empty list for a scope never written', () => {
    expect(loadExtras('nothing-here')).toEqual([])
  })

  it('keeps scopes independent', () => {
    saveExtras(creationScope('thermal'), ['a'])
    saveExtras(creationScope('battery'), ['b'])
    expect(loadExtras(creationScope('thermal'))).toEqual(['a'])
    expect(loadExtras(creationScope('battery'))).toEqual(['b'])
  })

  it('an empty list round-trips as empty', () => {
    saveExtras('s', [])
    expect(loadExtras('s')).toEqual([])
  })
})

describe('version and corruption handling (criterion 31)', () => {
  it('discards an entry whose v is not 1', () => {
    store.set('s', JSON.stringify({ v: 2, keys: ['a'] }))
    expect(loadExtras('s')).toEqual([])
  })

  it('discards an entry with no v at all', () => {
    store.set('s', JSON.stringify({ keys: ['a'] }))
    expect(loadExtras('s')).toEqual([])
  })

  it('discards a bare legacy array', () => {
    store.set('s', JSON.stringify(['a']))
    expect(loadExtras('s')).toEqual([])
  })

  it('tolerates unparseable JSON', () => {
    store.set('s', '{not json')
    expect(loadExtras('s')).toEqual([])
  })

  it('drops non-string entries rather than trusting them', () => {
    store.set('s', JSON.stringify({ v: 1, keys: ['a', 7, null, 'b'] }))
    expect(loadExtras('s')).toEqual(['a', 'b'])
  })

  it('de-duplicates', () => {
    saveExtras('s', ['a', 'a', 'b'])
    expect(loadExtras('s')).toEqual(['a', 'b'])
  })
})

describe('a hostile localStorage never throws out of the module', () => {
  it('survives getItem throwing', () => {
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      // `clear` is a no-op rather than absent: vitest.setup.ts:17 calls it in a
      // global afterEach, and a missing method there would fail the test after
      // its body had already passed.
      value: { getItem() { throw new Error('nope') }, setItem() {}, clear() {}, removeItem() {} },
    })
    expect(loadExtras('s')).toEqual([])
  })

  it('survives setItem throwing — Safari private mode, jsdom bare object', () => {
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem: () => null, setItem() { throw new Error('quota') }, clear() {}, removeItem() {} },
    })
    expect(() => saveExtras('s', ['a'])).not.toThrow()
  })
})
