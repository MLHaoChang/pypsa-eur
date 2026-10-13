// The site thumbnail (visual-layers plan 3 S5): the decisions around the
// capture the canvas takes from the south — when one is due, how large it
// is, where it lives — kept pure so they are tested without WebGL. The
// capture itself (render + toDataURL) is in pages/SiteCanvas.tsx; the file
// is PUT to `sites/<id>/thumbnail` and read back by the hub-design card.

/** The longest side of the stored PNG, px: enough for a card and a report figure, well under the route's 2 MB cap. */
export const THUMB_MAX_PX = 1200

/** The stored size for a canvas of `w` × `h` device pixels: scaled down to THUMB_MAX_PX on the long side, never up. */
export function thumbSize(w: number, h: number, maxPx = THUMB_MAX_PX): { w: number; h: number } {
  if (!(w > 0) || !(h > 0)) return { w: 0, h: 0 }
  const k = Math.min(1, maxPx / Math.max(w, h))
  return { w: Math.max(1, Math.round(w * k)), h: Math.max(1, Math.round(h * k)) }
}

/**
 * Whether a capture is due: once per fresh solve — `freshSince` (the moment
 * the solve's results became current for this network) is new and the
 * dispatch is fresh — and never for the same solve twice. `last` is the
 * freshSince already captured (0 = none).
 */
export function captureDue(solved: boolean, freshSince: number, last: number): boolean {
  return solved && freshSince > 0 && freshSince !== last
}

/** The thumbnail's URL, versioned so an <img> re-reads it after a capture (the route is no-store anyway). */
export function thumbnailUrl(project: string, siteId: string, version: number): string {
  return `/api/projects/${encodeURIComponent(project)}/sites/${encodeURIComponent(siteId)}/thumbnail?v=${version}`
}
