// How big a component is drawn (Phase 2 spec E6, plan Task 4.1). No three.
//
// Installed sizes (`p_nom`, `e_nom`, `s_nom`) by default. In optimised mode
// an extendable asset is drawn at its `*_nom_opt` when that is finite and
// positive (a myopic run leaves `-0.0` on assets it did not build);
// anything else stays at its installed size. The mode is optimised only
// while dispatch is fresh, so an edit never leaves the previous solve's
// sizes on screen.
import type { SizeRule } from './assetLibrary'

export type SizingMode = 'installed' | 'optimised'

export interface Sized {
  amount: number
  unit: string
  /** A StorageUnit's power beside its energy. */
  mw?: number
  /** Drawn at the optimum rather than the installed size. */
  optimised: boolean
}

export const effectiveSizing = (siteSizing: SizingMode, dispatchFresh: boolean): SizingMode =>
  siteSizing === 'optimised' && dispatchFresh ? 'optimised' : 'installed'

const num = (v: unknown): number => Number(v ?? 0) || 0

/** `<param>_opt` when optimised, extendable and finite > 0; else `<param>`. */
function nominal(c: Record<string, unknown>, param: 'p_nom' | 'e_nom' | 's_nom', mode: SizingMode): { v: number; opt: boolean } {
  const opt = c[`${param}_opt`]
  if (mode === 'optimised' && c[`${param}_extendable`] === true && typeof opt === 'number' && Number.isFinite(opt) && opt > 0) return { v: opt, opt: true }
  return { v: num(c[param]), opt: false }
}

export function sizeOf(c: Record<string, unknown>, rule: SizeRule, mode: SizingMode): Sized {
  switch (rule.param) {
    case 'p_nom': case 'e_nom': case 's_nom': {
      const { v, opt } = nominal(c, rule.param, mode)
      return { amount: v, unit: rule.unit, optimised: opt }
    }
    case 'mwh': {
      const { v, opt } = nominal(c, 'p_nom', mode)
      return { amount: v * (num(c.max_hours) || 1), unit: rule.unit, mw: v, optimised: opt }
    }
    case 'p_set': return { amount: Math.abs(num(c.p_set)), unit: rule.unit, optimised: false }
    default: return { amount: 0, unit: '', optimised: false }
  }
}
