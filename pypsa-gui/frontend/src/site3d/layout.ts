// Parametric site layout: PyPSA components at a site's buses → 3D objects.
//
// What each component looks like is decided by the asset library
// (assetLibrary.ts: match rules, size rules, templates, zones, placement
// rules, labels, colours; spec §4). This module only interprets it: it
// matches every component attached to a member bus, runs the matched type's
// template, and packs the results around each bus's yard by the type's
// placement rule. It names no asset type and holds no number or colour of
// its own (guarded by templates.test.ts; the packer's numbers are
// `PACKING` in the library), so a new type or a regional norm is a data
// change.
//
// One yard per member bus at that bus's offset from the site origin. Then
// (S2, owner decision O1): anchored objects first — a `between` object at
// the midpoint of the segment from its owner yard's edge to its far bus's
// yard edge, turned to face the far bus — then objects that want a
// neighbour, at the nearest free slot around it, then the rest shelf-packed
// into their zones as in Phase 1. Clearance and keep-out are hard
// constraints throughout: an object that would breach one is shifted along
// its row (then away from the yard) until it does not, within a bounded
// reach; past that reach it stays put and is named in `unresolved`. A
// placed object is drawn at its placement verbatim and is an obstacle to
// everything packed. Rooftop PV goes on the largest data hall. Everything
// is in the SITE frame: metres east/north of the site origin, z up. Every
// part's position is the CENTRE of its part, relative to the object's own
// origin.
//
// Pure on purpose: no React, no three.js. The canvas maps `SiteObject`s to
// meshes and nothing more.

import type { Generator, Link, Line, Load, StorageUnit, Store, Transformer } from '../api/types'
import {
  DEFAULT_LIBRARY, PACKING, validateLibrary, matchType, labelOf,
  type AssetType, type MatchComponent, type MatchContext, type MatchResult, type PlacementRule, type PyPSAClass, type Zone,
} from './assetLibrary'
import { runTemplate, type Anchors, type Part, type TemplateInput, type TemplateOutput } from './templates'
import { sizeOf, type SizingMode } from './sizing'
import {
  rectOf, rectSize, rectCentre, rectsOverlap, rectDistance, bearingDeg, betweenSegment, pairRequirement, ruleLookup,
  type Rect, type XY,
} from './placementCheck'

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
  /** Keys of packed objects whose clearance or keep-out the packer could not satisfy within its reach (S2). */
  unresolved: string[]
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

/** The yard (switchyard or manifold) of a member bus, at `origin`, with as many bays as the bus has branches. */
function yardFor(bus: SiteBusInput, candidates: Candidate[], ctx: MatchContext, lib: readonly AssetType[], origin: XY): SiteObject {
  const mine = candidates.filter(c => c.m.owner === bus.name)
  const bays = mine.filter(c => c.m.type.flags?.bay).length
  const carrier = ctx.busCarrier(bus.name) ?? 'AC'
  const yardMatch = matchType('Bus', { name: bus.name, carrier: ctx.busCarrier(bus.name) }, ctx, lib)!
  const yard = objectFrom('Bus', { name: bus.name, carrier }, yardMatch, { bays, vNom: bus.v_nom }).obj
  yard.carrier = carrier
  yard.origin = origin
  return yard
}

// ── the packer ──────────────────────────────────────────────────────────────

/**
 * Everything the packer knows while it places one site: the objects already
 * standing (yards, placed objects, and whatever it has packed so far — the
 * obstacles), each type's rule, and where it could not satisfy a rule.
 */
class Packer {
  readonly fixed: SiteObject[] = []
  readonly unresolved: string[] = []
  private readonly rects = new Map<SiteObject, Rect>()
  constructor(private readonly ruleOf: (kind: string) => PlacementRule | undefined, readonly yards: Map<string, SiteObject>) {}

  /** The object stands here from now on: an obstacle to everything packed after it. */
  fix(o: SiteObject): void { this.fixed.push(o); this.rects.set(o, rectOf(o)) }
  rect(o: SiteObject): Rect { return this.rects.get(o) ?? rectOf(o) }

