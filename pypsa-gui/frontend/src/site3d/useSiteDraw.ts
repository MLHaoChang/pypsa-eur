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

import { useCallback, useEffect } from 'react'
import { useUIStore } from '../store/uiStore'
import { useRescaleStore } from '../store/rescaleStore'
import { collapseDuplicateVertices, isValidBoundary, sameVertex } from './boundary'
import type { LngLatTuple } from './types'

export type SiteDrawMode = 'idle' | 'drawing'
export type MapClickOwner = 'place' | 'draw' | null

export function mapClickOwner(s: { placementActive: boolean; siteDrawMode: SiteDrawMode }): MapClickOwner {
  if (s.placementActive) return 'place'
  if (s.siteDrawMode === 'drawing') return 'draw'
  return null
}

export type CloseResult = { status: 'too_few' } | { status: 'closed'; boundary: LngLatTuple[] }

/**
 * Close a draft: collapse consecutive duplicates anywhere (a stray
 * double-click leaves them mid-list, not only at the end), drop a closing
 * vertex equal to the first, require three vertices.
 */
export function closeDraft(draft: LngLatTuple[]): CloseResult {
  const boundary = collapseDuplicateVertices(draft)
  if (boundary.length < 3 || !isValidBoundary(boundary)) return { status: 'too_few' }
  return { status: 'closed', boundary }
}

/** Whether the map's "New site" control is offered right now. */
export function newSiteButtonVisible(s: {
  activeSlidePanel: unknown
  paletteMode: unknown
  placing: boolean
  drawing: boolean
  draftOpen: boolean
}): boolean {
  return !s.activeSlidePanel && s.paletteMode === null && !s.placing && !s.drawing && !s.draftOpen
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

  // Placement started while a boundary was being drawn: the draft is
  // abandoned, visibly, rather than suspended and resumed unannounced when
  // placement ends.
  useEffect(() => {
    if (placementActive && mode === 'drawing') { setDraft([]); setMode('idle') }
  }, [placementActive, mode, setDraft, setMode])

  const start = useCallback((): boolean => {
    if (readOnly || placementActive) return false
    setDraft([])
    setMode('drawing')
    return true
  }, [readOnly, placementActive, setDraft, setMode])

  const cancel = useCallback(() => { setDraft([]); setMode('idle') }, [setDraft, setMode])

  const addVertex = useCallback((lng: number, lat: number) => {
    if (owner !== 'draw' || readOnly) return
    const cur = useUIStore.getState().siteDraft
    const last = cur[cur.length - 1]
    // A repeat of the last vertex (the second click of a double-click, a
    // jitter) adds nothing.
    if (last && sameVertex(last, [lng, lat])) return
    setDraft([...cur, [lng, lat]])
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
    // Leaflet fires click, click, dblclick: the clicks already added the
    // point (once — `addVertex` drops the repeat). Add it here only if a
    // synthetic dblclick arrived without them, then close.
    const cur = useUIStore.getState().siteDraft
    const last = cur[cur.length - 1]
    const withPoint = last && sameVertex(last, [lng, lat]) ? cur : [...cur, [lng, lat] as LngLatTuple]
    const result = closeDraft(withPoint)
    if (result.status === 'closed') { setDraft([]); setMode('idle') }
    // Keep the CLEANED draft so a stray double-click never wedges the
    // session in a state Enter can never close.
    else setDraft(collapseDuplicateVertices(withPoint))
    return result
  }, [owner, readOnly, setDraft, setMode])

  return { drawing, draft, owner, start, cancel, addVertex, finish, closeByDoubleClick }
}
