// Deleting a line or link is deferred for 5 s so the toast's Undo button has
// something to undo — the DELETE has not reached the backend yet. That made
// Save inside the window write a network.nc that still contained the line: the
// canvas showed it gone, the file disagreed, and reopening the project brought
// it back. The save flows now drain this registry first, so Save persists what
// the user sees.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  registerPendingEdgeDelete, cancelPendingEdgeDelete, pendingEdgeDeleteCount,
  flushPendingEdgeDeletes, drainPendingEdgeDeletes, drainPendingEdgeDeletesForUnload,
  keepaliveFlushPendingEdgeDeletes,
} from './pendingEdgeDeletes'
import { useUIStore } from '../store/uiStore'
import { useSimulationStore } from '../store/simulationStore'

const UNDO_MS = 5000

// Stand-in for the canvas's deferred delete: a timer that fires `commit` when
// the undo window lapses.
function defer(edgeId: string, commit: () => Promise<void>) {
  const timer = setTimeout(() => { cancelPendingEdgeDelete(edgeId); void commit() }, UNDO_MS)
  registerPendingEdgeDelete({ edgeId, timer, commit })
}

describe('pending edge deletes', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    drainPendingEdgeDeletes()
  })
  afterEach(() => { vi.useRealTimers() })

  it('flushes a delete the undo window has not yet committed', async () => {
    const commit = vi.fn(async () => {})
    defer('line-L1', commit)
    expect(pendingEdgeDeleteCount()).toBe(1)

    const res = await flushPendingEdgeDeletes()

    expect(commit).toHaveBeenCalledTimes(1)
    expect(res).toEqual({ flushed: 1, failed: 0 })
    expect(pendingEdgeDeleteCount()).toBe(0)
  })

  it('does not let the timer fire a second delete after a flush', async () => {
    const commit = vi.fn(async () => {})
    defer('line-L1', commit)
    await flushPendingEdgeDeletes()
    vi.advanceTimersByTime(UNDO_MS * 2)
    expect(commit).toHaveBeenCalledTimes(1)
  })

  it('leaves nothing to flush once Undo has cancelled the delete', async () => {
    const commit = vi.fn(async () => {})
    defer('line-L1', commit)
    cancelPendingEdgeDelete('line-L1')
    expect(pendingEdgeDeleteCount()).toBe(0)

    const res = await flushPendingEdgeDeletes()
    vi.advanceTimersByTime(UNDO_MS * 2)

    expect(commit).not.toHaveBeenCalled()
    expect(res).toEqual({ flushed: 0, failed: 0 })
  })

  it('is a no-op when nothing is pending', async () => {
    expect(await flushPendingEdgeDeletes()).toEqual({ flushed: 0, failed: 0 })
  })

  it('flushes every pending delete, counting the ones that fail', async () => {
    const ok1 = vi.fn(async () => {})
    const ok2 = vi.fn(async () => {})
    const bad = vi.fn(async () => { throw new Error('backend said no') })
    defer('line-L1', ok1)
    defer('link-K1', bad)
    defer('line-L2', ok2)

    const res = await flushPendingEdgeDeletes()

    // One failure must not strand the others — Save is about to serialise.
    expect(ok1).toHaveBeenCalledTimes(1)
    expect(ok2).toHaveBeenCalledTimes(1)
    expect(bad).toHaveBeenCalledTimes(1)
    expect(res).toEqual({ flushed: 2, failed: 1 })
    expect(pendingEdgeDeleteCount()).toBe(0)
  })

  it('re-registering the same edge replaces the earlier entry', async () => {
    const first = vi.fn(async () => {})
    const second = vi.fn(async () => {})
    defer('line-L1', first)
    defer('line-L1', second)
    expect(pendingEdgeDeleteCount()).toBe(1)

    await flushPendingEdgeDeletes()
    expect(second).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(UNDO_MS * 2)
    // The superseded entry's timer must be dead too, or it fires a delete for
    // an edge the registry no longer knows about.
    expect(first).not.toHaveBeenCalled()
  })

  it('hands drained entries to the caller with their edge ids', () => {
    defer('line-L1', async () => {})
    defer('link-K1', async () => {})
    const drained = drainPendingEdgeDeletes()
    expect(drained.map(e => e.edgeId).sort()).toEqual(['line-L1', 'link-K1'])
    expect(pendingEdgeDeleteCount()).toBe(0)
  })

  // A2 (deferred spec 2026-09-28 §2.1, "Writes that bypass axios"): while the
  // tab and the backend disagree about the open project, a pending delete
  // would remove a same-named line from the OTHER project's live network. The
  // unload path is a raw keepalive fetch (no axios interceptor) and the save
  // path's flush commits it: both drop it with one WARN instead.
  describe('while the tab and the backend disagree (A2)', () => {
    const warns = () => useSimulationStore.getState().logLines.filter(l => / WARN /.test(l))
    beforeEach(() => {
      useSimulationStore.setState({ logLines: [] })
      useUIStore.setState({ projectMismatch: { tab: 'X', backend: 'Y' } })
    })
    afterEach(() => { useUIStore.setState({ projectMismatch: null }) })

    it('the save path\'s flush drops the deletes with one WARN and commits nothing', async () => {
      const commit = vi.fn(async () => {})
      defer('line-L1', commit)
      defer('link-K1', commit)
      const res = await flushPendingEdgeDeletes()
      expect(commit).not.toHaveBeenCalled()
      expect(res).toEqual({ flushed: 0, failed: 0 })
      expect(pendingEdgeDeleteCount()).toBe(0)
      expect(warns()).toHaveLength(1)
      expect(warns()[0]).toContain('2 pending')
    })

    it('the unload keepalive DELETE is dropped with one WARN', () => {
      defer('line-L1', async () => {})
      expect(drainPendingEdgeDeletesForUnload()).toEqual([])
      expect(pendingEdgeDeleteCount()).toBe(0)
      expect(warns()).toHaveLength(1)
    })

    it('the pagehide keepalive flush sends no DELETE while mismatched', () => {
      const fetchSpy = vi.fn(async () => new Response(null))
      vi.stubGlobal('fetch', fetchSpy)
      try {
        defer('line-L1', async () => {})
        defer('link-K1', async () => {})
        keepaliveFlushPendingEdgeDeletes()
        expect(fetchSpy).not.toHaveBeenCalled()
        expect(pendingEdgeDeleteCount()).toBe(0)
        expect(warns()).toHaveLength(1)
      } finally { vi.unstubAllGlobals() }
    })

    it('the control: without a mismatch the keepalive flush DELETEs each edge', () => {
      useUIStore.setState({ projectMismatch: null })
      const fetchSpy = vi.fn(async () => new Response(null))
      vi.stubGlobal('fetch', fetchSpy)
      try {
        defer('line-L1', async () => {})
        defer('link-K 1', async () => {})
        keepaliveFlushPendingEdgeDeletes()
        const calls = fetchSpy.mock.calls as unknown as Array<[string, RequestInit]>
        expect(calls.map(c => c[0]).sort()).toEqual(['/api/network/lines/L1', '/api/network/links/K%201'])
        expect(calls.every(c => c[1].method === 'DELETE' && c[1].keepalive === true)).toBe(true)
      } finally { vi.unstubAllGlobals() }
    })

    it('the control: without a mismatch the unload drain hands the entries over', () => {
      useUIStore.setState({ projectMismatch: null })
      defer('line-L1', async () => {})
      expect(drainPendingEdgeDeletesForUnload().map(e => e.edgeId)).toEqual(['line-L1'])
      expect(warns()).toHaveLength(0)
    })
  })
})
