// One merged geometry per site object (Phase 2 spec E5, plan Task 2.1).
// SiteCanvas-only: imports three.
//
// Phase 1 drew one mesh per part — a campus of ~40 objects with up to 120
// parts each was thousands of draw calls. Here every part except the rotor
// blades is placed (partMatrix) and merged into one indexed geometry with a
// per-vertex colour (the part's own, else the object's), so an object is one
// mesh: selection, hover and tint stay per object. Each turbine's rotor is
// its own geometry in hub-local coordinates, so the results layer can spin
// it about its own hub (WP6).

import { BufferAttribute, Color, Matrix4, type BufferGeometry } from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import { partShape, partMatrix } from './partGeometry'
import { toScene } from './scene'
import type { Anchors, Part } from './templates'

export interface ObjectGeometry {
  /** Every non-rotor part, merged; null when nothing is left (all heroed). */
  body: BufferGeometry | null
  /** One per turbine: its blades, in coordinates relative to `origin` (the hub, scene frame). */
  rotors: { turbine: number; origin: [number, number, number]; geometry: BufferGeometry }[]
}

function placed(parts: Part[], color: string, offset?: [number, number, number]): BufferGeometry | null {
  if (!parts.length) return null
  const shift = offset ? new Matrix4().makeTranslation(-offset[0], -offset[1], -offset[2]) : null
  const pieces = parts.map(p => {
    const g = partShape(p).applyMatrix4(partMatrix(p))
    if (shift) g.applyMatrix4(shift)
    const c = new Color(p.color ?? color)
    const n = g.getAttribute('position').count
    const rgb = new Float32Array(n * 3)
    for (let i = 0; i < n; i++) { rgb[i * 3] = c.r; rgb[i * 3 + 1] = c.g; rgb[i * 3 + 2] = c.b }
    g.setAttribute('color', new BufferAttribute(rgb, 3))
    return g
  })
  const merged = mergeGeometries(pieces, false)
  for (const g of pieces) g.dispose()
  return merged
}

/**
 * Merge an object's parts. `keep` filters parts out (a hero stands in for
 * them, WP3) — from the body and from the rotors alike; a kept rotor part
 * goes to its turbine's rotor.
 */
export function objectGeometry(parts: Part[], color: string, anchors?: Anchors, keep: (p: Part) => boolean = () => true): ObjectGeometry {
  const body = placed(parts.filter(p => p.anchor !== 'rotor' && keep(p)), color)
  const rotors = (anchors?.rotors ?? []).map(r => {
    const origin = toScene(...r.hub)
    return { turbine: r.turbine, origin, geometry: placed(parts.filter(p => p.anchor === 'rotor' && p.turbine === r.turbine && keep(p)), color, origin) }
  }).filter((r): r is ObjectGeometry['rotors'][number] => r.geometry !== null)
  return { body, rotors }
}
