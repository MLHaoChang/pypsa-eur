// Capacity denominators for results (Phase 2 spec §6.1, plan Task 5.4).
// Pure, no three: shared by the 3D view and CanvasResultsContext (whose
// period-effective vintage rule moved here unchanged).
import type { LoadProfileMeta, SolverConfig } from '../api/types'
import type { TSPayload } from '../pages/results/shared'

export type VintageResults = Record<string, Record<string, {
  initial_capacity: number
  periods: Array<{ build_year: number; p_nom_opt: number }>
}>>

/** A share's denominator: `*_nom_opt` if finite and > 0, else `*_nom` if > 0, else null (no share; the label shows MW only). */
export function capOf(c: Record<string, unknown>, param: 'p_nom' | 'e_nom' | 's_nom'): number | null {
  const opt = c[`${param}_opt`]
  if (typeof opt === 'number' && Number.isFinite(opt) && opt > 0) return opt
  const nom = c[param]
  return typeof nom === 'number' && Number.isFinite(nom) && nom > 0 ? nom : null
}

/**
 * The capacity in force in `period`: the initial capacity plus every vintage
 * built by then (every vintage when there is no period: single-period or
 * flat results). Without a vintage entry, `fallback`.
 */
export function periodEffectiveCap(vr: VintageResults | undefined, cls: string, name: string, period: number | null, fallback: number): number {
  const entry = vr?.[cls]?.[name]
  if (!entry) return fallback
  const initial = entry.initial_capacity ?? fallback
  let total = initial
  for (const p of entry.periods ?? []) {
    if (period == null || (p.build_year != null && p.build_year <= period)) total += p.p_nom_opt ?? 0
  }
  return total
}

/** The investment period of a chunk-local row (multi-period payloads carry `periods` parallel to `index`). */
export function periodAt(payload: Pick<TSPayload, 'periods'> | null | undefined, localIdx: number): number | null {
  const ps = payload?.periods
  return ps && localIdx >= 0 && localIdx < ps.length ? (ps[localIdx] as number) : null
}

/** The largest load multiplier the solver may apply (legacy and per-carrier maps), never below 1. */
export function maxLoadScaler(cfg: Pick<SolverConfig, 'load_scalers' | 'load_scalers_by_carrier'> | undefined): number {
  const vals = [
    ...Object.values(cfg?.load_scalers ?? {}),
    ...Object.values(cfg?.load_scalers_by_carrier ?? {}).flatMap(m => Object.values(m)),
  ].filter(v => Number.isFinite(v))
  return Math.max(1, ...vals)
}

/**
 * A load's peak, the denominator of its share: its profile's peak (from the
 * profile metadata, not the fetched chunk, whose max may be lower), else
 * |p_set|, times the largest scaler. Null when there is none.
 */
export function loadPeak(load: { name: string; p_set?: number }, profile: LoadProfileMeta | undefined, scaler: number): number | null {
  const base = profile?.has_profile ? profile.peak : Math.abs(load.p_set ?? 0)
  return Number.isFinite(base) && base > 0 ? base * scaler : null
}
