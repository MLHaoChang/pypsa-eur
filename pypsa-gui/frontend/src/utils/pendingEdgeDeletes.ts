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
import { useUIStore } from '../store/uiStore'
import { appLog } from '../store/simulationStore'
import { mismatchSentence } from './projectMismatch'
import { rawFetchHeaders } from '../api/csrf'

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

/**
 * A2 (deferred spec 2026-09-28 §2.1): while this tab and the backend disagree
 * about the open project, a pending delete would remove a same-named line or
 * link from the OTHER project's live network. Drop every pending delete with
 * one WARN; the canvas is re-read when the tab reloads or switches.
 */
function dropIfProjectMismatch(where: string): boolean {
  const m = useUIStore.getState().projectMismatch
  if (!m) return false
  const n = drainPendingEdgeDeletes().length
  if (n > 0) {
    appLog('WARN', `Dropped ${n} pending edge delete(s) on ${where} — ${mismatchSentence(m)}`)
  }
  return true
}

/**
 * The `pagehide` keepalive path's drain: the entries to DELETE with a raw
 * `fetch` (which the axios interceptor never sees) — none while the tab is
 * mismatched.
 */
export function drainPendingEdgeDeletesForUnload(): PendingEdgeDelete[] {
  if (dropIfProjectMismatch('unload')) return []
  return drainPendingEdgeDeletes()
}

/**
 * The `pagehide` / `beforeunload` flush (moved here from TopologyCanvas so it
 * is testable): DELETE every pending edge with `fetch keepalive`, which
 * survives the page going away — axios requests are cancelled on unload. A raw
 * fetch bypasses the axios interceptors, so it carries its own CSRF header and
 * — A2 — goes through `drainPendingEdgeDeletesForUnload`, which drops the lot
 * (one WARN) while the tab and the backend disagree about the open project.
 */
export function keepaliveFlushPendingEdgeDeletes(): void {
  for (const { edgeId } of drainPendingEdgeDeletesForUnload()) {
    const isLink = edgeId.startsWith('link-')
    const name = edgeId.replace(/^(line-|link-)/, '')
    const url = `/api/network/${isLink ? 'links' : 'lines'}/${encodeURIComponent(name)}`
    try {
      fetch(url, {
        method: 'DELETE',
        headers: { ...rawFetchHeaders('DELETE') },
        keepalive: true,
      }).catch(() => { /* best effort */ })
    } catch { /* keepalive unsupported — drop on the floor */ }
  }
}

export interface FlushDeletesResult { flushed: number; failed: number }

/**
 * Commit every deferred delete and wait for the backend to acknowledge.
 * Failures are counted, not thrown: a save that follows should still proceed —
 * an edge whose DELETE failed is still present in the backend network, so the
 * file it writes stays consistent with reality either way.
 */
export async function flushPendingEdgeDeletes(): Promise<FlushDeletesResult> {
  if (dropIfProjectMismatch('save')) return { flushed: 0, failed: 0 }
  const entries = drainPendingEdgeDeletes()
  if (entries.length === 0) return { flushed: 0, failed: 0 }
  const results = await Promise.allSettled(entries.map(e => e.commit()))
  const failed = results.filter(r => r.status === 'rejected').length
  return { flushed: entries.length - failed, failed }
}
