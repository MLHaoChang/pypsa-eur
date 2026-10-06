// The map layout store: memory-first, debounced PUT, flush on demand,
// localStorage only as a fallback for a failed PUT, every write refused while
// the project is read-only, the lat/lng ↔ lng/lat boundary, and the one-time
// migration of the pre-M1 localStorage keys.
//
// Plan: docs/superpowers/plans/2026-10-06-visual-layers-2-map-view.md, M1.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/mapLayout', async importOriginal => {
  const real = await importOriginal<typeof import('../api/mapLayout')>()
  return { ...real, mapLayoutApi: { getMapLayout: vi.fn(), putMapLayout: vi.fn() } }
})

import { emptyMapLayoutDocument, mapLayoutApi, type MapLayoutDocument } from '../api/mapLayout'
import { useUIStore } from '../store/uiStore'
import {
  MAP_LAYOUT_SAVE_DEBOUNCE_MS,
  bubbleKey,
  buildLegacyDocument,
  flushPendingMapLayoutToServer,
  legacyOffsetsKey,
  legacyWaypointsKey,
  localMapLayoutKey,
  persistMapLayoutOnUnload,
  routeWaypoints,
  toLatLng,
  toLngLat,
  useMapLayoutStore,
} from './mapLayoutStore'

const getMapLayout = vi.mocked(mapLayoutApi.getMapLayout)
const putMapLayout = vi.mocked(mapLayoutApi.putMapLayout)

const doc = (over: Partial<MapLayoutDocument> = {}): MapLayoutDocument => ({
  version: 1,
  routes: { 'line:L1': { points: [[6.83, 53.44]], source: 'user' } },
  bubbles: { 'B1|Thermal': { dx: -110, dy: -70 } },
  ...over,
})

beforeEach(() => {
  vi.useFakeTimers()
  useMapLayoutStore.getState().resetForTests()
  useUIStore.setState({ readOnly: false, readOnlyReason: 'writable' })
  getMapLayout.mockReset()
  putMapLayout.mockReset()
  putMapLayout.mockResolvedValue({ saved: 'p', routes: 1, bubbles: 1 })
})
afterEach(() => {
  vi.useRealTimers()
})

describe('coordinate order', () => {
  it('converts the map\'s [lat, lng] to the document\'s [lng, lat] and back', () => {
    expect(toLngLat([[53.44, 6.83], [53.45, 6.84]])).toEqual([[6.83, 53.44], [6.84, 53.45]])
    expect(toLatLng([[6.83, 53.44]])).toEqual([[53.44, 6.83]])
    expect(toLatLng(toLngLat([[1, 2], [3, 4]]))).toEqual([[1, 2], [3, 4]])
  })

  it('routeWaypoints hands the map its table in [lat, lng]', () => {
    expect(routeWaypoints(doc())).toEqual({ 'line:L1': [[53.44, 6.83]] })
  })

  it('setRouteWaypoints stores the document in [lng, lat]', async () => {
    getMapLayout.mockResolvedValue(emptyMapLayoutDocument())
    await useMapLayoutStore.getState().ensureLoaded('p')
    useMapLayoutStore.getState().setRouteWaypoints('p', 'link:HVDC', [[53.5, 6.9], [53.6, 7.0]])
    const route = useMapLayoutStore.getState().docFor('p').routes['link:HVDC']
    expect(route).toEqual({ points: [[6.9, 53.5], [7.0, 53.6]], source: 'user' })
    await vi.advanceTimersByTimeAsync(MAP_LAYOUT_SAVE_DEBOUNCE_MS + 1)
    expect(putMapLayout.mock.calls[0][1].routes['link:HVDC'].points).toEqual([[6.9, 53.5], [7.0, 53.6]])
  })
})

