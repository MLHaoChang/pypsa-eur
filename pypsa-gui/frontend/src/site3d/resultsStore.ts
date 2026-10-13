// The snapshot's results, shared between the driver that writes them and the
// scene / DOM leaves that read them (Phase 2 spec §6.1, §6.4). No three.
import { useCallback, useRef, useSyncExternalStore } from 'react'
import type { SiteResults } from './useSiteResults'

export interface ResultsStore {
  get(): SiteResults
  set(r: SiteResults): void
  subscribe(listener: () => void): () => void
}

export const NO_RESULTS: SiteResults = { states: new Map(), idx: 0, iso: '' }

export function createResultsStore(): ResultsStore {
  let value = NO_RESULTS
  const listeners = new Set<() => void>()
  return {
    get: () => value,
    set(r) { if (r === value) return; value = r; for (const l of listeners) l() },
    subscribe(l) { listeners.add(l); return () => { listeners.delete(l) } },
  }
}

/** Subscribe to a slice: the host re-renders only when `select` returns a different value (Object.is, or `equal`). */
export function useResultsSelector<T>(store: ResultsStore, select: (r: SiteResults) => T, equal: (a: T, b: T) => boolean = Object.is): T {
  const last = useRef<{ r: SiteResults; select: (r: SiteResults) => T; v: T } | null>(null)
  const get = useCallback(() => {
    const r = store.get()
    const prev = last.current
    if (prev && prev.r === r && prev.select === select) return prev.v
    const v = select(r)
    // An equal value keeps the previous reference, so the host does not re-render.
    last.current = { r, select, v: prev && equal(prev.v, v) ? prev.v : v }
    return last.current.v
  }, [store, select, equal])
  return useSyncExternalStore(store.subscribe, get)
}