  /** The heading an object's rule asks for at `at`: towards its far bus's yard when it has one, else north. */
  headingFor(o: SiteObject, at: XY): number {
    const far = o.far ? this.yards.get(o.far) : undefined
    return this.ruleOf(o.kind)?.orientation === 'faceFar' && far ? bearingDeg(at, far.origin) : 0
  }

  /** East/north extents of the object's footprint once turned to its heading at `at` — what packs. */
  extents(o: SiteObject, at: XY): XY {
    return rectSize(rectOf({ origin: [0, 0], footprint: o.footprint, heading: this.headingFor(o, at) }))
  }

  /** Would the object, standing at `at`, overlap or come closer than a rule allows to anything fixed? */
  violates(o: SiteObject, at: XY): boolean {
    const r = rectOf({ origin: at, footprint: o.footprint, heading: this.headingFor(o, at) })
    const rule = this.ruleOf(o.kind)
    for (const f of this.fixed) {
      if (f === o) continue
      const fr = this.rect(f)
      if (rectsOverlap(r, fr)) return true
      const req = pairRequirement(o.kind, rule, f.kind, this.ruleOf(f.kind))
      const need = Math.max(req.keepOutM, req.clearanceM)
      if (need > 0 && rectDistance(r, fr) < need) return true
    }
    return false
  }

  /**
   * Stand the object at `start`, or at the nearest place along one of the
   * shift directions where nothing is violated (one step further out each
   * round, every direction in turn, so the smallest shift wins). Past the
   * reach the object stays at `start` and is named in `unresolved`. Fixes
   * the object. Returns where it stands.
   */
  settle(o: SiteObject, start: XY, dirs: XY[]): XY {
    const at = this.firstFree(o, start, dirs)
    if (!at) this.unresolved.push(objectKey(o))
    const final = at ?? start
    o.origin = final
    o.heading = this.headingFor(o, final)
    this.fix(o)
    return final
  }

  /** The first candidate (start, then shifted) that violates nothing; null when none does within reach. */
  firstFree(o: SiteObject, start: XY, dirs: XY[]): XY | null {
    if (!this.violates(o, start)) return start
    const step = PACKING.shiftStepM, max = PACKING.maxShiftSteps
    for (let k = 1; k <= max; k++) {
      for (const dir of dirs) {
        const p: XY = [start[0] + dir[0] * k * step, start[1] + dir[1] * k * step]
        if (!this.violates(o, p)) return p
      }
    }
    return null
  }

  /**
   * Shelf-pack objects in rows from `origin` (site frame): each row grows
   * along `dirX` (east or west), rows stack along `dirY` (north or south).
   * An object that would breach a rule is shifted on along its row, then
   * along the row's depth, and the row continues from where it stands.
   */
  packZone(objs: SiteObject[], origin: XY, maxRowWidth: number, dirY: 1 | -1, dirX: 1 | -1 = 1): void {
    const gap = PACKING.gapM
    let x = 0, y = 0, rowDepth = 0
    for (const o of objs) {
      const [w, d] = this.extents(o, origin)
      if (x > 0 && x + w > maxRowWidth) { x = 0; y += rowDepth + gap; rowDepth = 0 }
      const start: XY = [origin[0] + dirX * (x + w / 2), origin[1] + dirY * (y + d / 2)]
      const final = this.settle(o, start, [[dirX, 0], [0, dirY]])
      x = dirX * (final[0] - origin[0]) + w / 2 + gap
      rowDepth = Math.max(rowDepth, d + Math.abs(final[1] - start[1]))
    }
  }

