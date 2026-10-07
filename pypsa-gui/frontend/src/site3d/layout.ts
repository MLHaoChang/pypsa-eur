// Parametric site layout: PyPSA components at a site's buses → 3D objects.
//
// What each component looks like is decided by the asset library
// (assetLibrary.ts: match rules, size rules, templates, zones, labels,
// colours; spec §4). This module only interprets it: it matches every
// component attached to a member bus, runs the matched type's template,
// and shelf-packs the results around each bus's yard. It names no asset
// type and holds no number or colour of its own (guarded by
// templates.test.ts), so a new type or a regional norm is a data change.
//
// One yard per member bus at that bus's offset from the site origin; the
// bus's components packed around its yard; a placed object at its placement
// verbatim; rooftop PV on the largest data hall. Everything is in the SITE
// frame: metres east/north of the site origin, z up. Every part's position
// is the CENTRE of its part, relative to the object's own origin.
//
// Pure on purpose: no React, no three.js. The canvas maps `SiteObject`s to
// meshes and nothing more.

import type { Generator, Link, Line, Load, StorageUnit, Store, Transformer } from '../api/types'
import {
  DEFAULT_LIBRARY, validateLibrary, matchType, labelOf,
  type AssetType, type MatchComponent, type MatchContext, type MatchResult, type PyPSAClass, type Zone,
} from './assetLibrary'
import { runTemplate, type Anchors, type Part, type TemplateInput, type TemplateOutput } from './templates'
import { sizeOf, type SizingMode } from './sizing'

export type { Part, Anchors } from './templates'

/** An asset type id from the library (e.g. 'bess', 'switchyard'). */
export type SiteKind = string

export interface SiteObject {
  /** The PyPSA class, exactly as `setSelectedComponent` expects it. */
  type: 'Bus' | 'Generator' | 'StorageUnit' | 'Store' | 'Load' | 'Transformer' | 'Line' | 'Link'
  name: string
  /** The library type id. */
  kind: SiteKind
  /** The component's carrier (a Bus: the bus's). */
  carrier: string
  /** The member bus the object is drawn from (a Bus: itself). */
  bus: string
  /** Branches: the bus at the other end. */
  far?: string
  /** Object origin on the tangent plane, metres (east, north). */
  origin: [number, number]
  /** Degrees clockwise from north. Packed objects face north (0); a placement sets it. */
  heading: number
  /** Height of the object's base above the ground, metres (rooftop PV on a hall). */
  elevation?: number
  /** Footprint envelope (east extent, north extent), metres — what the packer packs. */
  footprint: [number, number]
  parts: Part[]
  color: string
  /** One line for the hover label, e.g. "Battery storage — 40 MWh in 10 containers". */
  summary: string
  /** Land the asset occupies, m² — the "does it fit" number (assessment Q1/Q6). */
  areaM2: number
  /** What the results layer animates (Phase 2 spec §4.3, §6.4). */
  anchors: Anchors
}

export interface SiteBusInput {
  name: string
  v_nom: number
  /** The bus's position in the SITE frame, metres east/north of the site origin. */
  offset: [number, number]
}

export interface SiteInput {
  /** Member buses in site order; the first is the primary bus. */
  buses: SiteBusInput[]
  generators: Generator[]
  storageUnits: StorageUnit[]
  stores: Store[]
  loads: Load[]
  transformers: Transformer[]
  lines: Line[]
  links: Link[]
  /** `"<Class>:<name>"` → where the user put it (site frame). Others are packed. */
  placements?: Record<string, { x: number; y: number; heading: number }>
  /** Every network bus's carrier (not only members'); unknown = AC. */
  busCarrier?: (name: string) => string | undefined
  /** The asset library; the default when absent. */
  library?: readonly AssetType[]
  /** Installed sizes (the default) or, for extendable assets, the optimum (spec E6). */
  sizing?: SizingMode
}