describe('loading', () => {
  it('loads once per project and caches', async () => {
    getMapLayout.mockResolvedValue(doc())
    await useMapLayoutStore.getState().ensureLoaded('p')
    await useMapLayoutStore.getState().ensureLoaded('p')
    expect(getMapLayout).toHaveBeenCalledTimes(1)
    expect(useMapLayoutStore.getState().docFor('p')).toEqual(doc())
  })

  it('falls back to localStorage only when the GET throws, never when the server says empty', async () => {
    localStorage.setItem(localMapLayoutKey('p'), JSON.stringify(doc({ bubbles: { 'B9|Load': { dx: 1, dy: 1 } } })))
    getMapLayout.mockResolvedValue(emptyMapLayoutDocument())
    await useMapLayoutStore.getState().ensureLoaded('p')
    expect(useMapLayoutStore.getState().docFor('p')).toEqual(emptyMapLayoutDocument())

    useMapLayoutStore.getState().resetForTests()
    getMapLayout.mockRejectedValue(new Error('offline'))
    await useMapLayoutStore.getState().ensureLoaded('p')
    expect(useMapLayoutStore.getState().docFor('p').bubbles).toEqual({ 'B9|Load': { dx: 1, dy: 1 } })
  })

  it('docFor returns one stable empty document for an unknown project', () => {
    const a = useMapLayoutStore.getState().docFor('nope')
    expect(a).toBe(useMapLayoutStore.getState().docFor('nope'))
    expect(a).toEqual(emptyMapLayoutDocument())
  })
})

