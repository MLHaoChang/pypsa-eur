// WP5 (plan Tasks 5.4 / 5.6): the pure geometry the site context renders
// through. `context.ts` imports three (ShapeUtils, BufferGeometry) and is
// SiteCanvas-only per the bundle guard; three imports cleanly under jsdom.
import { describe, it, expect } from 'vitest'
import {
  footprintToParts, buildingsGeometry, ribbonFor, linesToRibbons, areasGeometry,
  heightmapToDisplacement, groundHeightAt, groundMode, hillshade, RIBBON_WIDTH_M,
  densifyPolyline, subdivideTriangles, terrainCellMetres,
} from './context'
import type { ContextLine, SiteContext, TerrainGrid } from './types'

const origin = { lng: 6.835, lat: 53.435 }

function terrain(grid: number, heights: (i: number, j: number) => number): TerrainGrid {
  const heights_m: number[] = []
  for (let j = 0; j < grid; j++) for (let i = 0; i < grid; i++) heights_m.push(heights(i, j))
  // ~1 km × ~1 km around the origin.
  return { z: 12, grid, bbox: [6.8275, 53.4305, 6.8425, 53.4395], heights_m, source: 'terrarium', missing_tiles: 0 }
}

describe('footprintToParts', () => {
  it('extrudes a rectangle into a prism: 2 top triangles + 8 wall triangles, 8 vertices', () => {
    const p = footprintToParts([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 6 }, { x: 0, y: 6 }], 4)
    expect(p.positions.length / 3).toBe(8)
    expect(p.indices.length / 3).toBe(10)
    const ys = Array.from({ length: 8 }, (_, i) => p.positions[i * 3 + 1])
    expect(ys.filter(y => y === 4)).toHaveLength(4)
    expect(ys.filter(y => y === 0)).toHaveLength(4)
  })
  it('works for a clockwise ring too and refuses a degenerate one', () => {
    const cw = footprintToParts([{ x: 0, y: 6 }, { x: 10, y: 6 }, { x: 10, y: 0 }, { x: 0, y: 0 }], 4)
    expect(cw.indices.length / 3).toBe(10)
    expect(footprintToParts([{ x: 0, y: 0 }, { x: 10, y: 0 }], 4).indices).toHaveLength(0)
  })
  it('sits the prism on a base height', () => {
    const p = footprintToParts([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 6 }], 3, 2)
    const ys = Array.from({ length: 6 }, (_, i) => p.positions[i * 3 + 1])
    expect(Math.min(...ys)).toBe(2)
    expect(Math.max(...ys)).toBe(5)
  })
})

describe('buildingsGeometry', () => {
  it('merges every building into one flat-shaded geometry in the scene frame', () => {
    const g = buildingsGeometry([
      { id: 1, polygon: [[6.835, 53.435], [6.8352, 53.435], [6.8352, 53.4351], [6.835, 53.4351]], height_m: 5, height_source: 'default', tags: {} },
      { id: 2, polygon: [[6.836, 53.436], [6.8362, 53.436], [6.8362, 53.4361]], height_m: 8, height_source: 'tag', tags: {} },
    ], origin, () => 0)
    // Non-indexed so every face keeps its own normal (a box shaded smooth looks melted).
    expect(g.getIndex()).toBeNull()
    expect(g.getAttribute('position').count).toBe((10 + 7) * 3)
    expect(g.getAttribute('normal')).toBeTruthy()
    g.computeBoundingBox()
    const bb = g.boundingBox!
    // Scene frame: x = east, y = up from the ground, z = −north. Both
    // buildings lie east and north of the origin.
    expect(bb.min.y).toBe(0)
    expect(bb.max.y).toBe(8)
    expect(bb.min.x).toBeCloseTo(0, 6)
    expect(bb.max.z).toBeCloseTo(0, 6)
    expect(bb.min.z).toBeLessThan(-100)
    g.dispose()
  })
  it('lifts each building to the ground height under its centroid', () => {
    const g = buildingsGeometry([
      { id: 1, polygon: [[6.835, 53.435], [6.8352, 53.435], [6.8352, 53.4351]], height_m: 5, height_source: 'default', tags: {} },
    ], origin, () => 12)
    g.computeBoundingBox()
    expect(g.boundingBox!.min.y).toBe(12)
    expect(g.boundingBox!.max.y).toBe(17)
    g.dispose()
  })
})