export interface SiteLayout {
  objects: SiteObject[]
  /** Half-size of the square (about the site origin) that contains everything, metres (min 250). */
  halfSizeM: number
  /** Centre of the objects' bounds, metres east/north of the site origin — where the camera looks. */
  centre: [number, number]
  /** The objects' bounds on the tangent plane, metres east/north of the site origin. */
  bounds: { x0: number; x1: number; y0: number; y1: number }
  /** Sum of every object's land take, m². */
  totalAreaM2: number
  /** Placement keys that name no object in this site (deleted or renamed elsewhere). Ignored, never pruned here. */
  orphans: string[]
  /** Keys of the objects that were placed (not packed). */
  placed: string[]
}

export const objectKey = (o: Pick<SiteObject, 'type' | 'name'>): string => `${o.type}:${o.name}`

// ── building objects ────────────────────────────────────────────────────────

interface Candidate { cls: PyPSAClass; comp: MatchComponent; m: MatchResult }

function objectFrom(cls: PyPSAClass, comp: MatchComponent, m: MatchResult, input: Omit<TemplateInput, 'amount'> = {}, sizing: SizingMode = 'installed'): { obj: SiteObject; out: TemplateOutput } {
  const t = m.type
  const size = sizeOf(comp, t.size[cls] ?? { param: 'none', unit: '' }, sizing)
  const out = runTemplate(t.geometry, { amount: size.amount, ...input })
  const carrier = (comp.carrier as string | undefined) ?? ''
  const obj: SiteObject = {
    type: cls, name: comp.name, kind: t.id, carrier, bus: m.owner, far: m.far,
    origin: [0, 0], heading: 0, footprint: out.footprint, parts: out.parts, color: t.color,
    summary: t.summary({
      cls, name: comp.name, carrier, amount: size.amount, unit: size.unit, count: out.count, each: out.each, areaM2: out.areaM2,
      mw: size.mw, vNom: input.vNom, vHi: comp.v_nom_0 as number | undefined, vLo: comp.v_nom_1 as number | undefined, far: m.far,
      params: t.geometry.params,
    }) + (size.optimised ? ' (optimised)' : ''),
    areaM2: out.areaM2, anchors: out.anchors,
  }
  return { obj, out }
}

/**
 * Shelf-pack objects in rows from `origin`: each row grows along `dirX`
 * (east or west), rows stack along `dirY` (north or south). Mutates each
 * object's `origin`.
 */
function packZone(objs: SiteObject[], origin: [number, number], maxRowWidth: number, dirY: 1 | -1, dirX: 1 | -1 = 1, gap = 8): void {
  let x = 0, y = 0, rowDepth = 0
  for (const o of objs) {
    const [w, d] = o.footprint
    if (x > 0 && x + w > maxRowWidth) { x = 0; y += rowDepth + gap; rowDepth = 0 }
    o.origin = [origin[0] + dirX * (x + w / 2), origin[1] + dirY * (y + d / 2)]
    x += w + gap
    rowDepth = Math.max(rowDepth, d)
  }
}

