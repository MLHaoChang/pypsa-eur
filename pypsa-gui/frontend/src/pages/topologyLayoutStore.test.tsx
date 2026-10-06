// The blank canvas saved one change behind: the user moved a bus (or a
// waypoint), hit Save, went to another panel, came back — and the canvas was
// at the state BEFORE that last change. Every save landed at N-1.
//
// Cause: `scheduleSave()` built its payload at call time from refs mirroring
// `nodes` / `edges`, and every call site invokes it in the same handler as the
// `setNodes` / `setEdges` that made the change:
//
//     setEdges(prev => …)   // queued; `edges` still holds the OLD value
//     scheduleSave()        // captured the OLD value
//
// so the payload written to the module cache (what the Save button flushes to
// layout.json, and what a remount re-seeds from) was always the previous
// layout. These tests drive the hook the way the canvas does and assert the
// LATEST change is what gets persisted.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useState } from 'react'
import { render, screen, act, fireEvent } from '@testing-library/react'
import {
  useLayoutPersistence, layoutMemCache, layoutCacheKey, loadDiagramState, saveDiagramState,
  newestLayout, loadLayoutNewestWins, flushPendingLayoutToServer, resetLayoutNotices,
  buildPersistedState, coercePersistedState, storageKeyFor,
  SAVE_DEBOUNCE_MS, STORAGE_VERSION, type LayoutNodeLike, type LayoutEdgeLike, type PersistedState,
} from './topologyLayoutStore'
import { projectsApi } from '../api/projects'
import { useUIStore } from '../store/uiStore'

// The layout of an active project lives in layout.json on the server — the
// localStorage store is only the no-project / offline fallback, and jsdom here
// exposes a stub `localStorage`, so assert against the server write.
vi.mock('../api/projects', () => ({
  projectsApi: {
    putLayout: vi.fn(() => Promise.resolve({})),
    getLayout: vi.fn(() => Promise.resolve({})),
  },
}))

// A failed PUT surfaces once per project (A4); count the toasts.
const { toastFn } = vi.hoisted(() => ({
  toastFn: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), dismiss: vi.fn() }),
}))
vi.mock('react-hot-toast', () => ({ default: toastFn }))

const PROJECT = 'three-bus'

const WP_MOVED = { x: 70, y: 80 }
const BUS_DROPPED = { x: 123, y: 456 }

// Mirrors the canvas: every mutation queues a state update and then asks for a
// save in the SAME handler. Nothing here awaits a render — that is the point.
function Harness() {
  const [nodes, setNodes] = useState<LayoutNodeLike[]>([
    { id: 'bus-A', position: { x: 0, y: 0 } },
    { id: 'assetgrp-bus-A-Thermal', position: { x: 9, y: 9 } },
  ])
  const [edges, setEdges] = useState<LayoutEdgeLike[]>([
    { id: 'line-1', data: { waypoints: [], history: [[]] } },
  ])
  const scheduleSave = useLayoutPersistence(nodes, edges)
  return (
    <>
      <button onClick={() => {
        setEdges(prev => prev.map(e => e.id === 'line-1'
          ? { ...e, data: { waypoints: [WP_MOVED], history: [[], [WP_MOVED]] } }
          : e))
        scheduleSave()
      }}>move waypoint</button>
      <button onClick={() => {
        setNodes(prev => prev.map(n => n.id === 'bus-A'
          ? { ...n, position: BUS_DROPPED }
          : n))
        scheduleSave()
      }}>move bus</button>
      {/* onNodeDragStop's shape: React Flow already committed the position, so
          the handler only asks for a save. */}
      <button onClick={() => scheduleSave()}>save only</button>
      <button onClick={() => scheduleSave({ immediate: true })}>save now</button>
    </>
  )
}

// What the next session reads: the last payload written to layout.json.
const readPersisted = (): PersistedState | null => {
  const calls = vi.mocked(projectsApi.putLayout).mock.calls
  if (calls.length === 0) return null
  return calls[calls.length - 1][1] as unknown as PersistedState
}
// What the Save button flushes and what a remount re-seeds from.
const readCache = (): PersistedState | undefined => layoutMemCache.get(layoutCacheKey(PROJECT))

