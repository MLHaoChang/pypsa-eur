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
    })).toThrow(/footprint/i)
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
