// The schematic's auto-layout (plan 1 A5): deterministic by name, buses in
// voltage-tier rows, no two buses overlapping after the push-apart, and
// satellites at their offsets from their bus.
import { describe, it, expect } from 'vitest'
import { BUS_MIN_DIST, LAYOUT_H, LAYOUT_W, hashStr, mulberry32, runLayout, tierOf } from './topologyLayout'

const bus = (id: string, v_nom: number) => ({ id, v_nom })

describe('hashStr / mulberry32', () => {
  it('are deterministic and spread names', () => {
    expect(hashStr('bus A')).toBe(hashStr('bus A'))
    expect(hashStr('bus A')).not.toBe(hashStr('bus B'))
    const r = mulberry32(hashStr('x'))
    const v = r()
    expect(v).toBeGreaterThanOrEqual(0)
    expect(v).toBeLessThan(1)
    expect(mulberry32(hashStr('x'))()).toBe(v)
  })
})

describe('runLayout', () => {
  const NET = [bus('HV1', 380), bus('HV2', 220), bus('MV1', 110), bus('MV2', 50), bus('LV1', 20), bus('LV2', 0.4)]

  it('is deterministic for the same input, and empty for no buses', () => {
    expect(runLayout(NET)).toEqual(runLayout(NET))
    expect(runLayout([])).toEqual({})
  })

  it('puts buses in tier rows: ≥ 220 kV on top, ≥ 50 kV in the middle, the rest below', () => {
    expect([380, 220, 219.9, 50, 49, 0.4].map(tierOf)).toEqual([0, 0, 1, 1, 2, 2])
    const pos = runLayout(NET)
    const rowY = (ids: string[]) => ids.map(id => pos[id].y)
    const top = rowY(['HV1', 'HV2']), mid = rowY(['MV1', 'MV2']), low = rowY(['LV1', 'LV2'])
    expect(Math.max(...top)).toBeLessThan(Math.min(...mid))
    expect(Math.max(...mid)).toBeLessThan(Math.min(...low))
    // a row is a row: the jitter is well inside the gap between tiers
    expect(Math.abs(top[0] - top[1])).toBeLessThan(80)
  })

  it('centres the buses on the canvas', () => {
    const pos = runLayout(NET)
    const xs = NET.map(b => pos[b.id].x), ys = NET.map(b => pos[b.id].y)
    expect((Math.min(...xs) + Math.max(...xs)) / 2).toBeCloseTo(LAYOUT_W / 2, 6)
    expect((Math.min(...ys) + Math.max(...ys)) / 2).toBeCloseTo(LAYOUT_H / 2, 6)
  })

  it('leaves no two buses overlapping after the push-apart', () => {
    // A canvas 100 px high puts the three tier rows 30 px apart, well inside
    // BUS_MIN_DIST, so the same column of each tier starts overlapping.
    const crowded = [bus('a', 380), bus('b', 110), bus('c', 20), bus('d', 380), bus('e', 110), bus('f', 20)]
    const pos = runLayout(crowded, [], 600, 100)
    const ids = crowded.map(b => b.id)
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = pos[ids[i]], b = pos[ids[j]]
        expect(Math.hypot(a.x - b.x, a.y - b.y), `${ids[i]}–${ids[j]}`).toBeGreaterThanOrEqual(BUS_MIN_DIST - 1e-6)
      }
    }
  })

  it('places satellites at their offset from their bus and skips one whose bus is unknown', () => {
    const pos = runLayout(NET, [
      { id: 'assetgrp-HV1-Load', busId: 'HV1', dx: -30, dy: 175 },
      { id: 'asset-Generator:PV', busId: 'MV1', dx: 0, dy: -190 },
      { id: 'asset-Load:Lost', busId: 'nope', dx: 1, dy: 1 },
    ])
    expect(pos['assetgrp-HV1-Load']).toEqual({ x: pos.HV1.x - 30, y: pos.HV1.y + 175 })
    expect(pos['asset-Generator:PV']).toEqual({ x: pos.MV1.x, y: pos.MV1.y - 190 })
    expect(pos['asset-Load:Lost']).toBeUndefined()
  })
})
