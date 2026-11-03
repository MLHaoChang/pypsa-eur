// SC-02d: group editing and detach of placed equipment. Pure helpers (no
// three, no React): the move/rotate a group preview sends as ONE
// `place_existing`, and the words for detach and undo refusals.
//
// Frames: x = east, y = north (metres); heading is degrees clockwise from
// north, as everywhere in site3d.

import type { PlaceEntry } from '../api/siteScene'

export interface GroupMove { east: number; north: number; rotate: number }
export const NO_MOVE: GroupMove = { east: 0, north: 0, rotate: 0 }

const round = (v: number, step: number) => {
  const r = Math.round(v / step) * step
  return Object.is(r, -0) ? 0 : Number(r.toFixed(6))
}

/** The centre the group rotates about: the mean of the items' positions. */
export function groupCentre(entries: readonly PlaceEntry[]): [number, number] {
  if (entries.length === 0) return [0, 0]
  const sx = entries.reduce((s, e) => s + e.x, 0), sy = entries.reduce((s, e) => s + e.y, 0)
  return [sx / entries.length, sy / entries.length]
}

/**
 * Each item's new placement: rotated clockwise by `rotate` degrees about the
 * group's centre, then moved east and north. Headings turn with the group;
 * each item keeps its `on` (ground or its roof). Centimetre / 0.1° rounding.
 */
export function moveGroup(placements: Readonly<Record<string, PlaceEntry>>, keys: readonly string[],
  move: GroupMove): Record<string, PlaceEntry> {
  const entries = keys.map(k => placements[k]).filter((e): e is PlaceEntry => !!e)
  const [cx, cy] = groupCentre(entries)
  const t = (move.rotate * Math.PI) / 180
  const cos = Math.cos(t), sin = Math.sin(t)
  const out: Record<string, PlaceEntry> = {}
  for (const key of keys) {
    const e = placements[key]
    if (!e) continue
    const dx = e.x - cx, dy = e.y - cy
    // Clockwise seen from above: east turns toward south.
    const x = cx + dx * cos + dy * sin + move.east
    const y = cy - dx * sin + dy * cos + move.north
    const heading = ((round(e.heading + move.rotate, 0.1) % 360) + 360) % 360
    out[key] = { x: round(x, 0.01), y: round(y, 0.01), heading, on: e.on ?? 'ground' }
  }
  return out
}

export const isNoMove = (m: GroupMove) => m.east === 0 && m.north === 0 && m.rotate === 0
export const moveValid = (m: GroupMove) => [m.east, m.north, m.rotate].every(Number.isFinite)

/** The words UX asked for: the Undo receipt and the reason it can no longer be used. */
export const UNDO_GONE = 'This detach can’t be undone now: the scene changed since.'
export const detachedCopy = (n: number) => `Detached ${n} ${n === 1 ? 'item' : 'items'}`
export const detachTitle = (n: number) => `Detach ${n} ${n === 1 ? 'item' : 'items'} from the scene. ${n === 1 ? 'It stays' : 'They stay'} in the model.`
export const undoTitle = (n: number) => `Undo detach: restore ${n} ${n === 1 ? 'item' : 'items'}`
export const editTitle = (n: number) => `Edit ${n} items`

/**
 * A detach or undo refusal as a plain sentence naming the item (UX 1); null
 * leaves the server's own words. `item` is the display name of the refused key.
 */
export function detachRefusalCopy(code: string | undefined, item: string | null): string | null {
  const it = item ?? 'This item'
  switch (code) {
    case 'not_placed': return `${it}: it is not placed in this scene, so there is nothing to detach`
    case 'duplicate': return `${it}: it is listed twice`
    case 'already_placed': return `${it}: it was placed again since the detach, so it can’t be restored`
    case 'unbound_target': return `${it}: its model item was deleted or replaced since the detach, so it can’t be restored`
    case 'target_changed': return `${it}: something it was attached to changed since the detach, so it can’t be restored`
    case 'invalid_allocation': return `${it}: it carries a share of a split; change the split first`
    case 'unsupported_target': return `${it}: it is not equipment that can be placed or detached`
    case 'detach_not_found': return UNDO_GONE
    default: return null
  }
}

/** A canvas click picks (instead of selecting) only with Shift held, on a scene-enabled Site, on a placed item. */
export const clickPicks = (shift: boolean, sceneEnabled: boolean, placed: boolean) => shift && sceneEnabled && placed