const wpOf = (s: PersistedState | null | undefined) =>
  s?.edges.find(e => e.id === 'line-1')?.waypoints
const posOf = (s: PersistedState | null | undefined) =>
  s?.nodes.find(n => n.id === 'bus-A')

describe('useLayoutPersistence', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(projectsApi.putLayout).mockClear()
    layoutMemCache.clear()
    useUIStore.setState({ currentProject: PROJECT })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('persists the waypoint the user just moved, not the one before it', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('move waypoint'))
    act(() => { vi.advanceTimersByTime(SAVE_DEBOUNCE_MS) })
    expect(wpOf(readPersisted())).toEqual([WP_MOVED])
  })

  it('persists the position the user just dragged a bus to', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('move bus'))
    act(() => { vi.advanceTimersByTime(SAVE_DEBOUNCE_MS) })
    expect(posOf(readPersisted())).toEqual({ id: 'bus-A', canvasX: 123, canvasY: 456 })
  })

  it('has the last change in the cache BEFORE the debounce fires', () => {
    // This is the reported path: change something, then hit Save. The Save
    // button calls flushPendingLayoutToServer, which reads this cache — so a
    // stale entry here is what shipped the N-1 layout to layout.json.
    render(<Harness />)
    fireEvent.click(screen.getByText('move waypoint'))
    expect(wpOf(readCache())).toEqual([WP_MOVED])
  })

  it('keeps both changes when two land back to back', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('move waypoint'))
    fireEvent.click(screen.getByText('move bus'))
    act(() => { vi.advanceTimersByTime(SAVE_DEBOUNCE_MS) })
    const saved = readPersisted()
    expect(wpOf(saved)).toEqual([WP_MOVED])
    expect(posOf(saved)).toEqual({ id: 'bus-A', canvasX: 123, canvasY: 456 })
  })

  it('persists the committed state when the handler only asks for a save', () => {
    // onNodeDragStop / the non-rename bus Apply path: no state update of our
    // own, so the payload is simply the current layout.
    render(<Harness />)
    fireEvent.click(screen.getByText('move bus'))
    act(() => { vi.advanceTimersByTime(SAVE_DEBOUNCE_MS) })
    vi.mocked(projectsApi.putLayout).mockClear()
    fireEvent.click(screen.getByText('save only'))
    act(() => { vi.advanceTimersByTime(SAVE_DEBOUNCE_MS) })
    expect(posOf(readPersisted())).toEqual({ id: 'bus-A', canvasX: 123, canvasY: 456 })
  })

  it('writes immediately — without the debounce — when asked to', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('move waypoint'))
    fireEvent.click(screen.getByText('save now'))
    // No timer advance: the rename path cannot wait for the debounce.
    expect(wpOf(readPersisted())).toEqual([WP_MOVED])
  })

  it('never persists derived asset-group nodes', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('save only'))
    act(() => { vi.advanceTimersByTime(SAVE_DEBOUNCE_MS) })
    expect(readPersisted()?.nodes.map(n => n.id)).toEqual(['bus-A'])
  })

  it('flushes a pending debounced save on unmount', () => {
    const { unmount } = render(<Harness />)
    fireEvent.click(screen.getByText('move waypoint'))
    vi.mocked(projectsApi.putLayout).mockClear()
    unmount()
    expect(wpOf(readPersisted())).toEqual([WP_MOVED])
  })
})

// ── A2: the persisted shape gains the asset mode and asset-node positions ────
// Version 2. A version-1 document (every layout saved before A2) still reads,
// as `grouped` — the only view it had — and is never thrown away.

const V1_DOC = { version: 1, savedAt: 500, nodes: [{ id: 'bus-A', canvasX: 1, canvasY: 2 }], edges: [] }

