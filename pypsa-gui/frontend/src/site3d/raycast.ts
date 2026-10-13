// Screen ↔ ground for the 3D site view. Imported by SiteCanvas ONLY (it
// imports three); listed in the bundle guard's SiteCanvas-only set.
//
// The ground is the plane y = 0 in the scene (site frame, z up ↔ scene y
// up). After WP5 displaces the ground with terrain, a drop on a slope lands
// short by height·tan(pitch); documented in the plan (Task 4.1), not solved
// in Phase 1.

import { Plane, Raycaster, Vector2, Vector3, type Camera } from 'three'

const GROUND = new Plane(new Vector3(0, 1, 0), 0)

function ndc(rect: DOMRect, clientX: number, clientY: number): Vector2 | null {
  if (rect.width <= 0 || rect.height <= 0) return null
  return new Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1)
}

/** Client pixel → metres east/north on the ground plane, or null when the ray misses (pointing at the sky). */
export function screenToGround(camera: Camera, rect: DOMRect, clientX: number, clientY: number): { x: number; y: number } | null {
  const p = ndc(rect, clientX, clientY)
  if (!p) return null
  // A pointer-up from a window listener can be a frame behind OrbitControls
  // damping; refresh the matrices so the ray uses where the camera IS.
  camera.updateMatrixWorld()
  const ray = new Raycaster()
  ray.setFromCamera(p, camera)
  const hit = new Vector3()
  if (!ray.ray.intersectPlane(GROUND, hit)) return null
  return { x: hit.x, y: -hit.z }
}

/** Metres east/north on the ground → client pixel, or null when behind the camera. */
export function groundToScreen(camera: Camera, rect: DOMRect, x: number, y: number): { x: number; y: number } | null {
  camera.updateMatrixWorld()
  const v = new Vector3(x, 0, -y).project(camera)
  if (v.z > 1) return null
  return { x: rect.left + ((v.x + 1) / 2) * rect.width, y: rect.top + ((1 - v.y) / 2) * rect.height }
}