describe('writes', () => {
  beforeEach(async () => {
    getMapLayout.mockResolvedValue(doc())
    await useMapLayoutStore.getState().ensureLoaded('p')
  })

  it('setBubble writes the store synchronously and PUTs after the debounce', async () => {
    useMapLayoutStore.getState().setBubble('p', bubbleKey('B1', 'Storage'), { dx: 10, dy: 20 })
    expect(useMapLayoutStore.getState().docFor('p').bubbles['B1|Storage']).toEqual({ dx: 10, dy: 20 })
    expect(putMapLayout).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(MAP_LAYOUT_SAVE_DEBOUNCE_MS + 1)
    expect(putMapLayout).toHaveBeenCalledTimes(1)
    expect(putMapLayout.mock.calls[0][0]).toBe('p')
    expect(putMapLayout.mock.calls[0][1].bubbles['B1|Storage']).toEqual({ dx: 10, dy: 20 })
  })

  it('three edits inside the window produce one PUT with the last document', async () => {
    const s = useMapLayoutStore.getState()
    s.setRouteWaypoints('p', 'line:L1', [[1, 1]])
    s.setRouteWaypoints('p', 'line:L1', [[2, 2]])
    s.setRouteWaypoints('p', 'line:L1', [[3, 3]])
    await vi.advanceTimersByTimeAsync(MAP_LAYOUT_SAVE_DEBOUNCE_MS + 1)
    expect(putMapLayout).toHaveBeenCalledTimes(1)
    expect(putMapLayout.mock.calls[0][1].routes['line:L1'].points).toEqual([[3, 3]])
  })

  it('an empty waypoint list removes the route; other routes and the bubbles stay', () => {
    const s = useMapLayoutStore.getState()
    s.setRouteWaypoints('p', 'tr:T1', [[0, 0]])
    s.setRouteWaypoints('p', 'line:L1', [])
    const d = s.docFor('p')
    expect(Object.keys(d.routes)).toEqual(['tr:T1'])
    expect(d.bubbles).toEqual(doc().bubbles)
  })

  it('a rewrite keeps the route\'s source unless told otherwise', () => {
    const s = useMapLayoutStore.getState()
    s.setRouteWaypoints('p', 'line:L1', [[1, 1]], 'import')
    s.setRouteWaypoints('p', 'line:L1', [[2, 2]])
    expect(s.docFor('p').routes['line:L1'].source).toBe('user')
  })

  it('rejects what the backend would 422', () => {
    const s = useMapLayoutStore.getState()
    expect(() => s.setRouteWaypoints('p', 'L1', [[1, 1]])).toThrow(/route id/)
    expect(() => s.setRouteWaypoints('p', 'bus:B1', [[1, 1]])).toThrow(/route id/)
    expect(() => s.setRouteWaypoints('p', 'line:L1', [[91, 0]])).toThrow(/out of range/)
    expect(() => s.setRouteWaypoints('p', 'line:L1', [[Number.NaN, 0]])).toThrow(/waypoint/)
    expect(() => s.setBubble('p', 'B1', { dx: 0, dy: 0 })).toThrow(/bubble key/)
    expect(() => s.setBubble('p', 'B1|Thermal', { dx: Number.POSITIVE_INFINITY, dy: 0 })).toThrow(/finite/)
    expect(s.docFor('p')).toEqual(doc())
  })

  it('flushPendingMapLayoutToServer PUTs immediately and reports server', async () => {
    useMapLayoutStore.getState().setRouteWaypoints('p', 'line:L1', [[5, 5]])
    const r = await flushPendingMapLayoutToServer('p')
    expect(r).toEqual({ status: 'server', routes: 1, bubbles: 1 })
    expect(putMapLayout).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(MAP_LAYOUT_SAVE_DEBOUNCE_MS + 1)
    expect(putMapLayout).toHaveBeenCalledTimes(1) // the debounce was cancelled by the flush
  })

  it('a flush with nothing pending reports nothing', async () => {
    expect(await flushPendingMapLayoutToServer('p')).toEqual({ status: 'nothing', routes: 0, bubbles: 0 })
    expect(putMapLayout).not.toHaveBeenCalled()
  })

  it('after a Save-As the new name gets a copy and the original still receives its own write', async () => {
    useMapLayoutStore.getState().setRouteWaypoints('p', 'line:L1', [[7, 7]])
    const r = await flushPendingMapLayoutToServer('p-copy', { previousProject: 'p' })
    expect(r.status).toBe('server')
    expect(putMapLayout.mock.calls[0][0]).toBe('p-copy')
    expect(putMapLayout.mock.calls[0][1].routes['line:L1'].points).toEqual([[7, 7]])
    expect(useMapLayoutStore.getState().docFor('p-copy').routes['line:L1'].points).toEqual([[7, 7]])
    await vi.advanceTimersByTimeAsync(MAP_LAYOUT_SAVE_DEBOUNCE_MS + 1)
    expect(putMapLayout.mock.calls.map(c => c[0]).sort()).toEqual(['p', 'p-copy'])
  })

  it('a scratch network\'s document MOVES to the first-saved project and its local slot is cleared', async () => {
    useMapLayoutStore.getState().resetForTests()
    useMapLayoutStore.getState().setRouteWaypoints(null, 'line:L1', [[1, 1]])
    await vi.advanceTimersByTimeAsync(MAP_LAYOUT_SAVE_DEBOUNCE_MS + 1)
    expect(localStorage.getItem(localMapLayoutKey(null))).not.toBeNull()
    const r = await flushPendingMapLayoutToServer('fresh', { previousProject: null })
    expect(r.status).toBe('server')
    expect(putMapLayout.mock.calls[0][0]).toBe('fresh')
    expect(localStorage.getItem(localMapLayoutKey(null))).toBeNull()
    await useMapLayoutStore.getState().ensureLoaded(null)
    expect(useMapLayoutStore.getState().docFor(null)).toEqual(emptyMapLayoutDocument())
  })

  it('a write that lands while the initial GET is in flight is not clobbered by it', async () => {
    useMapLayoutStore.getState().resetForTests()
    let resolveGet: (d: MapLayoutDocument) => void = () => {}
    getMapLayout.mockReturnValue(new Promise<MapLayoutDocument>(res => { resolveGet = res }))
    const loading = useMapLayoutStore.getState().ensureLoaded('p')
    useMapLayoutStore.getState().setRouteWaypoints('p', 'tr:first', [[1, 1]])
    resolveGet(doc())
    await loading
    expect(Object.keys(useMapLayoutStore.getState().docFor('p').routes)).toEqual(['tr:first'])
  })

  it('a failed PUT writes localStorage and a later success clears it', async () => {
    putMapLayout.mockRejectedValueOnce(new Error('offline'))
    useMapLayoutStore.getState().setRouteWaypoints('p', 'line:L1', [[9, 9]])
    await vi.advanceTimersByTimeAsync(MAP_LAYOUT_SAVE_DEBOUNCE_MS + 1)
    expect(putMapLayout).toHaveBeenCalledTimes(1)
    const stored = JSON.parse(localStorage.getItem(localMapLayoutKey('p')) ?? 'null') as MapLayoutDocument
    expect(stored.routes['line:L1'].points).toEqual([[9, 9]])

    const r = await flushPendingMapLayoutToServer('p')
    expect(r.status).toBe('server')
    expect(localStorage.getItem(localMapLayoutKey('p'))).toBeNull()
  })

  it('renameRoute re-keys one kind only', () => {
    const s = useMapLayoutStore.getState()
    s.setRouteWaypoints('p', 'link:X', [[1, 1]])
    s.setRouteWaypoints('p', 'line:X', [[2, 2]])
    expect(s.renameRoute('p', 'Link', 'X', 'Y')).toBe(true)
    expect(Object.keys(s.docFor('p').routes).sort()).toEqual(['line:L1', 'line:X', 'link:Y'])
    expect(s.renameRoute('p', 'Generator', 'X', 'Y')).toBe(false)
    expect(s.renameRoute('p', 'Transformer', 'nope', 'Y')).toBe(false)
  })

  it('subscribers see every write', () => {
    const seen: number[] = []
    const unsub = useMapLayoutStore.subscribe(st => seen.push(Object.keys(st.docFor('p').routes).length))
    useMapLayoutStore.getState().setRouteWaypoints('p', 'link:A', [[0, 0]])
    useMapLayoutStore.getState().setRouteWaypoints('p', 'link:B', [[0, 0]])
    unsub()
    expect(seen).toEqual([2, 3])
  })
})

