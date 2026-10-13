// Phase 2 plan Task 5.4 / spec §6.1: capacity denominators for the 3D
// view's results. Pure, no three.
import { describe, it, expect } from 'vitest'
import { capOf, periodEffectiveCap, periodAt, loadPeak, type VintageResults } from './capacity'

describe('capOf', () => {
  it('*_nom_opt when finite and > 0, else *_nom when > 0, else null', () => {
    expect(capOf({ p_nom: 10, p_nom_opt: 40 }, 'p_nom')).toBe(40)
    expect(capOf({ p_nom: 10, p_nom_opt: 0 }, 'p_nom')).toBe(10)
    expect(capOf({ p_nom: 10, p_nom_opt: -0.0 }, 'p_nom')).toBe(10)
    expect(capOf({ p_nom: 10, p_nom_opt: Number.NaN }, 'p_nom')).toBe(10)
    expect(capOf({ p_nom: 10, p_nom_opt: Infinity }, 'p_nom')).toBe(10)
    expect(capOf({ p_nom: 10 }, 'p_nom')).toBe(10)
    expect(capOf({ p_nom: 0 }, 'p_nom')).toBeNull()
    expect(capOf({}, 'p_nom')).toBeNull()
    expect(capOf({ e_nom: 5, e_nom_opt: 9 }, 'e_nom')).toBe(9)
    expect(capOf({ s_nom: 100, s_nom_opt: 0 }, 's_nom')).toBe(100)
  })
})

describe('periodEffectiveCap (moved from CanvasResultsContext)', () => {
  const vr: VintageResults = { Generator: { Solar2: { initial_capacity: 300, periods: [{ build_year: 2028, p_nom_opt: 293 }] } } }
  it('counts a vintage only from its build year', () => {
    expect(periodEffectiveCap(vr, 'Generator', 'Solar2', 2026, 999)).toBe(300)
    expect(periodEffectiveCap(vr, 'Generator', 'Solar2', 2028, 999)).toBe(593)
    expect(periodEffectiveCap(vr, 'Generator', 'Solar2', 2030, 999)).toBe(593)
  })
  it('single-period (no period): every vintage on top of the initial capacity', () => {
    expect(periodEffectiveCap(vr, 'Generator', 'Solar2', null, 999)).toBe(593)
  })
  it('no vintage entry: the fallback', () => {
    expect(periodEffectiveCap(vr, 'Generator', 'Other', 2028, 42)).toBe(42)
    expect(periodEffectiveCap(undefined, 'Generator', 'Solar2', 2028, 42)).toBe(42)
  })
})

describe('periodAt', () => {
  it('reads the period of a chunk-local row, null for a flat payload or a row outside', () => {
    const p = { index: ['a', 'b'], columns: [], data: [[], []], periods: [2026, 2028], range: { from: 10, to: 11, total: 20 } }
    expect(periodAt(p, 1)).toBe(2028)
    expect(periodAt(p, 2)).toBeNull()
    expect(periodAt({ ...p, periods: undefined }, 0)).toBeNull()
    expect(periodAt(null, 0)).toBeNull()
  })
})

describe('load peak (as the solver scales demand: demand.py load_scale_factors)', () => {
  const cfg = { multi_investment_periods: true, load_scalers: { 2030: 1.25 }, load_scalers_by_carrier: { heat: { 2030: 3 } } }
  const P = [2026, 2030]
  it('a profile load: its profile peak x the largest factor over the periods for its carrier', () => {
    expect(loadPeak({ name: 'L', carrier: 'AC', p_set: 3 }, { has_profile: true, peak: 20 } as never, cfg as never, P)).toBeCloseTo(25)   // legacy 1.25
    expect(loadPeak({ name: 'H', carrier: 'heat', p_set: 3 }, { has_profile: true, peak: 20 } as never, cfg as never, P)).toBeCloseTo(60)  // heat 3
  })
  it('periods without a factor count as 1; all factors below 1 lower the peak', () => {
    expect(loadPeak({ name: 'L', carrier: 'AC' }, { has_profile: true, peak: 20 } as never, { ...cfg, load_scalers: { 2030: 0.8 }, load_scalers_by_carrier: {} } as never, P)).toBe(20)
    expect(loadPeak({ name: 'L', carrier: 'AC' }, { has_profile: true, peak: 20 } as never, { ...cfg, load_scalers: { 2026: 0.8, 2030: 0.9 }, load_scalers_by_carrier: {} } as never, P)).toBeCloseTo(18)
  })
  it('no scaling unless multi-period, and never for a static p_set (the solver scales only the time series)', () => {
    expect(loadPeak({ name: 'L', carrier: 'AC' }, { has_profile: true, peak: 20 } as never, { ...cfg, multi_investment_periods: false } as never, P)).toBe(20)
    expect(loadPeak({ name: 'L', carrier: 'AC', p_set: -3 }, { has_profile: false } as never, cfg as never, P)).toBe(3)
    expect(loadPeak({ name: 'L', carrier: 'AC', p_set: -3 }, undefined, cfg as never, P)).toBe(3)
    expect(loadPeak({ name: 'L', carrier: 'AC', p_set: 0 }, undefined, undefined, P)).toBeNull()
  })
})
