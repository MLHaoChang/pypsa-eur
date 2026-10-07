// The fit check (assessment Q1/Q6, design D9): does the plan fit the plot,
// and which assets stick out of it. Positions feed nothing else.
//
// Pure; main bundle; no `three`. Everything is in the site frame (metres
// east/north of the site origin).

import { pointInPolygonLocal, polygonAreaM2, boundaryToLocal } from './boundary'
import type { LocalXY } from './geo'
import type { Site } from './types'

export interface FitObject {
  /** The `"<Class>:<name>"` key, for the report. */
  key: string
  /** Centre in the site frame, metres. */
  origin: [number, number]
  /** Footprint (east extent, north extent) before rotation, metres. */
  footprint: [number, number]
  /** Degrees clockwise from north. */
  heading: number
  areaM2: number
}

export interface FitReport {
  landM2: number
  plotM2: number
  over: boolean
  /** Keys of objects with at least one footprint corner outside the boundary. */
  outside: string[]
}

/** The four footprint corners after rotating by `heading` about the origin. */
export function footprintCorners(o: Pick<FitObject, 'origin' | 'footprint' | 'heading'>): LocalXY[] {
  const [cx, cy] = o.origin
  const hw = o.footprint[0] / 2, hd = o.footprint[1] / 2
  // Clockwise-from-north heading is a NEGATIVE mathematical angle (east = +x, north = +y).
  const a = (-o.heading * Math.PI) / 180
  const cos = Math.cos(a), sin = Math.sin(a)
  return ([[-hw, hd], [hw, hd], [hw, -hd], [-hw, -hd]] as const).map(([x, y]) => ({
    x: cx + x * cos - y * sin,
    y: cy + x * sin + y * cos,
  }))
}

export function fitReport(objects: FitObject[], site: Site): FitReport {
  const poly = boundaryToLocal(site.boundary, site.origin)
  const plotM2 = polygonAreaM2(site.boundary)
  const landM2 = objects.reduce((a, o) => a + o.areaM2, 0)
  const outside = objects
    .filter(o => footprintCorners(o).some(c => !pointInPolygonLocal(c, poly)))
    .map(o => o.key)
  return { landM2, plotM2, over: landM2 > plotM2, outside }
}

export const formatHa = (m2: number): string => `${(m2 / 10_000).toFixed(1)} ha`
