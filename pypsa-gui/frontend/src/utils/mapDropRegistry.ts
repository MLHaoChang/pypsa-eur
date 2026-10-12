// The slot through which the map view offers its projection to the palette
// drag (plan M4, reverting spec D26's "a map drop carries no coordinate"):
// MapCanvas fills it on mount and clears it on unmount; `useAssetDrag` (main
// bundle) reads it at drop time. The twin of `site3d/dropRegistry.ts` and of
// the schematic's `window.rfInstance` handoff, as a module slot so tests
// stub the module and not `window`. No Leaflet import: the registrant
// closes over the map.

export interface MapDropTarget {
  /** Client pixel → the geographic point under it, or null when the pixel is off the map. */
  screenToLatLng: (clientX: number, clientY: number) => { lat: number; lng: number } | null
}

let current: MapDropTarget | null = null

export function registerMapDropTarget(t: MapDropTarget): void { current = t }

/** Clears only if `t` is still the registered target (a stale unmount must not clear a newer mount). */
export function unregisterMapDropTarget(t: MapDropTarget): void { if (current === t) current = null }

export function mapDropTarget(): MapDropTarget | null { return current }
