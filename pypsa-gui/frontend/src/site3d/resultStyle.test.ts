// Phase 2 plan Task 2.2 (and WP5 later): how an object glows. One place
// decides it, with a fixed precedence: selected > outside the boundary >
// hovered > outside the boundary > a result.
import { describe, it, expect } from 'vitest'
import { emissiveFor, visualFor, OUTSIDE_COLOR, HOVER_COLOR, type AssetState } from './resultStyle'
import { DEFAULT_LIBRARY } from './assetLibrary'
import { loadingColor, socColor } from '../components/CanvasResultsContext'

describe('emissiveFor', () => {
  it('nothing on: no glow', () => {
    expect(emissiveFor({ selected: false, hovered: false, outside: false }, '#7c3aed').intensity).toBe(0)
  })
  it('selected glows in the object\'s colour, over everything else', () => {
    expect(emissiveFor({ selected: true, hovered: true, outside: true }, '#7c3aed', 0.9)).toEqual({ color: '#7c3aed', intensity: 0.6 })
  })
  it('hovered glows warm, over the outside warning and a result (hover is transient; the label says "outside")', () => {
    expect(emissiveFor({ selected: false, hovered: true, outside: true }, '#7c3aed', 0.9)).toEqual({ color: HOVER_COLOR, intensity: 0.25 })
  })
  it('outside the boundary glows red, over a result', () => {
    expect(emissiveFor({ selected: false, hovered: false, outside: true }, '#7c3aed', 0.9)).toEqual({ color: OUTSIDE_COLOR, intensity: 0.45 })
  })
  it('a result glows in the object\'s colour, scaled and clamped', () => {
    expect(emissiveFor({ selected: false, hovered: false, outside: false }, '#16a34a', 0.5)).toEqual({ color: '#16a34a', intensity: 0.25 })
    expect(emissiveFor({ selected: false, hovered: false, outside: false }, '#16a34a', 7).intensity).toBe(0.5)
    expect(emissiveFor({ selected: false, hovered: false, outside: false }, '#16a34a', -1).intensity).toBe(0)
  })
})

// ── Result styling (Phase 2 plan Task 5.5, spec §6.2) ─────────────────────────
const T = (id: string) => DEFAULT_LIBRARY.find(t => t.id === id)!
const obj = (name: string, bus = 'B') => ({ name, bus })

