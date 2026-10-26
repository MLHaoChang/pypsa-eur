// User-entered total equipment envelopes, independent of electrical ratings.
import type { AssetFootprint } from './types'
import type { TemplateOutput } from './templates'

export const FOOTPRINT_CLASSES = new Set(['Generator', 'StorageUnit', 'Store', 'Transformer', 'Link'])
export const MAX_FOOTPRINT_DIMENSION_M = 100_000

export function assertFootprintValid(key: string, value: AssetFootprint): void {
  const [cls, ...name] = key.split(':')
  if (!FOOTPRINT_CLASSES.has(cls) || !name.join(':')) throw new Error('Footprint requires an equipment Class:name')
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Footprint requires dimensions')
  const dims = [value.width_m, value.depth_m, ...('height_m' in value ? [value.height_m] : [])]
  if (!dims.every(v => typeof v === 'number' && Number.isFinite(v) && v > 0 && v <= MAX_FOOTPRINT_DIMENSION_M)) {
    throw new Error(`Width, depth and height must be positive numbers of metres, up to ${MAX_FOOTPRINT_DIMENSION_M} m.`)
  }
}

/** A truthful simple proxy; never non-uniformly stretch vendor/hero geometry. */
export function footprintGeometry(value: AssetFootprint, base: TemplateOutput, color: string): TemplateOutput {
  const w = value.width_m, d = value.depth_m
  const h = value.height_m ?? Math.max(0.1, ...base.parts.map(p => p.pos[2] + p.size[2] / 2))
  return {
    ...base, footprint: [w, d], areaM2: w * d,
    parts: [{ pos: [0, 0, h / 2], size: [w, d, h], color }],
    anchors: {
      emissive: true,
      ...(base.anchors.fill ? { fill: { pos: [0, 0, h], size: [w, Math.min(d, 0.2), 0.1] as [number, number, number] } } : {}),
      ...(base.anchors.flow ? { flow: { from: [0, -d / 2, h / 2] as [number, number, number], to: [0, d / 2, h / 2] as [number, number, number] } } : {}),
    },
  }
}