describe('PersistedState v2', () => {
  it('STORAGE_VERSION is 2', () => { expect(STORAGE_VERSION).toBe(2) })

  it('reads a version-1 document as grouped at the current version, keeping its positions', () => {
    const s = coercePersistedState(V1_DOC)
    expect(s).toMatchObject({ version: 2, assetMode: 'grouped', savedAt: 500 })
    expect(s?.nodes).toEqual(V1_DOC.nodes)
  })

  it('reads a version-1 document from localStorage too, without removing it', () => {
    localStorage.setItem(storageKeyFor(PROJECT), JSON.stringify(V1_DOC))
    expect(loadDiagramState(PROJECT)).toMatchObject({ version: 2, assetMode: 'grouped' })
    expect(localStorage.getItem(storageKeyFor(PROJECT))).not.toBeNull()
  })

  it('passes a version-2 document through, drops an unknown assetMode, and rejects a future version', () => {
    const v2 = { ...V1_DOC, version: 2, assetMode: 'individual', nodes: [...V1_DOC.nodes, { id: 'asset-Generator:PV', canvasX: 3, canvasY: 4 }] }
    expect(coercePersistedState(v2)).toEqual(v2)
    expect(coercePersistedState({ ...V1_DOC, version: 2 })?.assetMode).toBeUndefined()
    expect(coercePersistedState({ ...V1_DOC, version: 2, assetMode: 'weird' })?.assetMode).toBeUndefined()
    expect(coercePersistedState({ ...V1_DOC, version: 3 })).toBeNull()
  })

  it('buildPersistedState writes the mode and the hidden asset-node positions, deduplicated against live nodes', () => {
    const live: LayoutNodeLike[] = [
      { id: 'bus-A', position: { x: 0, y: 0 } },
      { id: 'asset-Generator:PV', position: { x: 10, y: 20 } },
      { id: 'assetgrp-bus-A-Load', position: { x: 9, y: 9 } },
    ]
    const s = buildPersistedState(live, [], {
      assetMode: 'individual',
      assetNodes: [
        { id: 'asset-Generator:PV', canvasX: 99, canvasY: 99 },   // live wins
        { id: 'asset-Load:DataHall', canvasX: 30, canvasY: 40 },  // hidden (grouped mode) — kept
      ],
    })
    expect(s.version).toBe(2)
    expect(s.assetMode).toBe('individual')
    expect(s.nodes).toEqual([
      { id: 'bus-A', canvasX: 0, canvasY: 0 },
      { id: 'asset-Generator:PV', canvasX: 10, canvasY: 20 },
      { id: 'asset-Load:DataHall', canvasX: 30, canvasY: 40 },
    ])
    // no choice made: the key is absent so the default rule decides on load
    expect('assetMode' in buildPersistedState(live, [])).toBe(false)
  })

  it('the hook persists the extras read at save time', () => {
    function Harness2() {
      const [nodes] = useState<LayoutNodeLike[]>([{ id: 'bus-A', position: { x: 0, y: 0 } }])
      const [mode, setMode] = useState<'grouped' | 'individual'>('grouped')
      const scheduleSave = useLayoutPersistence(nodes, [], () => ({
        assetMode: mode, assetNodes: [{ id: 'asset-Store:S', canvasX: 5, canvasY: 6 }],
      }))
      return <button onClick={() => { setMode('individual'); scheduleSave() }}>individual</button>
    }
    vi.useFakeTimers()
    vi.mocked(projectsApi.putLayout).mockClear()
    useUIStore.setState({ currentProject: PROJECT })
    render(<Harness2 />)
    fireEvent.click(screen.getByText('individual'))
    act(() => { vi.advanceTimersByTime(SAVE_DEBOUNCE_MS) })
    const saved = readPersisted()
    expect(saved?.assetMode).toBe('individual')
    expect(saved?.nodes).toEqual([{ id: 'bus-A', canvasX: 0, canvasY: 0 }, { id: 'asset-Store:S', canvasX: 5, canvasY: 6 }])
    vi.useRealTimers()
  })
})

// ── A4: positions never revert (OPEN-ITEMS 7) ────────────────────────────────
// Finding 2026-07-31: `PUT /layout` 404s until the project directory exists,
// and on load the server won unconditionally over anything newer held locally
// — so a drag whose PUT failed was discarded on the next load for an older
// layout.json, silently at both ends. `savedAt` was on every copy and nothing
// compared it.

