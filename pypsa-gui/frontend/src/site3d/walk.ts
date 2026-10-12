// Walk the campus (visual-layers plan 3 S5): the pure half of the first-
// person mode — which keys walk, how fast, how high the eye is, and the
// ground-plane step for the keys held. WalkControls.tsx (three) applies it.
// Metres, seconds; (east, north) on the ground.

export const EYE_HEIGHT_M = 1.7
/** A comfortable walking pace. */
export const WALK_SPEED_MPS = 1.4

export type WalkKey = 'forward' | 'back' | 'left' | 'right'

/** `KeyboardEvent.code` → the walk it means (WASD and the arrows). */
export const WALK_KEYS: Readonly<Record<string, WalkKey>> = Object.freeze({
  KeyW: 'forward', ArrowUp: 'forward',
  KeyS: 'back', ArrowDown: 'back',
  KeyA: 'left', ArrowLeft: 'left',
  KeyD: 'right', ArrowRight: 'right',
})

/**
 * The step on the ground for the keys held: `forward` is the camera's view
 * direction projected on the ground (east, north; any length, zero when
 * looking straight down), right is 90° clockwise of it. Opposite keys
 * cancel; a diagonal is no faster than a straight line.
 */
export function walkDelta(held: ReadonlySet<WalkKey>, forward: { x: number; y: number }, dtS: number, speed = WALK_SPEED_MPS): { x: number; y: number } {
  const len = Math.hypot(forward.x, forward.y)
  if (!(len > 1e-6) || !(dtS > 0)) return { x: 0, y: 0 }
  const fx = forward.x / len, fy = forward.y / len
  const rx = fy, ry = -fx
  const ahead = (held.has('forward') ? 1 : 0) - (held.has('back') ? 1 : 0)
  const side = (held.has('right') ? 1 : 0) - (held.has('left') ? 1 : 0)
  if (!ahead && !side) return { x: 0, y: 0 }
  const vx = fx * ahead + rx * side, vy = fy * ahead + ry * side
  const vlen = Math.hypot(vx, vy)
  const s = (speed * dtS) / vlen
  return { x: vx * s, y: vy * s }
}
