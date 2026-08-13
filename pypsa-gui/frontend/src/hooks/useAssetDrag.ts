import { useState } from 'react'
import { useUIStore } from '../store/uiStore'

// ── Palette drag, extracted from Sidebar.tsx ─────────────────────────────────
// Manual pointer-event drag, NOT HTML5 drag-and-drop: the HTML5 API was
// unreliable in the user's environment (drops didn't land), so the gesture is
// tracked by hand — pointerdown → pointermove → pointerup — with our own
// ghost and our own hit-test via document.elementFromPoint.
//
// This file is the ONLY owner of the hit-test. The duplicate that lived in
// layout/AssetPalette.tsx is deleted, not migrated: that file was dead and its
// palette was stale (it labelled `hydrogen` "P2G / Electrolysis" while
// FIELD_MAP.hydrogen is a StorageUnit).

/** What the pointer was over when the drag was released. */
export interface DropResult {
  /** 'schematic' = React Flow (.react-flow); 'map' = Leaflet
   *  (.leaflet-container); null = released outside both, i.e. cancelled. */
  canvas: 'schematic' | 'map' | null
  /** Name of the bus under the pointer, from the nearest [data-bus-name]
   *  ancestor. null when the release did not land on a bus. */
  busName: string | null
  /** React Flow flow-space coordinates. Non-null ONLY for a schematic drop
   *  with window.rfInstance present. Always null on the map: map drops
   *  prefill terminals only, so no coordinate conversion is needed and no
   *  global Leaflet handle exists to do it with (spec D26). */
  position: { x: number; y: number } | null
}

export interface AssetDragItem { id: string; label: string }

const DRAG_THRESHOLD_PX = 3

type RfInstance = { screenToFlowPosition: (p: { x: number; y: number }) => { x: number; y: number } }

function flowPosition(clientX: number, clientY: number): { x: number; y: number } | null {
  // TopologyCanvas pins the instance to window in onInit (TopologyCanvas.tsx
  // :2923-2924). Without it a new node would land at (0,0) regardless of
  // where it was dropped, so a missing handle degrades to "no position"
  // rather than to a wrong one.
  const rf = (window as unknown as { rfInstance?: RfInstance }).rfInstance
  return rf?.screenToFlowPosition({ x: clientX, y: clientY }) ?? null
}

/**
 * Resolve a release point to a drop outcome. Evaluation order is fixed:
 *   1. [data-bus-name]      → a bus drop, carrying that bus's name
 *   2. .react-flow          → the schematic canvas, no bus
 *   3. .leaflet-container   → the map canvas, no bus
 *   4. otherwise            → cancelled
 * Testing the bus attribute FIRST is what lets one attribute serve both
 * canvases. Using React Flow's own `data-id` instead would tie this to
 * @xyflow/react's internal markup and would still need a second check to tell
 * a `bus` node from an `assetGroup` node (TopologyCanvas.tsx:1786).
 */
export function resolveDrop(clientX: number, clientY: number): DropResult {
  const target = document.elementFromPoint(clientX, clientY)
  const busEl = target?.closest('[data-bus-name]') ?? null
  const busName = busEl?.getAttribute('data-bus-name') ?? null

  const schematic = target?.closest('.react-flow') ?? null
  if (schematic) {
    return { canvas: 'schematic', busName, position: flowPosition(clientX, clientY) }
  }
  const map = target?.closest('.leaflet-container') ?? null
  if (map) {
    return { canvas: 'map', busName, position: null }
  }
  return { canvas: null, busName: null, position: null }
}

export function useAssetDrag(): {
  ghost: { label: string; x: number; y: number } | null
  beginDrag: (e: React.PointerEvent, item: AssetDragItem) => void
} {
  // Drag ghost — a fixed-position chip that follows the cursor. The caller
  // renders it with pointer-events:none so pointerup passes through to the
  // real drop target underneath.
  const [ghost, setGhost] = useState<{ label: string; x: number; y: number } | null>(null)
  const setCreationItem = useUIStore(s => s.setCreationItem)

  function beginDrag(e: React.PointerEvent, item: AssetDragItem) {
    if (e.button !== 0) return  // left button only
    e.preventDefault()
    const startX = e.clientX
    const startY = e.clientY
    let moved = false

    const onMove = (ev: PointerEvent) => {
      const dx = Math.abs(ev.clientX - startX)
      const dy = Math.abs(ev.clientY - startY)
      if (!moved && (dx > DRAG_THRESHOLD_PX || dy > DRAG_THRESHOLD_PX)) {
        moved = true
        document.body.style.cursor = 'grabbing'
      }
      if (moved) setGhost({ label: item.label, x: ev.clientX, y: ev.clientY })
    }

    const onUp = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      document.body.style.cursor = ''
      setGhost(null)

      if (!moved) {
        // Click — open the slide-in panel with no drop data at all.
        setCreationItem({ id: item.id, label: item.label })
        return
      }

      const drop = resolveDrop(ev.clientX, ev.clientY)
      if (drop.canvas === null) return  // released outside both canvases — cancel silently

      setCreationItem({
        id: item.id,
        label: item.label,
        ...(drop.position ? { dropPosition: drop.position } : {}),
        ...(drop.busName ? { dropBusName: drop.busName } : {}),
      })
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  return { ghost, beginDrag }
}
