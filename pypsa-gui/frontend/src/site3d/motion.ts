// Motion for results in 3D (Phase 2 spec E11; plan Task 6.1). No three.
//
// A snapshot step moves each value towards its new target over ~300 ms
// (exponential easing, frame-rate independent); rotors turn at a speed from
// the output. `prefers-reduced-motion` applies targets at once and holds the
// rotors still (their speed stays in the label and the readout).
import { useEffect, useState } from 'react'

/** Easing time constant: ~95 % of the way in 3 τ ≈ 300 ms. */
export const EASE_TAU_MS = 100
const SNAP = 1e-4

/** One frame of exponential easing: never overshoots; a long gap lands on the target. */
export function easeTowards(current: number, target: number, dtMs: number, tauMs: number): number {
  if (tauMs <= 0) return target
  if (dtMs <= 0) return current
  const next = target + (current - target) * Math.exp(-dtMs / tauMs)
  return Math.abs(next - target) <= SNAP * Math.max(1, Math.abs(target)) ? target : next
}

export interface Motion { tauMs: number; spin: boolean }
export const motionFor = (reduced: boolean): Motion => (reduced ? { tauMs: 0, spin: false } : { tauMs: EASE_TAU_MS, spin: true })

/** A rotor's turn in one frame, radians. */
export const rotorStep = (rpm: number, dtMs: number, spin: boolean): number => (spin && rpm > 0 ? (rpm / 60) * 2 * Math.PI * (dtMs / 1000) : 0)

const QUERY = '(prefers-reduced-motion: reduce)'
const media = (): MediaQueryList | null =>
  (typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(QUERY) : null)

/** Reactive `prefers-reduced-motion` (modelled on useIsCoarsePointer); false without matchMedia. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => media()?.matches ?? false)
  useEffect(() => {
    const mq = media()
    if (!mq) return
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduced
}

// ── The fly-in (visual-layers plan 3 S4; Phase 1 decision 3) ────────────────
//
// Generate 3D site and Open in 3D start the camera straight above the plot,
// looking down as the map did, and bring it to the fitted view over
// FLY_IN_MS. Reduced motion is a jump cut (duration 0). Poses are in scene
// coordinates (x east, y up, z south), as `fitCamera` returns them.

export const FLY_IN_MS = 900

const clamp01 = (u: number) => Math.min(1, Math.max(0, u))

/** Ease-out cubic: leaves fast, lands gently. */
export const easeOutCubic = (u: number): number => 1 - Math.pow(1 - clamp01(u), 3)

export interface Pose { position: [number, number, number]; target: [number, number, number] }

/**
 * Where the flight starts: above the fitted target at `lift` × the fitted
 * distance, nudged a hair south so the look-down never aligns with the
 * camera's up vector.
 */
export function flyStart(to: Pose, lift = 1.6): Pose {
  const [px, py, pz] = to.position, [tx, ty, tz] = to.target
  const dist = Math.hypot(px - tx, py - ty, pz - tz)
  const h = dist * lift
  return { position: [tx, ty + h, tz + h * 0.02], target: [tx, ty, tz] }
}

/** The pose `elapsedMs` into the flight, and whether it has landed. A duration of 0 lands at once. */
export function flyPose(from: Pose, to: Pose, elapsedMs: number, durationMs = FLY_IN_MS): { pose: Pose; done: boolean } {
  const u = durationMs <= 0 ? 1 : clamp01(elapsedMs / durationMs)
  const k = easeOutCubic(u)
  const lerp = (a: [number, number, number], b: [number, number, number]): [number, number, number] =>
    [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k]
  return { pose: { position: lerp(from.position, to.position), target: lerp(from.target, to.target) }, done: u >= 1 }
}

/** How long the fly-in takes: none under reduced motion. */
export const flyDuration = (reduced: boolean): number => (reduced ? 0 : FLY_IN_MS)
