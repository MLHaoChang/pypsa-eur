// The sites document store: memory-first, debounced PUT, flush on demand,
// localStorage only as a fallback when the server is unreachable, and every
// write refused while the project is read-only.
//
// Plan: docs/superpowers/plans/2026-09-29-3d-site-view-phase1.md, Task 1.5.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/sites', () => ({
  sitesApi: {
    getSites: vi.fn(),
    putSites: vi.fn(),
  },
}))

import { sitesApi } from '../api/sites'
import { useUIStore } from '../store/uiStore'
import {
  SITES_SAVE_DEBOUNCE_MS,
  flushPendingSitesToServer,
  localSitesKey,
  persistSitesOnUnload,
  useSitesStore,
} from './sitesStore'
import { emptySitesDocument, type Site, type SitesDocument } from './types'

const getSites = vi.mocked(sitesApi.getSites)
const putSites = vi.mocked(sitesApi.putSites)

const site = (over: Partial<Site> = {}): Site => ({
  id: 'site_a',
  name: 'Campus',
  buses: ['B1'],
  boundary: [[6.83, 53.44], [6.84, 53.44], [6.84, 53.43]],
  origin: { lng: 6.835, lat: 53.435 },
  placements: {},
  ...over,
})
const doc = (sites: Site[] = [site()]): SitesDocument => ({ version: 1, sites })

beforeEach(() => {
  vi.useFakeTimers()
  useSitesStore.getState().resetForTests()
  useUIStore.setState({ readOnly: false, readOnlyReason: 'writable' })
  getSites.mockReset()
  putSites.mockReset()
  putSites.mockResolvedValue({ saved: 'p', sites: 1 })
})
afterEach(() => {
  vi.useRealTimers()
})

describe('loading', () => {
  it('loads once per project and caches', async () => {
    getSites.mockResolvedValue(doc())
    await useSitesStore.getState().ensureLoaded('p')
    await useSitesStore.getState().ensureLoaded('p')
    expect(getSites).toHaveBeenCalledTimes(1)
    expect(useSitesStore.getState().docFor('p').sites[0].name).toBe('Campus')
  })

  it('falls back to localStorage only when the GET throws, never when the server says empty', async () => {
    localStorage.setItem(localSitesKey('p'), JSON.stringify(doc([site({ name: 'Stale local' })])))
    getSites.mockResolvedValue(emptySitesDocument())
    await useSitesStore.getState().ensureLoaded('p')
    expect(useSitesStore.getState().docFor('p').sites).toEqual([])

    useSitesStore.getState().resetForTests()
    getSites.mockRejectedValue(new Error('offline'))
    await useSitesStore.getState().ensureLoaded('p')
    expect(useSitesStore.getState().docFor('p').sites[0].name).toBe('Stale local')
  })
})