  /**
   * A `between` object: at the midpoint of the segment from its owner yard's
   * edge to its far yard's edge, shifted across the segment (alternately to
   * either side) when that spot is taken. A `far` object: just beyond the far
   * yard's edge on the same line. False when the far bus has no yard here or
   * the yards touch — the caller packs it in its zone instead.
   */
  anchor(o: SiteObject): boolean {
    const owner = this.yards.get(o.bus), far = o.far ? this.yards.get(o.far) : undefined
    if (!owner || !far) return false
    const seg = betweenSegment(this.rect(owner), this.rect(far))
    if (!seg) return false
    const perp: XY = [-seg.dir[1], seg.dir[0]]
    const rule = this.ruleOf(o.kind)
    let start: XY
    if (rule?.anchor === 'far') {
      const [w, d] = this.extents(o, seg.b)
      const half = (w / 2) * Math.abs(seg.dir[0]) + (d / 2) * Math.abs(seg.dir[1]) + PACKING.gapM
      // Past the far yard's near edge, so it stands beside the far yard, not on its approach.
      const fc = rectCentre(this.rect(far)), [fw, fd] = rectSize(this.rect(far))
      const across = (fw / 2) * Math.abs(seg.dir[0]) + (fd / 2) * Math.abs(seg.dir[1])
      start = [fc[0] + seg.dir[0] * (across + half), fc[1] + seg.dir[1] * (across + half)]
    } else {
      start = [(seg.a[0] + seg.b[0]) / 2, (seg.a[1] + seg.b[1]) / 2]
    }
    this.settle(o, start, [perp, [-perp[0], -perp[1]]])
    return true
  }

  /**
   * An object that wants a neighbour: the nearest free slot (one gap away,
   * on any side, sliding along that side) around the fixed objects of the
   * named types that belong to its bus, the types in the rule's order.
   * False when no such neighbour stands yet or no slot is free.
   */
  adjacent(o: SiteObject): boolean {
    const wanted = this.ruleOf(o.kind)?.adjacentTo ?? []
    const yard = this.yards.get(o.bus)
    const home: XY = yard ? yard.origin : [0, 0]
    const dist = (a: XY, b: XY) => Math.hypot(a[0] - b[0], a[1] - b[1])
    const gap = PACKING.gapM, step = PACKING.shiftStepM, max = PACKING.maxShiftSteps
    for (const kind of wanted) {
      const neighbours = this.fixed
        .filter(f => f !== o && f.kind === kind && (f.bus === o.bus || f.far === o.bus))
        .sort((a, b) => dist(a.origin, home) - dist(b.origin, home))
      for (const n of neighbours) {
        const nr = this.rect(n)
        const [cx, cy] = rectCentre(nr)
        const [w, d] = this.extents(o, [cx, cy])
        // Four slots, each with the direction it slides along; the nearest to the yard first.
        const slot = (at: XY, along: XY) => ({ at, along })
        const slots = [
          slot([nr.x1 + gap + w / 2, cy], [0, 1]),
          slot([nr.x0 - gap - w / 2, cy], [0, 1]),
          slot([cx, nr.y1 + gap + d / 2], [1, 0]),
          slot([cx, nr.y0 - gap - d / 2], [1, 0]),
        ].sort((a, b) => dist(a.at, home) - dist(b.at, home))
        for (let k = 0; k <= max; k++) {
          for (const s of slots) {
            for (const sign of k === 0 ? [1] : [1, -1]) {
              const p: XY = [s.at[0] + s.along[0] * sign * k * step, s.at[1] + s.along[1] * sign * k * step]
              if (!this.violates(o, p)) { this.settle(o, p, []); return true }
            }
          }
        }
      }
    }
    return false
  }
}

/** The zone an object is packed in; null for rooftop PV when a hall will take it (the rooftop pass places it). */
function zoneFor(lib: readonly AssetType[], roofAvailable: boolean): (o: SiteObject) => Zone | null {
  const zoneOf = new Map(lib.map(t => [t.id, t.zone]))
  return o => {
    const z = zoneOf.get(o.kind) ?? 'south'
    return z === 'roof' ? (roofAvailable ? null : 'south') : z
  }
}

/**
 * Shelf-pack one bus's remaining objects into their zones against its yard.
 * Big things (PV, wind) go south so they never sit between the camera's
 * default vantage (south, looking north) and the yard. Rooftop PV is not
 * packed when the site has a data hall (the rooftop pass puts it on the
 * roof — packing it first would leave a hole in the south row); without one
 * it stands on its canopy in the south zone.
 */