describe('visualFor', () => {
  it('storage: gauge share, SoC band colour, charging/discharging, the capacity named', () => {
    const d = visualFor(T('bess'), { kind: 'storage', mw: 18.24, energy: 102.4, energyCap: 160 }, obj('BESS 1'))
    expect(d.fill).toBeCloseTo(0.64)
    expect(d.fillColor).toBe(socColor(64))
    expect(d.label).toBe('BESS 1 · discharging 18.2 MW · SoC 64 % of 160 MWh')
    const c = visualFor(T('bess'), { kind: 'storage', mw: -5, energy: 10, energyCap: 160 }, obj('BESS 1'))
    expect(c.label).toContain('charging 5.0 MW')
    expect(c.fillColor).toBe(socColor(6.25))
    const idle = visualFor(T('h2store'), { kind: 'storage', mw: 0, energy: 60, energyCap: 120 }, obj('H2 tanks'))
    expect(idle.label).toBe('H2 tanks · idle · fill 50 % of 120 MWh')
    const noCap = visualFor(T('store'), { kind: 'storage', mw: 1, energy: 42, energyCap: null }, obj('S'))
    expect(noCap.fill).toBeUndefined()
    expect(noCap.label).toBe('S · discharging 1.0 MW · 42.0 MWh stored')
  })
  it('wind: rotor speed = rated rpm x output share, 0 when not producing; no glow', () => {
    const rated = T('wind').geometry.params.ratedRpm as number
    const v = visualFor(T('wind'), { kind: 'output', mw: 7.5, cap: 15 }, obj('Onshore wind'))
    expect(v.spin).toBeCloseTo(rated * 0.5)
    expect(v.emissive).toBeUndefined()
    expect(v.label).toBe('Onshore wind · 7.5 MW · 50 % of 15 MW')
    expect(visualFor(T('wind'), { kind: 'output', mw: 0, cap: 15 }, obj('W')).spin).toBe(0)
    expect(visualFor(T('wind'), { kind: 'output', mw: -0.1, cap: 15 }, obj('W')).spin).toBe(0)
    expect(visualFor(T('wind'), { kind: 'output', mw: 20, cap: 15 }, obj('W')).spin).toBeCloseTo(rated)
  })
  it('other generators glow with their output share; no capacity → MW only, no glow', () => {
    const v = visualFor(T('pv'), { kind: 'output', mw: 12.3, cap: 20 }, obj('PV field'))
    expect(v.emissive).toBeCloseTo(0.615)
    expect(v.label).toBe('PV field · 12.3 MW · 62 % of 20 MW')
    const n = visualFor(T('pv'), { kind: 'output', mw: 3, cap: null }, obj('PV'))
    expect(n.emissive).toBeUndefined()
    expect(n.label).toBe('PV · 3.0 MW')
  })
  it('links (electrolyser, heat pump, CHP) glow with their share and say "MW in"', () => {
    const e = visualFor(T('electrolyser'), { kind: 'link', mw: 8.1, cap: 10 }, obj('Electrolyser'))
    expect(e.emissive).toBeCloseTo(0.81)
    expect(e.label).toBe('Electrolyser · 8.1 MW in · 81 % of 10 MW')
    expect(visualFor(T('heatPump'), { kind: 'link', mw: -2, cap: 10 }, obj('HP')).emissive).toBe(0)
  })
  it('loads glow with their share of the peak, and the label shows MW', () => {
    const l = visualFor(T('load'), { kind: 'load', mw: 4.2, peak: 7.6 }, obj('Data hall A'))
    expect(l.emissive).toBeCloseTo(4.2 / 7.6)
    expect(l.label).toBe('Data hall A · 4.2 MW · 55 % of 7.6 MW peak')
    expect(visualFor(T('load'), { kind: 'load', mw: 9, peak: 7.6 }, obj('L')).emissive).toBe(1)
    expect(visualFor(T('load'), { kind: 'load', mw: 2, peak: null }, obj('L')).label).toBe('L · 2.0 MW')
  })
  it('branches: loading band + percentage, chevrons downstream, the label names the destination', () => {
    const own0 = visualFor(T('feeder'), { kind: 'branch', p0: 45, cap: 120, unit: 'MVA', bus0: 'B', bus1: 'Grid' }, obj('L1', 'B'))
    expect(own0.color).toBe(loadingColor(37.5))
    expect(own0.flow).toEqual({ dir: 1, pct: 37.5 })
    expect(own0.label).toBe('L1 · 45.0 MW → Grid · 38 % of 120 MVA rating')
    // Owned through bus1, p0 > 0 (bus0 → bus1): flowing towards the yard.
    const own1 = visualFor(T('feeder'), { kind: 'branch', p0: 45, cap: 120, unit: 'MVA', bus0: 'Grid', bus1: 'B' }, obj('L1', 'B'))
    expect(own1.flow!.dir).toBe(-1)
    expect(own1.label).toContain('→ B ')
    const back = visualFor(T('transformer'), { kind: 'branch', p0: -60, cap: 50, unit: 'MVA', bus0: 'B', bus1: 'X' }, obj('TR1', 'B'))
    expect(back.flow).toEqual({ dir: -1, pct: 120 })                 // overloaded (ac_pf): not clamped
    expect(back.color).toBe(loadingColor(120))
    expect(back.label).toBe('TR1 · 60.0 MW → B · 120 % of 50 MVA rating')
    const dc = visualFor(T('feeder'), { kind: 'branch', p0: -10, cap: 20, unit: 'MW', bus0: 'B', bus1: 'Far' }, obj('DC', 'B'))
    expect(dc.flow!.dir).toBe(-1)                                     // a bidirectional DC link running backwards
    expect(dc.label).toBe('DC · 10.0 MW → B · 50 % of 20 MW rating')
    const idle = visualFor(T('feeder'), { kind: 'branch', p0: 0, cap: 20, unit: 'MW', bus0: 'B', bus1: 'Far' }, obj('I', 'B'))
    expect(idle.flow!.dir).toBe(0)
    const noCap = visualFor(T('feeder'), { kind: 'branch', p0: 5, cap: null, unit: 'MVA', bus0: 'B', bus1: 'Far' }, obj('N', 'B'))
    expect(noCap.flow).toEqual({ dir: 1, pct: null })
    expect(noCap.color).toBeUndefined()
    expect(noCap.label).toBe('N · 5.0 MW → Far')
  })
  it('every coloured state carries a number in its label', () => {
    const states: [string, AssetState][] = [
      ['bess', { kind: 'storage', mw: 1, energy: 5, energyCap: 10 }],
      ['feeder', { kind: 'branch', p0: 1, cap: 2, unit: 'MVA', bus0: 'B', bus1: 'C' }],
    ]
    for (const [id, st] of states) {
      const v = visualFor(T(id), st, obj('X'))
      if (v.color || v.fillColor) expect(v.label).toMatch(/\d+ %/)
    }
  })
})