describe('ribbons', () => {
  it('a two-point line is one quad (2 triangles) of the kind width', () => {
    const r = ribbonFor([{ x: 0, y: 0 }, { x: 100, y: 0 }], 6)
    expect(r.positions.length / 3).toBe(4)
    expect(r.indices.length / 3).toBe(2)
    const zs = [0, 1, 2, 3].map(i => r.positions[i * 3 + 2])
    expect(Math.max(...zs) - Math.min(...zs)).toBeCloseTo(6, 6)
  })
  it('groups by kind so each kind gets its own material, and skips the empty ones', () => {
    const lines: ContextLine[] = [
      { id: 1, kind: 'road', points: [[6.835, 53.435], [6.836, 53.435], [6.836, 53.436]], tags: {} },
      { id: 2, kind: 'road', points: [[6.834, 53.434], [6.835, 53.434]], tags: {} },
      { id: 3, kind: 'fence', points: [[6.834, 53.434], [6.835, 53.434]], tags: {} },
      { id: 4, kind: 'rail', points: [[6.834, 53.434]], tags: {} },
    ]
    const groups = linesToRibbons(lines, origin, () => 0)
    expect(groups.map(g => g.kind)).toEqual(['road', 'fence'])
    // 2 segments + 1 segment → 3 quads; the fence 1 quad.
    expect(groups[0].geometry.getIndex()!.count / 3).toBe(6)
    expect(groups[1].geometry.getIndex()!.count / 3).toBe(2)
    expect(RIBBON_WIDTH_M.road).toBeGreaterThan(RIBBON_WIDTH_M.fence)
    groups.forEach(g => g.geometry.dispose())
  })
})

describe('areasGeometry', () => {
  it('triangulates water and substation polygons flat on the ground, dropping landuse', () => {
    const groups = areasGeometry([
      { id: 1, kind: 'water', polygon: [[6.835, 53.435], [6.836, 53.435], [6.836, 53.436], [6.835, 53.436]], tags: {} },
      { id: 2, kind: 'landuse', polygon: [[6.835, 53.435], [6.836, 53.435], [6.836, 53.436]], tags: {} },
      { id: 3, kind: 'power_substation', polygon: [[6.835, 53.435], [6.836, 53.435], [6.836, 53.436]], tags: {} },
    ], origin, () => 0)
    expect(groups.map(g => g.kind)).toEqual(['water', 'power_substation'])
    expect(groups[0].geometry.getIndex()!.count / 3).toBe(2)
    expect(groups[1].geometry.getIndex()!.count / 3).toBe(1)
    groups.forEach(g => g.geometry.dispose())
  })
})

describe('draping', () => {
  it('densifyPolyline keeps every segment under the limit and is a no-op without one', () => {
    const line = [{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 30 }]
    expect(densifyPolyline(line, Infinity)).toBe(line)
    const dense = densifyPolyline(line, 20)
    expect(dense).toHaveLength(1 + 5 + 2)
    for (let i = 1; i < dense.length; i++) expect(Math.hypot(dense[i].x - dense[i - 1].x, dense[i].y - dense[i - 1].y)).toBeLessThanOrEqual(20 + 1e-9)
    expect(dense[dense.length - 1]).toEqual({ x: 100, y: 30 })
  })
  it('subdivideTriangles splits until every edge is under the limit, sharing midpoints', () => {
    const square = [{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }, { x: 0, y: 100 }]
    const tris = [[0, 1, 2], [0, 2, 3]]
    const { pts, tris: out } = subdivideTriangles(square, tris, 60)
    expect(out.length).toBeGreaterThan(2)
    for (const [a, b, c] of out) {
      for (const [i, j] of [[a, b], [b, c], [c, a]]) expect(Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y)).toBeLessThanOrEqual(60)
    }
    // The diagonal's midpoint is shared by both halves: fewer vertices than 3 × triangles.
    expect(pts.length).toBeLessThan(out.length * 3)
    expect(subdivideTriangles(square, tris, Infinity).tris).toBe(tris)
  })
  it('subdivideTriangles stops at the triangle cap', () => {
    const big = [{ x: 0, y: 0 }, { x: 5000, y: 0 }, { x: 5000, y: 5000 }]
    const { tris } = subdivideTriangles(big, [[0, 1, 2]], 1, 50)
    expect(tris.length).toBeLessThanOrEqual(53)
  })
  it('areas and ribbons densify to the terrain cell when asked', () => {
    const water = [{ id: 1, kind: 'water' as const, polygon: [[6.835, 53.435], [6.84, 53.435], [6.84, 53.44], [6.835, 53.44]] as [number, number][], tags: {} }]
    const coarse = areasGeometry(water, origin, () => 0)
    const fine = areasGeometry(water, origin, () => 0, 50)
    expect(fine[0].geometry.getIndex()!.count).toBeGreaterThan(coarse[0].geometry.getIndex()!.count)
    const road: ContextLine[] = [{ id: 1, kind: 'road', points: [[6.835, 53.435], [6.84, 53.435]], tags: {} }]
    expect(linesToRibbons(road, origin, () => 0, 50)[0].geometry.getIndex()!.count).toBeGreaterThan(linesToRibbons(road, origin, () => 0)[0].geometry.getIndex()!.count)
    expect(terrainCellMetres(terrain(64, () => 0))).toBeGreaterThan(10)
    expect(terrainCellMetres(terrain(64, () => 0))).toBeLessThan(30)
  })
})