/** The objects attached to one bus, packed around its yard at `frameOrigin` (site frame). */
function buildBus(bus: SiteBusInput, candidates: Candidate[], ctx: MatchContext, lib: readonly AssetType[], frameOrigin: [number, number], roofAvailable: boolean, sizing: SizingMode): SiteObject[] {
  const mine = candidates.filter(c => c.m.owner === bus.name)
  const objs = mine.map(c => objectFrom(c.cls, c.comp, c.m, {}, sizing).obj)
  const bays = mine.filter(c => c.m.type.flags?.bay).length

  const yardMatch = matchType('Bus', { name: bus.name, carrier: ctx.busCarrier(bus.name) }, ctx, lib)!
  const yard = objectFrom('Bus', { name: bus.name, carrier: ctx.busCarrier(bus.name) ?? 'AC' }, yardMatch, { bays, vNom: bus.v_nom }).obj
  yard.carrier = ctx.busCarrier(bus.name) ?? 'AC'
  const [yw, yd] = yard.footprint

  // Pack each zone against the yard, in the yard's own frame, then shift
  // everything by the yard's position in the site frame. Big things (PV,
  // wind) go south so they never sit between the camera's default vantage
  // (south, looking north) and the yard. Rooftop PV is not packed when the
  // site has a data hall (the rooftop pass puts it on the roof — packing it
  // first would leave a hole in the south row); without one it stands on
  // its canopy in the south zone.
  const zoneOf = new Map(lib.map(t => [t.id, t.zone]))
  const zone = (o: SiteObject): Zone | null => {
    const z = zoneOf.get(o.kind) ?? 'south'
    return z === 'roof' ? (roofAvailable ? null : 'south') : z
  }
  const by = (z: Zone) => objs.filter(o => zone(o) === z)
  const M = 12 // margin from the yard
  packZone(by('north'), [-yw / 2, yd / 2 + M], yw, 1)
  packZone(by('west'), [-yw / 2 - M, -yd / 2], 80, 1, -1)
  packZone(by('east'), [yw / 2 + M, -yd / 2], 90, 1)
  packZone(by('northeast'), [yw / 2 + M, yd / 2 + M], 120, 1)
  packZone(by('south'), [-yw / 2, -yd / 2 - M], 600, -1)
  const all = [yard, ...objs]
  for (const o of all) o.origin = [o.origin[0] + frameOrigin[0], o.origin[1] + frameOrigin[1]]
  return all
}

/** Every component attached to a member bus, with its matched type and owner. */
function candidatesFor(input: SiteInput, ctx: MatchContext, lib: readonly AssetType[]): Candidate[] {
  const out: Candidate[] = []
  const add = (cls: PyPSAClass, rows: readonly object[]) => {
    for (const r of rows) {
      const comp = r as MatchComponent
      const m = matchType(cls, comp, ctx, lib)
      if (m) out.push({ cls, comp, m })
    }
  }
  add('Generator', input.generators)
  add('StorageUnit', input.storageUnits)
  add('Store', input.stores)
  add('Load', input.loads)
  add('Transformer', input.transformers)
  add('Line', input.lines)
  add('Link', input.links)
  return out
}

/**
 * Rooftop PV onto the largest data hall of the site (after placements, so it
 * follows a moved hall), re-run on the roof; without a hall it keeps its
 * canopy in the south zone. Exempt from the no-overlap rule: it is on the roof.
 */
function rooftopPass(objects: SiteObject[], candidates: Candidate[], lib: readonly AssetType[], sizing: SizingMode): void {
  const roofTypes = new Set(lib.filter(t => t.zone === 'roof').map(t => t.id))
  const hallType = lib.find(t => t.geometry.template === 'hall')?.id
  const halls = objects.filter(o => o.kind === hallType)
  if (!halls.length) return
  const hall = halls.reduce((a, b) => (b.areaM2 > a.areaM2 ? b : a))
  // The hall's body is its first part: its top is the roof. The rooftop
  // plant strip covers the band 0.15–0.35 of the depth north of centre, so
  // the panels take the southern part of the roof, 1 m in from every edge.
  const body = hall.parts[0]
  const [w, d] = hall.footprint
  const free = { w: w - 2, d: 0.65 * d - 2, cy: body.pos[1] + (-d / 2 + 1 + 0.15 * d - 1) / 2, h: body.pos[2] + body.size[2] / 2 }
  for (const o of objects) {
    if (!roofTypes.has(o.kind)) continue
    const c = candidates.find(x => x.cls === o.type && x.comp.name === o.name)
    if (!c) continue
    const { obj, out } = objectFrom(c.cls, c.comp, c.m, { roof: free }, sizing)
    if (out.fitsRoof) {
      Object.assign(o, { parts: obj.parts, footprint: obj.footprint, summary: obj.summary, origin: [...hall.origin], heading: hall.heading, elevation: free.h })
    } else {
      // Not one table fits the roof: the canopy stands just south of the hall.
      o.origin = [hall.origin[0], hall.origin[1] - d / 2 - o.footprint[1] / 2 - 8]
    }
  }
}