describe('read-only', () => {
  beforeEach(async () => {
    getMapLayout.mockResolvedValue(doc())
    await useMapLayoutStore.getState().ensureLoaded('p')
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
  })

  it('refuses every write and never PUTs, even after the debounce window', async () => {
    const s = useMapLayoutStore.getState()
    s.setRouteWaypoints('p', 'line:L1', [[1, 1]])
    s.setBubble('p', 'B1|Load', { dx: 1, dy: 1 })
    s.renameRoute('p', 'Line', 'L1', 'L2')
    expect(s.docFor('p')).toEqual(doc())
    await vi.advanceTimersByTimeAsync(MAP_LAYOUT_SAVE_DEBOUNCE_MS * 3)
    expect(putMapLayout).not.toHaveBeenCalled()
  })

  it('a debounce armed before the lock does not fire after it', async () => {
    useUIStore.setState({ readOnly: false, readOnlyReason: 'writable' })
    useMapLayoutStore.getState().setRouteWaypoints('p', 'line:L1', [[1, 1]])
    useUIStore.setState({ readOnly: true, readOnlyReason: 'solving' })
    await vi.advanceTimersByTimeAsync(MAP_LAYOUT_SAVE_DEBOUNCE_MS * 3)
    expect(putMapLayout).not.toHaveBeenCalled()
    expect(await flushPendingMapLayoutToServer('p')).toMatchObject({ status: 'refused' })
  })
})

