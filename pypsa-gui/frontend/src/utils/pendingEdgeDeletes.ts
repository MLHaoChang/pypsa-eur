// ── Deferred line / link deletes ──────────────────────────────────────────────
// Deleting an edge on the canvas is optimistic: the edge disappears at once,
// but the DELETE is held for the length of the undo toast (5 s) so the Undo
// button has something to undo. Until it fires, the backend network still
// contains the line — so anything that serialises the network in that window
// writes a file that disagrees with what the user is looking at, and reopening
// the project brings the line back.
//
// This module-level registry is what lets the save flows close that window:
// they drain it and await the deletes before `projectsApi.save`, so Save
// persists what the user sees. It lives outside TopologyCanvas because the
// save flows must not have to import the canvas (and because a pending delete
// outlives the canvas being unmounted).
export interface PendingEdgeDelete {
  /** React Flow edge id, e.g. `line-L1` / `link-K1`. */
  edgeId: string
  /** The undo-window timer; cleared whenever the entry leaves the registry. */
  timer: ReturnType<typeof setTimeout>
  /** Undo toast to dismiss once the delete is final. */
  toastId?: string
  /** Fires the DELETE now. Rejects if the backend refused. */
  commit: () => Promise<void>
}

const pending = new Map<string, PendingEdgeDelete>()

/** Registering the same edge twice supersedes the first entry (and its timer). */
export function registerPendingEdgeDelete(entry: PendingEdgeDelete): void {
  const existing = pending.get(entry.edgeId)
  if (existing) clearTimeout(existing.timer)
  pending.set(entry.edgeId, entry)
}

/** Undo: drop the entry and stop its timer so no DELETE is ever sent. */
export function cancelPendingEdgeDelete(edgeId: string): PendingEdgeDelete | undefined {
  const entry = pending.get(edgeId)
  if (!entry) return undefined
  clearTimeout(entry.timer)
  pending.delete(edgeId)
  return entry
}

export function pendingEdgeDeleteCount(): number {
  return pending.size
}

/**
 * Empty the registry, stopping every timer, and hand the entries back. Callers
 * that can't await (the `pagehide` keepalive path) use this and fire their own
 * requests; everyone else should prefer `flushPendingEdgeDeletes`.
 */
export function drainPendingEdgeDeletes(): PendingEdgeDelete[] {
  const entries = [...pending.values()]
  entries.forEach(e => clearTimeout(e.timer))
  pending.clear()
  return entries
}

export interface FlushDeletesResult { flushed: number; failed: number }

/**
 * Commit every deferred delete and wait for the backend to acknowledge.
 * Failures are counted, not thrown: a save that follows should still proceed —
 * an edge whose DELETE failed is still present in the backend network, so the
 * file it writes stays consistent with reality either way.
 */
export async function flushPendingEdgeDeletes(): Promise<FlushDeletesResult> {
  const entries = drainPendingEdgeDeletes()
  if (entries.length === 0) return { flushed: 0, failed: 0 }
  const results = await Promise.allSettled(entries.map(e => e.commit()))
  const failed = results.filter(r => r.status === 'rejected').length
  return { flushed: entries.length - failed, failed }
}
