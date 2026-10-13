// Phase 2 spec E6, plan Task 4.1: installed sizes by default; optimised
// sizes only for extendable assets with a finite positive *_nom_opt, and
// only while dispatch is fresh.
import { describe, it, expect } from 'vitest'
import { sizeOf, effectiveSizing } from './sizing'

const c = (o: Record<string, unknown>) => ({ name: 'x', ...o })

describe('sizeOf', () => {
  it('installed mode reads *_nom whatever the optimum', () => {
    expect(sizeOf(c({ p_nom: 10, p_nom_opt: 40, p_nom_extendable: true }), { param: 'p_nom', unit: 'MW' }, 'installed'))
      .toEqual({ amount: 10, unit: 'MW', optimised: false })
    expect(sizeOf(c({ e_nom: 5, e_nom_opt: 9, e_nom_extendable: true }), { param: 'e_nom', unit: 'MWh' }, 'installed').amount).toBe(5)
    expect(sizeOf(c({ s_nom: 20, s_nom_opt: 60, s_nom_extendable: true }), { param: 's_nom', unit: 'MVA' }, 'installed').amount).toBe(20)
  })
  it('optimised mode reads *_nom_opt for an extendable asset', () => {
    expect(sizeOf(c({ p_nom: 10, p_nom_opt: 40, p_nom_extendable: true }), { param: 'p_nom', unit: 'MW' }, 'optimised'))
      .toEqual({ amount: 40, unit: 'MW', optimised: true })
    expect(sizeOf(c({ e_nom: 5, e_nom_opt: 9, e_nom_extendable: true }), { param: 'e_nom', unit: 'MWh' }, 'optimised').amount).toBe(9)
    expect(sizeOf(c({ s_nom: 20, s_nom_opt: 60, s_nom_extendable: true }), { param: 's_nom', unit: 'MVA' }, 'optimised').amount).toBe(60)
  })
  it('falls back to installed when not extendable, or the optimum is missing, non-finite, zero or -0.0', () => {
    const rule = { param: 'p_nom', unit: 'MW' } as const
    for (const o of [
      { p_nom: 10, p_nom_opt: 40 },
      { p_nom: 10, p_nom_opt: 40, p_nom_extendable: false },
      { p_nom: 10, p_nom_extendable: true },
      { p_nom: 10, p_nom_opt: null, p_nom_extendable: true },
      { p_nom: 10, p_nom_opt: Number.NaN, p_nom_extendable: true },
      { p_nom: 10, p_nom_opt: Infinity, p_nom_extendable: true },
      { p_nom: 10, p_nom_opt: 0, p_nom_extendable: true },
      { p_nom: 10, p_nom_opt: -0.0, p_nom_extendable: true }, // a myopic heat-pump Link
    ]) expect(sizeOf(c(o), rule, 'optimised')).toEqual({ amount: 10, unit: 'MW', optimised: false })
  })
  it('a StorageUnit\'s MWh is p_nom(_opt) × max_hours, with its MW beside it', () => {
    const rule = { param: 'mwh', unit: 'MWh' } as const
    const su = c({ p_nom: 10, p_nom_opt: 40, p_nom_extendable: true, max_hours: 4 })
    expect(sizeOf(su, rule, 'installed')).toEqual({ amount: 40, unit: 'MWh', mw: 10, optimised: false })
    expect(sizeOf(su, rule, 'optimised')).toEqual({ amount: 160, unit: 'MWh', mw: 40, optimised: true })
    expect(sizeOf(c({ p_nom: 10 }), rule, 'installed').amount).toBe(10) // max_hours absent → 1
  })
  it('a load is sized by |p_set| in either mode; none is 0', () => {
    expect(sizeOf(c({ p_set: -3 }), { param: 'p_set', unit: 'MW' }, 'optimised')).toEqual({ amount: 3, unit: 'MW', optimised: false })
    expect(sizeOf(c({}), { param: 'none', unit: '' }, 'optimised')).toEqual({ amount: 0, unit: '', optimised: false })
  })
})

describe('effectiveSizing', () => {
  it('is optimised only when the switch says so and dispatch is fresh', () => {
    expect(effectiveSizing('optimised', true)).toBe('optimised')
    expect(effectiveSizing('optimised', false)).toBe('installed') // stale after an edit
    expect(effectiveSizing('installed', true)).toBe('installed')
    expect(effectiveSizing('installed', false)).toBe('installed')
  })
})
