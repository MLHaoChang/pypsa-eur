// Phase 2 plan Task 5.4 / spec §6.1: capacity denominators for the 3D
// view's results. Pure, no three.
import { describe, it, expect } from 'vitest'
import { capOf, periodEffectiveCap, periodAt, loadPeak, maxLoadScaler, type VintageResults } from './capacity'

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

describe('load peak', () => {
  it('the profile peak x the largest scaler, else |p_set| x the scaler, else null', () => {
    expect(loadPeak({ name: 'L', p_set: 3 }, { has_profile: true, peak: 20 } as never, 1.1)).toBeCloseTo(22)
    expect(loadPeak({ name: 'L', p_set: -3 }, { has_profile: false } as never, 1)).toBe(3)
    expect(loadPeak({ name: 'L', p_set: -3 }, undefined, 2)).toBe(6)
    expect(loadPeak({ name: 'L', p_set: 0 }, undefined, 1)).toBeNull()
  })
  it('the largest scaler over the legacy and per-carrier maps, never below 1', () => {
    expect(maxLoadScaler(undefined)).toBe(1)
    expect(maxLoadScaler({ load_scalers: { 2026: 1, 2030: 1.2 } } as never)).toBe(1.2)
    expect(maxLoadScaler({ load_scalers: { 2030: 0.8 }, load_scalers_by_carrier: { heat: { 2030: 1.5 } } } as never)).toBe(1.5)
    expect(maxLoadScaler({ load_scalers: { 2030: 0.8 } } as never)).toBe(1)
  })
})
