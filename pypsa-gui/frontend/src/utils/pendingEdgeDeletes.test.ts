// Deleting a line or link is deferred for 5 s so the toast's Undo button has
// something to undo — the DELETE has not reached the backend yet. That made
// Save inside the window write a network.nc that still contained the line: the
// canvas showed it gone, the file disagreed, and reopening the project brought
// it back. The save flows now drain this registry first, so Save persists what
// the user sees.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  registerPendingEdgeDelete, cancelPendingEdgeDelete, pendingEdgeDeleteCount,
  flushPendingEdgeDeletes, drainPendingEdgeDeletes,
} from './pendingEdgeDeletes'

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
})
