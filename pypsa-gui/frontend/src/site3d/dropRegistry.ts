// The slot through which the 3D site view offers its ground plane to the
// palette drag (design D16). The canvas fills it on mount and clears it on
// unmount; `useAssetDrag` (main bundle) reads it at drop time. No imports,
// on purpose: this file must never pull `three` into the main bundle.
//
// Mirrors the schematic's `window.rfInstance` handoff, as a module slot
// rather than a global, so tests stub the module and not `window`.

export interface SiteDropTarget {
  /** The site the canvas is showing. */
  siteId: string
  /** Client pixel → metres on the ground plane in the site frame, or null when the ray misses the ground. */
  screenToGround: (clientX: number, clientY: number) => { x: number; y: number } | null
  /** Inverse, for tests and the debug hook. */
  groundToScreen: (x: number, y: number) => { x: number; y: number } | null
}

let current: SiteDropTarget | null = null

export function registerSiteDropTarget(t: SiteDropTarget): void { current = t }

/** Clears only if `t` is still the registered target (a stale unmount must not clear a newer mount). */
export function unregisterSiteDropTarget(t: SiteDropTarget): void { if (current === t) current = null }

export function siteDropTarget(): SiteDropTarget | null { return current }
