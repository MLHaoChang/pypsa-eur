// How a site object looks as a function of its state (Phase 2 spec E10,
// §6.4). Pure, no three: the renderer asks, this module decides. WP5 adds
// the result styling (fill, spin, loading, labels) here.

export const OUTSIDE_COLOR = '#dc2626'
export const HOVER_COLOR = '#fde68a'

export interface ObjectState { selected: boolean; hovered: boolean; outside: boolean }

/**
 * The emissive glow, with a fixed precedence: selected > outside the
 * boundary > hovered > a result. `result` is a 0..1 share (output, load);
 * it glows at most half as bright as a selection.
 */
export function emissiveFor(s: ObjectState, color: string, result?: number): { color: string; intensity: number } {
  if (s.selected) return { color, intensity: 0.6 }
  if (s.outside) return { color: OUTSIDE_COLOR, intensity: 0.45 }
  if (s.hovered) return { color: HOVER_COLOR, intensity: 0.25 }
  if (result != null && Number.isFinite(result)) return { color, intensity: Math.min(1, Math.max(0, result)) * 0.5 }
  return { color: '#000000', intensity: 0 }
}
