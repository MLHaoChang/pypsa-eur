import { describe, expect, it } from 'vitest'
import { buildSiteLayout, type SiteInput } from './layout'
import type { Generator } from '../api/types'

const input: SiteInput = {
  buses: [{ name: 'B', v_nom: 33, offset: [0, 0] }],
  generators: [{ name: 'G', bus: 'B', carrier: 'gas', p_nom: 10 } as Generator],
  storageUnits: [], stores: [], loads: [], transformers: [], lines: [], links: [],
}

describe('optional asset footprint in the actual site layout', () => {
  it('uses one exact user envelope for proxy, packing and land area without changing native data', () => {
    const before = structuredClone(input)
    const result = buildSiteLayout({ ...input,
      footprints: { 'Generator:G': { width_m: 12, depth_m: 6, height_m: 3 } },
    })
    const asset = result.objects.find(o => o.name === 'G')!
    expect(asset.footprint).toEqual([12, 6])
    expect(asset.areaM2).toBe(72)
    expect(asset.parts).toEqual([{ pos: [0, 0, 1.5], size: [12, 6, 3], color: asset.color }])
    expect(asset.footprintSource).toBe('user')
    expect(asset.summary).toContain('user-entered envelope')
    expect(input).toEqual(before)
  })

  it('preserves the existing output exactly when no override is supplied', () => {
    expect(buildSiteLayout({ ...input, footprints: {} })).toEqual(buildSiteLayout(input))
  })

  it('does not silently scale explicit dimensions when optimised capacity changes', () => {
    const sized = { ...input, generators: [{ ...input.generators[0], p_nom_extendable: true, p_nom_opt: 50 }],
      footprints: { 'Generator:G': { width_m: 12, depth_m: 6 } } }
    const a = buildSiteLayout(sized).objects.find(o => o.name === 'G')!
    const b = buildSiteLayout({ ...sized, sizing: 'optimised' }).objects.find(o => o.name === 'G')!
    expect(a.footprint).toEqual([12, 6])
    expect(b.footprint).toEqual([12, 6])
    expect(a.areaM2).toBe(72)
    expect(b.areaM2).toBe(72)
  })

  it('refuses invalid override geometry instead of rendering a malformed mesh', () => {
    expect(() => buildSiteLayout({ ...input,
      footprints: { 'Generator:G': { width_m: -12, depth_m: 6 } },
    })).toThrow('Width, depth and height must be positive numbers of metres, up to 100000 m.')
  })
})

it('uses the same envelope for rotated boundary geometry', async () => {
  const { footprintCorners } = await import('./fit')
  const asset = buildSiteLayout({ ...input,
    placements: { 'Generator:G': { x: 0, y: 0, heading: 90 } },
    footprints: { 'Generator:G': { width_m: 12, depth_m: 6 } },
  }).objects.find(o => o.name === 'G')!
  const corners = footprintCorners(asset)
  expect(Math.min(...corners.map(p => p.x))).toBeCloseTo(-3, 10)
  expect(Math.max(...corners.map(p => p.x))).toBeCloseTo(3, 10)
  expect(Math.min(...corners.map(p => p.y))).toBeCloseTo(-6, 10)
  expect(Math.max(...corners.map(p => p.y))).toBeCloseTo(6, 10)
})

for (const heading of [0, 90]) it(`keeps an explicit PV proxy clear of rooftop plant at heading ${heading}`, () => {
  const result = buildSiteLayout({ ...input,
    loads: [{name: 'hall', bus: 'B', carrier: 'AC', p_set: 20} as never],
    generators: [{name: 'roof', bus: 'B', carrier: 'solar-rooftop', p_nom: 1} as Generator],
    placements: {'Load:hall': {x: 100, y: 200, heading}},
    footprints: {'Generator:roof': {width_m: 12, depth_m: 60, height_m: 1}},
  })
  const hall = result.objects.find(o => o.name === 'hall')!
  const pv = result.objects.find(o => o.name === 'roof')!
  expect(hall.footprint).toEqual([160, 100])
  expect(pv.elevation).toBeGreaterThan(0)
  // Southern free roof spans y=-49..14 in the hall frame. A 60m proxy
  // centered at -17.5 spans -47.5..12.5 and stays clear of plant y=15..35.
  expect(pv.origin[0]).toBeCloseTo(heading === 0 ? 100 : 82.5, 10)
  expect(pv.origin[1]).toBeCloseTo(heading === 0 ? 182.5 : 200, 10)
  expect(pv.parts[0].pos).toEqual([0, 0, 0.5])
  expect(pv.parts[0].size).toEqual([12, 60, 1])
  expect(pv.areaM2).toBe(0)
})