function packZones(packer: Packer, yard: SiteObject, objs: SiteObject[], zone: (o: SiteObject) => Zone | null): void {
  const [ox, oy] = yard.origin
  const [yw, yd] = rectSize(packer.rect(yard))
  const by = (z: Zone) => objs.filter(o => zone(o) === z)
  const M = PACKING.marginM
  packer.packZone(by('north'), [ox - yw / 2, oy + yd / 2 + M], yw, 1)
  packer.packZone(by('west'), [ox - yw / 2 - M, oy - yd / 2], 80, 1, -1)
  packer.packZone(by('east'), [ox + yw / 2 + M, oy - yd / 2], 90, 1)
  packer.packZone(by('northeast'), [ox + yw / 2 + M, oy + yd / 2 + M], 120, 1)
  packer.packZone(by('south'), [ox - yw / 2, oy - yd / 2 - M], 600, -1)
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
 * its placement, if the user moved the yard); every placed object at its
 * placement; then the unplaced objects by their rules — anchored ones on
 * their inter-yard segment, neighbour-seeking ones beside their neighbour,
 * the rest shelf-packed into their zones — never overlapping or breaching a
 * clearance or keep-out against anything already standing, where the
 * packer's reach allows (else `unresolved`); rooftop PV on the largest hall.
 * Deterministic for a given input. Arrange only touches unplaced objects'
 * positions.
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
  const zone = zoneFor(lib, roofAvailable)
  const ruleOf = ruleLookup(lib)
  const placed: string[] = []

  // Yards first: everything else is placed relative to them.
  const yards = new Map<string, SiteObject>()
  for (const bus of input.buses) {
    const p = placements[`Bus:${bus.name}`]
    const yard = yardFor(bus, candidates, ctx, lib, p ? [p.x, p.y] : bus.offset)
    if (p) { yard.heading = p.heading; placed.push(objectKey(yard)) }
    yards.set(bus.name, yard)
  }
  const packer = new Packer(ruleOf, yards)
  for (const yard of yards.values()) packer.fix(yard)

  // The bus's objects, in site order: yard, then its components in list order.
  const objects: SiteObject[] = []
  const pending: SiteObject[] = []
  for (const bus of input.buses) {
    objects.push(yards.get(bus.name)!)
    for (const c of candidates.filter(c => c.m.owner === bus.name)) {
      const o = objectFrom(c.cls, c.comp, c.m, {}, sizing).obj
      objects.push(o)
      const p = placements[objectKey(o)]
      if (p) {
        o.origin = [p.x, p.y]
        o.heading = p.heading
        placed.push(objectKey(o))
        packer.fix(o)
      } else if (zone(o) !== null) {
        // Rooftop PV bound for a hall is not packed (the rooftop pass places it).
        pending.push(o)
      }
    }
  }

  // Anchors, then neighbours, then the zones — each pass over every bus, so
  // a neighbour owned by a later bus is standing before an earlier bus's
  // objects look for it.
  const done = new Set<SiteObject>()
  for (const o of pending) {
    const a = ruleOf(o.kind)?.anchor
    if ((a === 'between' || a === 'far') && packer.anchor(o)) done.add(o)
  }
  for (const o of pending) {
    if (!done.has(o) && ruleOf(o.kind)?.adjacentTo?.length && packer.adjacent(o)) done.add(o)
  }
  for (const bus of input.buses) {
    packZones(packer, yards.get(bus.name)!, pending.filter(o => !done.has(o) && o.bus === bus.name), zone)
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
    unresolved: packer.unresolved,
  }
}

/** The label a library type shows for an object (its legend text). */
export function objectLabel(o: Pick<SiteObject, 'kind' | 'carrier'>, lib: readonly AssetType[] = DEFAULT_LIBRARY): string {
  const t = lib.find(x => x.id === o.kind)
  return t ? labelOf(t, o.carrier) : o.kind
}
