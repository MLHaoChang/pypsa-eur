// Capacity denominators for results (Phase 2 spec §6.1, plan Task 5.4).
// Pure, no three: shared by the 3D view and CanvasResultsContext (whose
// period-effective vintage rule moved here unchanged).
import type { LoadProfileMeta, SolverConfig } from '../api/types'
import type { TSPayload } from '../pages/results/shared'
import { loadCarrierKey } from '../pages/results/carrierAliases'

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

type ScalerConfig = Pick<SolverConfig, 'multi_investment_periods' | 'load_scalers' | 'load_scalers_by_carrier'>

/**
 * A load's peak, the denominator of its share. A profile load: its
 * profile's peak (from the metadata, not the fetched chunk, whose max may
 * be lower) times the largest factor the solver applies to it over the
 * network's investment periods — per carrier bucket first, then the legacy map, 1
 * where neither says (backend demand.py `load_scale_factors`; multi-period
 * runs only, time series only). A static load: |p_set|, never scaled.
 * Null when there is none.
 */
export function loadPeak(load: { name: string; carrier?: string; p_set?: number }, profile: LoadProfileMeta | undefined, cfg: ScalerConfig | undefined, periods: readonly number[] | undefined): number | null {
  if (profile?.has_profile) {
    const peak = profile.peak
    if (!Number.isFinite(peak) || peak <= 0) return null
    if (!cfg?.multi_investment_periods || !periods?.length) return peak
    const block = cfg.load_scalers_by_carrier?.[loadCarrierKey(load.carrier)]
    const factors = periods.map(p => {
      const f = block?.[String(p)] ?? cfg.load_scalers?.[String(p)]
      return f != null && Number.isFinite(f) ? f : 1
    })
    return peak * Math.max(...factors)
  }
  const base = Math.abs(load.p_set ?? 0)
  return Number.isFinite(base) && base > 0 ? base : null
}
