// One part's geometry and placement (SiteCanvas-only: imports three).
//
// `partShape` is the geometry in the part's own frame — a box sized (east,
// height, north) in scene axes, or a cylinder whose axis is baked in (a
// north- or east-lying cylinder is rotated once, here, not by a render-time
// callback: r3f ran `onUpdate` twice at mount and undid the turn — WP1
// review gate). `partMatrix` places it: the part's rotations and its centre
// in scene coordinates (x = east, y = up, z = −north). WP2 merges an
// object's parts with exactly these two.

import { BoxGeometry, CylinderGeometry, Euler, Matrix4, Quaternion, Vector3, type BufferGeometry } from 'three'
import { toScene, toBoxArgs } from './scene'
import type { Part } from './templates'

export const CYLINDER_SEGMENTS = 16

export function partShape(p: Part): BufferGeometry {
  if (p.shape !== 'cylinder') return new BoxGeometry(...toBoxArgs(p.size))
  const [e, n, h] = p.size
  if (p.axis === 'north') return new CylinderGeometry(e / 2, e / 2, n, CYLINDER_SEGMENTS).rotateX(Math.PI / 2)
  if (p.axis === 'east') return new CylinderGeometry(n / 2, n / 2, e, CYLINDER_SEGMENTS).rotateZ(Math.PI / 2)
  return new CylinderGeometry(e / 2, e / 2, h, CYLINDER_SEGMENTS)
}

/**
 * The part's placement in its object's frame. Rotations, in scene axes:
 * `rotX` about east (tilt), `rotZ` about up (yaw), `rotN` about north —
 * scene Z is −north, so the angle is negated: the part's east axis turns to
 * (cos rotN, −sin rotN) in the (east, up) plane.
 */
export function partMatrix(p: Part): Matrix4 {
  const q = new Quaternion().setFromEuler(new Euler(p.rotX ?? 0, p.rotZ ?? 0, -(p.rotN ?? 0)))
  return new Matrix4().compose(new Vector3(...toScene(p.pos[0], p.pos[1], p.pos[2])), q, new Vector3(1, 1, 1))
}