describe('migration of the pre-M1 localStorage keys', () => {
  const seedLegacy = (project: string | null) => {
    // The map stored Leaflet [lat, lng] tuples keyed by edge id …
    localStorage.setItem(legacyWaypointsKey(project), JSON.stringify({
      'line:L1': [[53.44, 6.83], [53.45, 6.84]],
      'tr:T1': [[53.5, 6.9]],
      'link:empty': [],                 // a cleared route: nothing to carry
      'bus:B1': [[1, 1]],               // never a valid id: dropped, not fatal
    }))
    // … and bubble offsets keyed `<bus>::<category>`.
    localStorage.setItem(legacyOffsetsKey(project), JSON.stringify({
      'B1::Thermal': { dx: -110, dy: -70 },
      'B1::Storage': { dx: 'x', dy: 0 },  // corrupt: dropped, not fatal
    }))
  }
  const expectedDoc: MapLayoutDocument = {
    version: 1,
    routes: {
      'line:L1': { points: [[6.83, 53.44], [6.84, 53.45]], source: 'user' },
      'tr:T1': { points: [[6.9, 53.5]], source: 'user' },
    },
    bubbles: { 'B1|Thermal': { dx: -110, dy: -70 } },
  }

  it('buildLegacyDocument converts both keys, both orders, and skips the unusable', () => {
    seedLegacy('p')
    expect(buildLegacyDocument('p')).toEqual(expectedDoc)
    expect(buildLegacyDocument('other')).toBeNull()
  })

  it('on first load of a project whose server document is empty, PUTs the legacy state and removes the keys on success', async () => {
    seedLegacy('p')
    getMapLayout.mockResolvedValue(emptyMapLayoutDocument())
    await useMapLayoutStore.getState().ensureLoaded('p')
    expect(useMapLayoutStore.getState().docFor('p')).toEqual(expectedDoc)
    expect(putMapLayout).toHaveBeenCalledTimes(1)
    expect(putMapLayout.mock.calls[0]).toEqual(['p', expectedDoc])
    expect(localStorage.getItem(legacyWaypointsKey('p'))).toBeNull()
    expect(localStorage.getItem(legacyOffsetsKey('p'))).toBeNull()
    expect(useMapLayoutStore.getState().dirty['p']).toBeUndefined()
  })

  it('keeps the legacy keys when the PUT fails, and retries on the next flush', async () => {
    seedLegacy('p')
    getMapLayout.mockResolvedValue(emptyMapLayoutDocument())
    putMapLayout.mockRejectedValueOnce(new Error('offline'))
    await useMapLayoutStore.getState().ensureLoaded('p')
    expect(putMapLayout).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem(legacyWaypointsKey('p'))).not.toBeNull()
    expect(localStorage.getItem(legacyOffsetsKey('p'))).not.toBeNull()
    // The document is dirty, so the next save path flush carries it.
    expect((await flushPendingMapLayoutToServer('p')).status).toBe('server')
    expect(putMapLayout).toHaveBeenCalledTimes(2)
  })

  it('never migrates over a real server document', async () => {
    seedLegacy('p')
    getMapLayout.mockResolvedValue(doc({ routes: { 'line:server': { points: [[1, 1]], source: 'import' } } }))
    await useMapLayoutStore.getState().ensureLoaded('p')
    expect(Object.keys(useMapLayoutStore.getState().docFor('p').routes)).toEqual(['line:server'])
    expect(putMapLayout).not.toHaveBeenCalled()
    expect(localStorage.getItem(legacyWaypointsKey('p'))).not.toBeNull()
  })

  it('does not migrate when the server could not be reached', async () => {
    seedLegacy('p')
    getMapLayout.mockRejectedValue(new Error('offline'))
    await useMapLayoutStore.getState().ensureLoaded('p')
    expect(putMapLayout).not.toHaveBeenCalled()
    expect(localStorage.getItem(legacyWaypointsKey('p'))).not.toBeNull()
  })

  it('shows the legacy state but keeps the keys while the project is read-only', async () => {
    seedLegacy('p')
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
    getMapLayout.mockResolvedValue(emptyMapLayoutDocument())
    await useMapLayoutStore.getState().ensureLoaded('p')
    expect(useMapLayoutStore.getState().docFor('p')).toEqual(expectedDoc)
    expect(putMapLayout).not.toHaveBeenCalled()
    expect(localStorage.getItem(legacyWaypointsKey('p'))).not.toBeNull()
  })

  it('a scratch network migrates its `default` slot into the local fallback slot', async () => {
    seedLegacy(null)
    await useMapLayoutStore.getState().ensureLoaded(null)
    expect(useMapLayoutStore.getState().docFor(null)).toEqual(expectedDoc)
    expect(JSON.parse(localStorage.getItem(localMapLayoutKey(null)) ?? 'null')).toEqual(expectedDoc)
    expect(localStorage.getItem(legacyWaypointsKey(null))).toBeNull()
    expect(localStorage.getItem(legacyOffsetsKey(null))).toBeNull()
    expect(putMapLayout).not.toHaveBeenCalled()
  })
})

describe('lifecycle', () => {
  it('persistMapLayoutOnUnload writes localStorage and fires a keepalive PUT only when dirty', async () => {
    getMapLayout.mockResolvedValue(doc())
    await useMapLayoutStore.getState().ensureLoaded('p')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'))
    persistMapLayoutOnUnload('p')
    expect(fetchSpy).not.toHaveBeenCalled()
    useMapLayoutStore.getState().setRouteWaypoints('p', 'line:L1', [[4, 4]])
    persistMapLayoutOnUnload('p')
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/projects/p/map_layout')
    expect(init.method).toBe('PUT')
    expect(init.keepalive).toBe(true)
    expect(JSON.parse(localStorage.getItem(localMapLayoutKey('p')) ?? 'null').routes['line:L1'].points).toEqual([[4, 4]])
    fetchSpy.mockRestore()
  })
})
