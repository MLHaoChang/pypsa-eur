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
