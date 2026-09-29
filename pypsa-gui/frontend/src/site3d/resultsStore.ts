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
  const last = useRef<{ r: SiteResults; v: T } | null>(null)
  const get = useCallback(() => {
    const r = store.get()
    if (last.current && last.current.r === r) return last.current.v
    const v = select(r)
    if (last.current && equal(last.current.v, v)) { last.current = { r, v: last.current.v }; return last.current.v }
    last.current = { r, v }
    return v
  }, [store, select, equal])
  return useSyncExternalStore(store.subscribe, get)
}
