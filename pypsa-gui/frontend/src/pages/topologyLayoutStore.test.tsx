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
  useLayoutPersistence, layoutMemCache, layoutCacheKey,
  SAVE_DEBOUNCE_MS, type LayoutNodeLike, type LayoutEdgeLike, type PersistedState,
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