/**
 * Build the whole site: one yard per member bus at that bus's offset (or at
 * its placement, if the user moved the yard), the bus's components packed
 * around it, every placed object at its placement instead, rooftop PV on the
 * largest hall. Deterministic for a given input. Packed objects of different
 * buses may overlap each other and placed ones — the user resolves that by
 * moving things; Arrange only touches unplaced objects' positions.
 */
export function buildSiteLayout(input: SiteInput): SiteLayout {
  const lib = input.library ?? DEFAULT_LIBRARY
  validateLibrary(lib)
  const placements = input.placements ?? {}
  const sizing = input.sizing ?? 'installed'
  const members = input.buses.map(b => b.name)
  const ctx: MatchContext = { members, busCarrier: input.busCarrier ?? (() => undefined) }
  const candidates = candidatesFor(input, ctx, lib)
  const roofTypes = new Set(lib.filter(t => t.zone === 'roof').map(t => t.id))
  const hallType = lib.find(t => t.geometry.template === 'hall')?.id
  const roofAvailable = candidates.some(c => c.m.type.id === hallType)
  const objects: SiteObject[] = []
  const placed: string[] = []

  for (const bus of input.buses) {
    const yardKey = `Bus:${bus.name}`
    const yardPlacement = placements[yardKey]
    const frameOrigin: [number, number] = yardPlacement ? [yardPlacement.x, yardPlacement.y] : bus.offset
    for (const o of buildBus(bus, candidates, ctx, lib, frameOrigin, roofAvailable, sizing)) {
      const p = placements[objectKey(o)]
      if (p) {
        o.origin = [p.x, p.y]
        o.heading = p.heading
        placed.push(objectKey(o))
      }
      objects.push(o)
    }
  }
  rooftopPass(objects, candidates, lib, sizing)
  // A rooftop object that went onto a hall is not "placed" by its own entry.
  const onRoof = new Set(objects.filter(o => roofTypes.has(o.kind) && o.elevation).map(objectKey))
  const placedOut = placed.filter(k => !onRoof.has(k))

  const keys = new Set(objects.map(objectKey))
  const orphans = Object.keys(placements).filter(k => !keys.has(k))

  let reach = 250
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity
  for (const o of objects) {
    reach = Math.max(reach, Math.abs(o.origin[0]) + o.footprint[0] / 2, Math.abs(o.origin[1]) + o.footprint[1] / 2)
    x0 = Math.min(x0, o.origin[0] - o.footprint[0] / 2); x1 = Math.max(x1, o.origin[0] + o.footprint[0] / 2)
    y0 = Math.min(y0, o.origin[1] - o.footprint[1] / 2); y1 = Math.max(y1, o.origin[1] + o.footprint[1] / 2)
  }
  if (objects.length === 0) { x0 = x1 = y0 = y1 = 0 }
  return {
    objects,
    centre: [(x0 + x1) / 2, (y0 + y1) / 2],
    bounds: { x0, x1, y0, y1 },
    halfSizeM: Math.ceil(reach * 1.15 / 50) * 50,
    totalAreaM2: objects.reduce((a, o) => a + o.areaM2, 0),
    orphans,
    placed: placedOut,
  }
}

/** The label a library type shows for an object (its legend text). */
export function objectLabel(o: Pick<SiteObject, 'kind' | 'carrier'>, lib: readonly AssetType[] = DEFAULT_LIBRARY): string {
  const t = lib.find(x => x.id === o.kind)
  return t ? labelOf(t, o.carrier) : o.kind
}