describe('writes', () => {
  beforeEach(async () => {
    getSites.mockResolvedValue(doc())
    await useSitesStore.getState().ensureLoaded('p')
  })

  it('setPlacement writes the store synchronously and PUTs after the debounce', async () => {
    useSitesStore.getState().setPlacement('p', 'site_a', 'Generator:G1', { x: 1, y: 2, heading: 90 })
    expect(useSitesStore.getState().docFor('p').sites[0].placements['Generator:G1']).toEqual({ x: 1, y: 2, heading: 90 })
    expect(putSites).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(SITES_SAVE_DEBOUNCE_MS + 1)
    expect(putSites).toHaveBeenCalledTimes(1)
    expect(putSites.mock.calls[0][0]).toBe('p')
    expect(putSites.mock.calls[0][1].sites[0].placements['Generator:G1']).toEqual({ x: 1, y: 2, heading: 90 })
  })

  it('three edits inside the window produce one PUT with the last document', async () => {
    const s = useSitesStore.getState()
    s.setPlacement('p', 'site_a', 'Generator:G1', { x: 1, y: 0, heading: 0 })
    s.setPlacement('p', 'site_a', 'Generator:G1', { x: 2, y: 0, heading: 0 })
    s.setPlacement('p', 'site_a', 'Generator:G1', { x: 3, y: 0, heading: 0 })
    await vi.advanceTimersByTimeAsync(SITES_SAVE_DEBOUNCE_MS + 1)
    expect(putSites).toHaveBeenCalledTimes(1)
    expect(putSites.mock.calls[0][1].sites[0].placements['Generator:G1'].x).toBe(3)
  })

  it('flushPendingSitesToServer PUTs immediately and reports server', async () => {
    useSitesStore.getState().setPlacement('p', 'site_a', 'Generator:G1', { x: 5, y: 5, heading: 0 })
    const r = await flushPendingSitesToServer('p')
    expect(r).toEqual({ status: 'server', sites: 1 })
    expect(putSites).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(SITES_SAVE_DEBOUNCE_MS + 1)
    expect(putSites).toHaveBeenCalledTimes(1) // the debounce was cancelled by the flush
  })

  it('a flush with nothing pending reports nothing', async () => {
    expect(await flushPendingSitesToServer('p')).toEqual({ status: 'nothing', sites: 0 })
    expect(putSites).not.toHaveBeenCalled()
  })

  it('after a Save-As the flush to the new name carries the previous project\'s pending document', async () => {
    useSitesStore.getState().setPlacement('p', 'site_a', 'Generator:G1', { x: 7, y: 7, heading: 0 })
    const r = await flushPendingSitesToServer('p-copy', { previousProject: 'p' })
    expect(r.status).toBe('server')
    expect(putSites.mock.calls[0][0]).toBe('p-copy')
    expect(putSites.mock.calls[0][1].sites[0].placements['Generator:G1'].x).toBe(7)
    expect(useSitesStore.getState().docFor('p-copy').sites[0].placements['Generator:G1'].x).toBe(7)
  })

  it('a failed PUT writes localStorage and a later success clears it', async () => {
    putSites.mockRejectedValueOnce(new Error('offline'))
    useSitesStore.getState().setPlacement('p', 'site_a', 'Generator:G1', { x: 9, y: 9, heading: 0 })
    await vi.advanceTimersByTimeAsync(SITES_SAVE_DEBOUNCE_MS + 1)
    expect(putSites).toHaveBeenCalledTimes(1)
    const stored = JSON.parse(localStorage.getItem(localSitesKey('p')) ?? 'null') as SitesDocument
    expect(stored.sites[0].placements['Generator:G1'].x).toBe(9)

    const r = await flushPendingSitesToServer('p')
    expect(r.status).toBe('server')
    expect(localStorage.getItem(localSitesKey('p'))).toBeNull()
  })

  it('upsertSite / removeSite / setSiteBuses / setBoundary keep the document valid', () => {
    const s = useSitesStore.getState()
    s.upsertSite('p', site({ id: 'site_b', name: 'Second' }))
    expect(s.docFor('p').sites.map(x => x.id)).toEqual(['site_a', 'site_b'])
    s.upsertSite('p', site({ id: 'site_b', name: 'Second renamed' }))
    expect(s.docFor('p').sites.map(x => x.name)).toEqual(['Campus', 'Second renamed'])
    expect(() => s.upsertSite('p', site({ id: '../x' }))).toThrow(/id/)
    expect(() => s.setBoundary('p', 'site_a', [[0, 0], [1, 1]])).toThrow(/boundary/)
    s.setSiteBuses('p', 'site_a', ['B1', 'B2'])
    expect(s.docFor('p').sites[0].buses).toEqual(['B1', 'B2'])
    s.removeSite('p', 'site_b')
    expect(s.docFor('p').sites.map(x => x.id)).toEqual(['site_a'])
  })

  it('renamePlacement re-keys one class only', () => {
    const s = useSitesStore.getState()
    s.setPlacement('p', 'site_a', 'Generator:old', { x: 1, y: 1, heading: 0 })
    s.setPlacement('p', 'site_a', 'Load:old', { x: 2, y: 2, heading: 0 })
    expect(s.renamePlacement('p', 'Generator', 'old', 'new')).toBe(true)
    const pl = s.docFor('p').sites[0].placements
    expect(Object.keys(pl).sort()).toEqual(['Generator:new', 'Load:old'])
    expect(s.renamePlacement('p', 'Store', 'nope', 'x')).toBe(false)
  })

  it('subscribers see every write', () => {
    const seen: number[] = []
    const unsub = useSitesStore.subscribe(st => seen.push(Object.keys(st.docFor('p').sites[0].placements).length))
    useSitesStore.getState().setPlacement('p', 'site_a', 'Generator:G1', { x: 0, y: 0, heading: 0 })
    useSitesStore.getState().setPlacement('p', 'site_a', 'Generator:G2', { x: 0, y: 0, heading: 0 })
    unsub()
    expect(seen).toEqual([1, 2])
  })
})

describe('read-only', () => {
  beforeEach(async () => {
    getSites.mockResolvedValue(doc())
    await useSitesStore.getState().ensureLoaded('p')
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
  })

  it('refuses every write and never PUTs, even after the debounce window', async () => {
    const s = useSitesStore.getState()
    s.setPlacement('p', 'site_a', 'Generator:G1', { x: 1, y: 1, heading: 0 })
    s.upsertSite('p', site({ id: 'site_b' }))
    s.removeSite('p', 'site_a')
    expect(s.docFor('p')).toEqual(doc())
    await vi.advanceTimersByTimeAsync(SITES_SAVE_DEBOUNCE_MS * 3)
    expect(putSites).not.toHaveBeenCalled()
  })

  it('a debounce armed before the lock does not fire after it', async () => {
    useUIStore.setState({ readOnly: false, readOnlyReason: 'writable' })
    useSitesStore.getState().setPlacement('p', 'site_a', 'Generator:G1', { x: 1, y: 1, heading: 0 })
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
    await vi.advanceTimersByTimeAsync(SITES_SAVE_DEBOUNCE_MS * 3)
    expect(putSites).not.toHaveBeenCalled()
  })
})

describe('lifecycle', () => {
  it('persistSitesOnUnload writes localStorage and fires a keepalive PUT only when dirty', async () => {
    getSites.mockResolvedValue(doc())
    await useSitesStore.getState().ensureLoaded('p')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'))
    persistSitesOnUnload('p')
    expect(fetchSpy).not.toHaveBeenCalled()
    useSitesStore.getState().setPlacement('p', 'site_a', 'Generator:G1', { x: 4, y: 4, heading: 0 })
    persistSitesOnUnload('p')
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/projects/p/sites')
    expect(init.method).toBe('PUT')
    expect(init.keepalive).toBe(true)
    expect(JSON.parse(localStorage.getItem(localSitesKey('p')) ?? 'null').sites[0].placements['Generator:G1'].x).toBe(4)
    fetchSpy.mockRestore()
  })
})
