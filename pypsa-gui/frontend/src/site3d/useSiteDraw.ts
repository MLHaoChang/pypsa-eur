// Drawing a site boundary on the map: click adds a vertex, double-click or
// Enter closes, Escape cancels. The state lives in uiStore so the map and
// the draft panel share it; this hook is the only writer.
//
// Click ownership. The map already has one click consumer — bus placement
// (`ClickToPlace`, driven by the rescale store's `placementActive`). Two
// consumers on `map.on('click')` would each act on every click, so ownership
// is a single pure decision: placement wins, drawing only starts when
// nothing else owns the map, and read-only blocks drawing entirely.
//
// Pure functions first, hook second. Main bundle; no Leaflet, no three.

import { useCallback } from 'react'
import { useUIStore } from '../store/uiStore'
import { useRescaleStore } from '../store/rescaleStore'
import { dedupeTrailing, isValidBoundary } from './boundary'
import type { LngLatTuple } from './types'

export type SiteDrawMode = 'idle' | 'drawing'
export type MapClickOwner = 'place' | 'draw' | null

export function mapClickOwner(s: { placementActive: boolean; siteDrawMode: SiteDrawMode }): MapClickOwner {
  if (s.placementActive) return 'place'
  if (s.siteDrawMode === 'drawing') return 'draw'
  return null
}

export type CloseResult = { status: 'too_few' } | { status: 'closed'; boundary: LngLatTuple[] }

/** Close a draft: drop the double-click's trailing duplicates, require three vertices. */
export function closeDraft(draft: LngLatTuple[]): CloseResult {
  const boundary = dedupeTrailing(draft)
  if (boundary.length < 3 || !isValidBoundary(boundary)) return { status: 'too_few' }
  return { status: 'closed', boundary }
}

export function useSiteDraw() {
  const mode = useUIStore(s => s.siteDrawMode)
  const draft = useUIStore(s => s.siteDraft)
  const readOnly = useUIStore(s => s.readOnly)
  const placementActive = useRescaleStore(s => s.placementActive)
  const setMode = useUIStore(s => s.setSiteDrawMode)
  const setDraft = useUIStore(s => s.setSiteDraft)

  const owner = mapClickOwner({ placementActive, siteDrawMode: mode })
  const drawing = owner === 'draw'

  const start = useCallback((): boolean => {
    if (readOnly || placementActive) return false
    setDraft([])
    setMode('drawing')
    return true
  }, [readOnly, placementActive, setDraft, setMode])

  const cancel = useCallback(() => { setDraft([]); setMode('idle') }, [setDraft, setMode])

  const addVertex = useCallback((lng: number, lat: number) => {
    if (owner !== 'draw' || readOnly) return
    setDraft([...useUIStore.getState().siteDraft, [lng, lat]])
  }, [owner, readOnly, setDraft])

  // Both close paths leave the hook in `idle` on success and untouched on
  // `too_few`, so the user can keep clicking.
  const finish = useCallback((): CloseResult => {
    if (owner !== 'draw') return { status: 'too_few' }
    const result = closeDraft(useUIStore.getState().siteDraft)
    if (result.status === 'closed') { setDraft([]); setMode('idle') }
    return result
  }, [owner, setDraft, setMode])

  const closeByDoubleClick = useCallback((lng: number, lat: number): CloseResult => {
    if (owner !== 'draw' || readOnly) return { status: 'too_few' }
    // Leaflet fires click, click, dblclick: the two clicks already added the
    // point (twice). Add it once more so a draft that never received them
    // (a synthetic dblclick) still closes on the same vertex, then dedupe.
    setDraft([...useUIStore.getState().siteDraft, [lng, lat]])
    const result = closeDraft([...useUIStore.getState().siteDraft])
    if (result.status === 'closed') { setDraft([]); setMode('idle') }
    return result
  }, [owner, readOnly, setDraft, setMode])

  return { drawing, draft, owner, start, cancel, addVertex, finish, closeByDoubleClick }
}
