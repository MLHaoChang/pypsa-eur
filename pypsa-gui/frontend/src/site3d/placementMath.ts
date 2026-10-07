// Placement ↔ matrix: the gizmo speaks three.js matrices, the sidecar
// speaks metres and a heading. Imported by SiteCanvas ONLY (it imports
// three); listed in the bundle guard's SiteCanvas-only set.
//
// Frames: scene x = east, scene y = up, scene z = −north (site3d/scene.ts).
// Heading is degrees clockwise from north; a rotation about the up axis by
// −heading (radians) — the same sign SiteObjectMesh and fit.ts use.

import { Matrix4, Vector3, Quaternion, Euler } from 'three'
import type { Placement } from './types'

const UP = new Vector3(0, 1, 0)

export function matrixFor(p: Placement): Matrix4 {
  const q = new Quaternion().setFromAxisAngle(UP, (-p.heading * Math.PI) / 180)
  return new Matrix4().compose(new Vector3(p.x, 0, -p.y), q, new Vector3(1, 1, 1))
}

/** Decompose a world matrix into a placement; the heading is normalised to [0, 360). */
export function placementFromMatrix(m: Matrix4): Placement {
  const pos = new Vector3(), q = new Quaternion(), s = new Vector3()
  m.decompose(pos, q, s)
  const e = new Euler().setFromQuaternion(q, 'YXZ')
  let heading = (-e.y * 180) / Math.PI
  heading = ((heading % 360) + 360) % 360
  if (Math.abs(heading - 360) < 1e-9) heading = 0
  return { x: round(pos.x), y: round(-pos.z), heading: round(heading) }
}

/** Millimetre precision is plenty for a planning placement and keeps the sidecar tidy. */
function round(v: number): number {
  const r = Math.round(v * 1000) / 1000
  return Object.is(r, -0) ? 0 : r
}