const layoutAt = (savedAt: number, x: number, y: number): PersistedState => ({
  version: STORAGE_VERSION, savedAt,
  nodes: [{ id: 'bus-A', canvasX: x, canvasY: y }], edges: [],
})
const http = (status: number) => Object.assign(new Error(`HTTP ${status}`), { response: { status } })
const putCalls = () => vi.mocked(projectsApi.putLayout).mock.calls
// What `GET /layout` answers (the wire type is the opaque document).
const serverHolds = (s: PersistedState) =>
  vi.mocked(projectsApi.getLayout).mockResolvedValue(s as unknown as Record<string, unknown>)

describe('newestLayout', () => {
  const server = layoutAt(1000, 1, 1)
  const memory = layoutAt(2000, 2, 2)
  const local  = layoutAt(3000, 3, 3)

  it.each([
    ['server', [layoutAt(9000, 9, 9), memory, local], 9],
    ['memory', [server, layoutAt(9000, 9, 9), local], 9],
    ['localStorage', [server, memory, layoutAt(9000, 9, 9)], 9],
  ] as const)('picks the newest by savedAt when %s is newest', (_which, candidates, x) => {
    expect(newestLayout(...candidates)?.nodes[0].canvasX).toBe(x)
  })

  it('on a tie keeps the earliest argument — the server copy needs no push', () => {
    const a = layoutAt(1000, 1, 1)
    const b = layoutAt(1000, 2, 2)
    expect(newestLayout(a, b)).toBe(a)
  })

  it('treats a missing savedAt as oldest and skips nulls', () => {
    const undated = { ...layoutAt(0, 7, 7), savedAt: undefined } as unknown as PersistedState
    expect(newestLayout(null, undefined, undated, server)).toBe(server)
    expect(newestLayout(null, undefined)).toBeNull()
  })
})

describe('loadLayoutNewestWins', () => {
  beforeEach(() => {
    vi.mocked(projectsApi.putLayout).mockReset().mockResolvedValue({ saved: PROJECT })
    vi.mocked(projectsApi.getLayout).mockReset().mockResolvedValue({})
    layoutMemCache.clear()
    localStorage.clear()
    resetLayoutNotices()
    toastFn.mockClear(); toastFn.error.mockClear()
  })

  it('the server copy wins when it is newest, and nothing is pushed', async () => {
    serverHolds(layoutAt(3000, 3, 3))
    layoutMemCache.set(layoutCacheKey(PROJECT), layoutAt(2000, 2, 2))
    saveDiagramState(PROJECT, layoutAt(1000, 1, 1))
    const got = await loadLayoutNewestWins(PROJECT)
    expect(got?.nodes[0].canvasX).toBe(3)
    expect(putCalls()).toHaveLength(0)
    expect(layoutMemCache.get(layoutCacheKey(PROJECT))).toBe(got)
  })

  it('a newer memory-cache copy is adopted and pushed with one PUT', async () => {
    serverHolds(layoutAt(1000, 1, 1))
    const dragged = layoutAt(2000, 123, 456)
    layoutMemCache.set(layoutCacheKey(PROJECT), dragged)
    const got = await loadLayoutNewestWins(PROJECT)
    expect(got).toBe(dragged)
    expect(putCalls()).toHaveLength(1)
    expect(putCalls()[0][1]).toBe(dragged)
  })

  it('REGRESSION (finding 2026-07-31): a drag whose PUT failed survives the next load', async () => {
    // layout.json still holds the pre-drag positions …
    serverHolds(layoutAt(1000, 0, 0))
    // … and the failed PUT left the dragged layout in localStorage, third in
    // the old `ps ?? mem ?? local` chain and therefore discarded.
    saveDiagramState(PROJECT, layoutAt(2000, 123, 456))
    const got = await loadLayoutNewestWins(PROJECT)
    expect(got?.nodes[0]).toEqual({ id: 'bus-A', canvasX: 123, canvasY: 456 })
    // and the server is brought up to date, once
    expect(putCalls()).toHaveLength(1)
    expect((putCalls()[0][1] as unknown as PersistedState).nodes[0].canvasX).toBe(123)
  })

  it('with no server copy, a local copy is shown and pushed; with nothing anywhere, null', async () => {
    saveDiagramState(PROJECT, layoutAt(2000, 5, 5))
    expect((await loadLayoutNewestWins(PROJECT))?.nodes[0].canvasX).toBe(5)
    expect(putCalls()).toHaveLength(1)
    localStorage.clear(); layoutMemCache.clear()
    vi.mocked(projectsApi.putLayout).mockClear()
    expect(await loadLayoutNewestWins(PROJECT)).toBeNull()
    expect(putCalls()).toHaveLength(0)
  })

  it('with no project there is no server copy to ask for and nothing to push', async () => {
    saveDiagramState(null, layoutAt(2000, 8, 8))
    expect((await loadLayoutNewestWins(null))?.nodes[0].canvasX).toBe(8)
    expect(projectsApi.getLayout).not.toHaveBeenCalled()
    expect(putCalls()).toHaveLength(0)
  })
})