describe('groundHeightAt', () => {
  it('is bilinear inside the grid', () => {
    // Height rises 1 m per column (west → east), 4 columns over the bbox width.
    const t = terrain(4, i => i)
    const lngW = t.bbox[0], lngE = t.bbox[2], lat = 53.435
    // The mid-longitude between columns 1 and 2 is 1.5 m.
    const midLng = lngW + ((lngE - lngW) * 1.5) / 3
    expect(groundHeightAt(t, { lng: midLng, lat })).toBeCloseTo(1.5, 6)
    expect(groundHeightAt(t, { lng: lngW, lat })).toBeCloseTo(0, 6)
    expect(groundHeightAt(t, { lng: lngE, lat })).toBeCloseTo(3, 6)
  })
  it('clamps outside the grid and reads rows north-first', () => {
    const t = terrain(3, (_i, j) => j * 10)  // row 0 (north) = 0, row 2 (south) = 20
    expect(groundHeightAt(t, { lng: 6.835, lat: 60 })).toBe(0)
    expect(groundHeightAt(t, { lng: 6.835, lat: 40 })).toBe(20)
    expect(groundHeightAt(t, { lng: 0, lat: t.bbox[3] })).toBe(0)
  })
})

describe('heightmapToDisplacement', () => {
  it('builds a (grid+1)² vertex ground over the extent with imagery UVs (v = 1 at the north edge)', () => {
    const t = terrain(4, (i, j) => (j === 0 ? 10 : 0) + i * 0)
    const extent = { west: -200, east: 200, south: -100, north: 100 }
    const g = heightmapToDisplacement(t, origin, extent, 0)
    expect(g.getAttribute('position').count).toBe(25)
    expect(g.getIndex()!.count / 3).toBe(4 * 4 * 2)
    const pos = g.getAttribute('position'), uv = g.getAttribute('uv')
    // First vertex: north-west corner.
    expect(pos.getX(0)).toBe(-200)
    expect(pos.getZ(0)).toBe(-100)
    expect(uv.getX(0)).toBe(0)
    expect(uv.getY(0)).toBe(1)
    // Heights are relative to `base`.
    const g2 = heightmapToDisplacement(t, origin, extent, 10)
    expect(g2.getAttribute('position').getY(0)).toBeCloseTo(pos.getY(0) - 10, 6)
    expect(g.getAttribute('normal')).toBeTruthy()
    g.dispose(); g2.dispose()
  })
})

describe('groundMode', () => {
  const ctx = (terrainGrid: TerrainGrid | null): SiteContext =>
    ({ version: 1, source: 'overpass', fetched_at: '', bbox: [0, 0, 1, 1], buildings: [], lines: [], areas: [], terrain: terrainGrid, attribution: [] })
  it('prefers imagery, then hillshade when there is terrain, then flat', () => {
    expect(groundMode(ctx(terrain(2, () => 0)), true)).toBe('imagery')
    expect(groundMode(ctx(terrain(2, () => 0)), false)).toBe('hillshade')
    expect(groundMode(ctx(null), false)).toBe('flat')
    expect(groundMode(null, false)).toBe('flat')
    expect(groundMode(null, true)).toBe('imagery')
  })
})

describe('hillshade', () => {
  it('is a grid² RGBA buffer where a slope facing the light is brighter than flat and one facing away is darker', () => {
    const flat = hillshade(terrain(8, () => 5), 100)
    expect(flat).toBeInstanceOf(Uint8ClampedArray)
    expect(flat.length).toBe(8 * 8 * 4)
    const px = (buf: Uint8ClampedArray, i: number, j: number) => buf[(j * 8 + i) * 4]
    expect(px(flat, 4, 4)).toBe(px(flat, 1, 1))
    expect(flat[3]).toBe(255)
    // Light from the north-west. Ground rising to the east has its surface
    // facing west, toward the light: brighter. Rising to the west: darker.
    const eastUp = hillshade(terrain(8, i => i * 30), 100)
    const westUp = hillshade(terrain(8, i => (7 - i) * 30), 100)
    expect(px(eastUp, 4, 4)).toBeGreaterThan(px(flat, 4, 4))
    expect(px(westUp, 4, 4)).toBeLessThan(px(flat, 4, 4))
  })
})