describe('a failed layout PUT', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(projectsApi.putLayout).mockReset()
    layoutMemCache.clear()
    localStorage.clear()
    resetLayoutNotices()
    toastFn.mockClear(); toastFn.error.mockClear()
    useUIStore.setState({ currentProject: PROJECT })
  })
  afterEach(() => { vi.useRealTimers() })

  const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS) }) }

  it('404 before the first save: one toast per project, the layout waits in the cache, and the save flushes it to the server', async () => {
    vi.mocked(projectsApi.putLayout).mockRejectedValue(http(404))
    render(<Harness />)
    fireEvent.click(screen.getByText('move bus'))
    await settle()
    expect(toastFn).toHaveBeenCalledTimes(1)
    expect(toastFn.mock.calls[0][0]).toBe('Layout will be saved with the project')
    expect(toastFn.error).not.toHaveBeenCalled()
    // a second drag before the save is not a second toast
    fireEvent.click(screen.getByText('move waypoint'))
    await settle()
    expect(toastFn).toHaveBeenCalledTimes(1)
    // the dragged layout is still what the save paths will flush
    expect(posOf(readCache())).toEqual({ id: 'bus-A', canvasX: 123, canvasY: 456 })
    expect(wpOf(readCache())).toEqual([WP_MOVED])
    // … the project is saved (directory now exists) and the save flow flushes
    vi.mocked(projectsApi.putLayout).mockReset().mockResolvedValue({ saved: PROJECT })
    const r = await flushPendingLayoutToServer(PROJECT)
    expect(r.status).toBe('server')
    expect(posOf(readPersisted())).toEqual({ id: 'bus-A', canvasX: 123, canvasY: 456 })
    expect(wpOf(readPersisted())).toEqual([WP_MOVED])
  })

  it('any other failure surfaces once as an error and keeps the layout locally', async () => {
    vi.mocked(projectsApi.putLayout).mockRejectedValue(http(409))
    render(<Harness />)
    fireEvent.click(screen.getByText('move bus'))
    await settle()
    fireEvent.click(screen.getByText('move waypoint'))
    await settle()
    expect(toastFn.error).toHaveBeenCalledTimes(1)
    expect(String(toastFn.error.mock.calls[0][0])).toContain('HTTP 409')
    expect(toastFn).not.toHaveBeenCalled()
    expect(posOf(loadDiagramState(PROJECT))).toEqual({ id: 'bus-A', canvasX: 123, canvasY: 456 })
  })

  it('a later successful write re-arms the notice, so a new failure is reported again', async () => {
    vi.mocked(projectsApi.putLayout).mockRejectedValue(http(404))
    render(<Harness />)
    fireEvent.click(screen.getByText('move bus'))
    await settle()
    expect(toastFn).toHaveBeenCalledTimes(1)
    vi.mocked(projectsApi.putLayout).mockResolvedValue({ saved: PROJECT })
    fireEvent.click(screen.getByText('move waypoint'))
    await settle()
    vi.mocked(projectsApi.putLayout).mockRejectedValue(http(404))
    fireEvent.click(screen.getByText('move bus'))
    await settle()
    expect(toastFn).toHaveBeenCalledTimes(2)
  })
})
