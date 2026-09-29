// Parametric site layout: PyPSA components at one bus → 3D objects.
//
// This is the "parameter → geometry" table from the feasibility assessment
// (docs/superpowers/assessments/2026-09-28-3d-site-view-feasibility.md §5),
// as code. Every asset is sized from its PyPSA parameters, not from a model
// file: a BESS is N containers from its MWh, a PV field is rows from its MWp,
// a turbine's tower from its MW. The scene therefore re-generates itself when
// the user edits a parameter in the properties panel — there is nothing else
// to keep in sync.
//
// Pure on purpose: no React, no three.js. The canvas maps `SiteObject`s to
// meshes and nothing more. Numbers below are 2025-era rules of thumb for a
// PLANNING view (how much land, how many boxes), not engineering dimensions;
// they belong in a data table once a second consumer appears (spec, phase 2).
//
// Positions are metres on the local tangent plane: x east, y north, z up.
// Every part's position is the CENTRE of its box, relative to the object's
// own origin, and the object's origin is placed by `packZone` below.

import { isRenewableCarrier } from '../utils/carriers'
import type { Generator, Link, Line, Load, StorageUnit, Store, Transformer } from '../api/types'

export type SiteKind =
  | 'switchyard' | 'transformer' | 'feeder'
  | 'thermal' | 'pv' | 'wind' | 'electrolyser'
  | 'bess' | 'h2store' | 'store'
  | 'load'

export interface Part {
  /** Centre of the box relative to the object origin, metres (east, north, up). */
  pos: [number, number, number]
  /** Box size (east extent, north extent, height), metres. */
  size: [number, number, number]
  /** Rotation about the vertical axis, radians, counter-clockwise from east. */
  rotZ?: number
  /** Rotation about the east axis, radians (tilted PV tables). */
  rotX?: number
  /** Rotation about the north axis, radians (turbine blades in the rotor plane). */
  rotN?: number
  /** Optional per-part colour override. */
  color?: string
}

export interface SiteObject {
  /** The PyPSA class, exactly as `setSelectedComponent` expects it. */
  type: 'Bus' | 'Generator' | 'StorageUnit' | 'Store' | 'Load' | 'Transformer' | 'Line' | 'Link'
  name: string
  kind: SiteKind
  /** Object origin on the tangent plane, metres (east, north). */
  origin: [number, number]
  /** Footprint envelope (east extent, north extent), metres — what `packZone` packs. */
  footprint: [number, number]
  parts: Part[]
  color: string
  /** One line for the hover label, e.g. "BESS — 40 MWh in 10 containers". */
  summary: string
  /** Land the asset occupies, m² — the "does it fit" number (assessment Q1/Q6). */
  areaM2: number
}

// Category colours match the map view's asset-group bubbles so a user moving
// between views reads the same colour as the same thing.
export const KIND_COLOR: Record<SiteKind, string> = {
  switchyard:   '#64748b',
  transformer:  '#2563eb',
  feeder:       '#475569',
  thermal:      '#dc2626',
  pv:           '#16a34a',
  wind:         '#15803d',
  electrolyser: '#0891b2',
  bess:         '#7c3aed',
  h2store:      '#0e7490',
  store:        '#6d28d9',
  load:         '#d97706',
}

export const KIND_LABEL: Record<SiteKind, string> = {
  switchyard: 'Switchyard', transformer: 'Transformer', feeder: 'Feeder bay',
  thermal: 'Thermal generation', pv: 'PV field', wind: 'Wind turbines',
  electrolyser: 'Electrolyser', bess: 'Battery storage', h2store: 'H₂ storage',
  store: 'Energy store', load: 'Load',
}

// ── Rules of thumb (planning-grade, 2025) ─────────────────────────────────────
const CONTAINER_20FT: [number, number, number] = [6.1, 2.44, 2.9]
const CONTAINER_40FT: [number, number, number] = [12.2, 2.44, 2.9]
const MWH_PER_BESS_CONTAINER = 4          // 3–5 MWh per 20 ft in 2025 products
const MW_PER_GENSET_ENCLOSURE = 2.5       // reciprocating / small GT in 40 ft
const MW_PER_ELECTROLYSER_CONTAINER = 5   // PEM/alkaline 40 ft skid
const MWH_PER_H2_BULLET = 20              // ≈ 600 kg H₂ at 33.3 MWh/t
const HA_PER_MWP_PV = 2.5                 // ground-mount, fixed tilt
const MW_PER_TURBINE = 5
const DATAHALL_M2_PER_MW = 800            // IT MW → gross floor area, single storey
const GAP = 1.5                           // aisle between containers, metres
const PV_ROW_PITCH = 6
const PV_ROW_LEN = 30

/** How many of a thing, never fewer than one (a 0 MW placeholder still shows a box). */
const count = (amount: number, per: number): number => Math.max(1, Math.ceil(amount / per))

/** Lay `n` identical boxes on a grid, `perRow` across, returning centred parts. */
function gridParts(n: number, size: [number, number, number], perRow: number, gap = GAP): { parts: Part[]; footprint: [number, number] } {
  const cols = Math.min(n, perRow)
  const rows = Math.ceil(n / perRow)
  const pitchX = size[0] + gap
  const pitchY = size[1] + gap
  const w = cols * pitchX - gap
  const d = rows * pitchY - gap
  const parts: Part[] = []
  for (let i = 0; i < n; i++) {
    const c = i % perRow, r = Math.floor(i / perRow)
    parts.push({
      pos: [-w / 2 + size[0] / 2 + c * pitchX, -d / 2 + size[1] / 2 + r * pitchY, size[2] / 2],
      size,
    })
  }
  return { parts, footprint: [w, d] }
}

// Generous cap so a 1 GWh store does not become 250 draw calls; beyond it,
// each box stands for several. The summary line still reports the real count.
const MAX_PARTS = 120
function capped(n: number): { drawn: number; each: number } {
  if (n <= MAX_PARTS) return { drawn: n, each: 1 }
  const each = Math.ceil(n / MAX_PARTS)
  return { drawn: Math.ceil(n / each), each }
}

const fmt = (v: number, unit: string) => `${Number.isInteger(v) ? v : v.toFixed(1)} ${unit}`

// ── Per-class builders ────────────────────────────────────────────────────────

function bessObject(type: 'StorageUnit' | 'Store', name: string, mwh: number, mw: number | null): SiteObject {
  const n = count(mwh, MWH_PER_BESS_CONTAINER)
  const { drawn, each } = capped(n)
  const g = gridParts(drawn, CONTAINER_20FT, 8)
  // A PCS/inverter skid per row, in front of it.
  const rows = Math.ceil(drawn / 8)
  for (let r = 0; r < rows; r++) {
    g.parts.push({ pos: [-g.footprint[0] / 2 - 3, -g.footprint[1] / 2 + 1.22 + r * (CONTAINER_20FT[1] + GAP), 1.2], size: [2.4, 2.4, 2.4], color: '#a78bfa' })
  }
  g.footprint[0] += 6
  return {
    type, name, kind: 'bess', origin: [0, 0], footprint: g.footprint, parts: g.parts,
    color: KIND_COLOR.bess,
    summary: `${KIND_LABEL.bess} — ${fmt(mwh, 'MWh')}${mw != null ? ` / ${fmt(mw, 'MW')}` : ''} in ${n} container${n === 1 ? '' : 's'}${each > 1 ? ` (each box = ${each})` : ''}`,
    areaM2: g.footprint[0] * g.footprint[1],
  }
}

function h2StoreObject(name: string, mwh: number): SiteObject {
  const n = count(mwh, MWH_PER_H2_BULLET)
  const { drawn, each } = capped(n)
  const g = gridParts(drawn, [3, 20, 3], 6, 2)
  return {
    type: 'Store', name, kind: 'h2store', origin: [0, 0], footprint: g.footprint, parts: g.parts,
    color: KIND_COLOR.h2store,
    summary: `${KIND_LABEL.h2store} — ${fmt(mwh, 'MWh')} in ${n} bullet tank${n === 1 ? '' : 's'}${each > 1 ? ` (each = ${each})` : ''}`,
    areaM2: g.footprint[0] * g.footprint[1],
  }
}

function genericStoreObject(name: string, carrier: string, mwh: number): SiteObject {
  // A tank whose volume grows with the energy — anything not battery or H₂.
  const side = Math.max(4, Math.cbrt(Math.max(mwh, 1)) * 2)
  return {
    type: 'Store', name, kind: 'store', origin: [0, 0], footprint: [side, side],
    parts: [{ pos: [0, 0, side / 2], size: [side, side, side] }],
    color: KIND_COLOR.store,
    summary: `${KIND_LABEL.store} (${carrier || 'unknown carrier'}) — ${fmt(mwh, 'MWh')}`,
    areaM2: side * side,
  }
}

function pvObject(name: string, mw: number): SiteObject {
  const areaM2 = Math.max(mw, 0.05) * HA_PER_MWP_PV * 10_000
  // Square-ish field: rows of PV_ROW_LEN across, PV_ROW_PITCH apart.
  const width = Math.max(PV_ROW_LEN, Math.sqrt(areaM2))
  const tablesPerRow = Math.max(1, Math.round(width / (PV_ROW_LEN + 2)))
  const rows = Math.max(1, Math.round(areaM2 / (tablesPerRow * PV_ROW_LEN * PV_ROW_PITCH)))
  const n = rows * tablesPerRow
  const { drawn, each } = capped(n)
  const g = gridParts(drawn, [PV_ROW_LEN, 2.2, 0.1], tablesPerRow, 2)
  // Tilt every table 25° toward the equator (south in the northern hemisphere;
  // the sign is cosmetic for a planning view). Lift so the low edge clears ground.
  for (const p of g.parts) { p.rotX = 25 * Math.PI / 180; p.pos[2] = 1.2 }
  g.footprint[1] = Math.max(g.footprint[1], rows * PV_ROW_PITCH)
  return {
    type: 'Generator', name, kind: 'pv', origin: [0, 0], footprint: g.footprint, parts: g.parts,
    color: KIND_COLOR.pv,
    summary: `${KIND_LABEL.pv} — ${fmt(mw, 'MW')} on ~${(areaM2 / 10_000).toFixed(1)} ha${each > 1 ? ` (each table = ${each})` : ''}`,
    areaM2,
  }
}

function windObject(name: string, mw: number): SiteObject {
  const n = count(mw, MW_PER_TURBINE)
  const hub = 100, rotor = 140
  const spacing = rotor * 4      // ~4 D in-row is the low end of practice
  const perRow = Math.min(n, 4)
  const parts: Part[] = []
  const cols = perRow, rows = Math.ceil(n / perRow)
  const w = (cols - 1) * spacing, d = (rows - 1) * spacing
  for (let i = 0; i < n; i++) {
    const c = i % perRow, r = Math.floor(i / perRow)
    const x = -w / 2 + c * spacing, y = -d / 2 + r * spacing
    parts.push({ pos: [x, y, hub / 2], size: [4, 4, hub], color: '#e5e7eb' })                 // tower
    parts.push({ pos: [x, y - 3, hub], size: [4, 10, 4], color: '#e5e7eb' })                  // nacelle
    for (let b = 0; b < 3; b++) {                                                             // blades, rotor facing south
      const a = b * (2 * Math.PI / 3)
      // A blade is a box along east, rotated about north by `a`, its centre a
      // quarter-rotor out from the hub along the same angle.
      parts.push({ pos: [x + Math.cos(a) * rotor / 4, y - 8, hub + Math.sin(a) * rotor / 4], size: [rotor / 2, 0.5, 3], rotN: a, color: '#f3f4f6' })
    }
  }
  const footprint: [number, number] = [w + rotor, d + rotor]
  return {
    type: 'Generator', name, kind: 'wind', origin: [0, 0], footprint, parts,
    color: KIND_COLOR.wind,
    summary: `${KIND_LABEL.wind} — ${fmt(mw, 'MW')} as ${n} × ${MW_PER_TURBINE} MW, ${hub} m hub, ${rotor} m rotor`,
    areaM2: footprint[0] * footprint[1],
  }
}

function thermalObject(name: string, carrier: string, mw: number): SiteObject {
  const n = count(mw, MW_PER_GENSET_ENCLOSURE)
  const { drawn, each } = capped(n)
  const g = gridParts(drawn, CONTAINER_40FT, 6, 3)
  // One exhaust stack per enclosure row.
  const rows = Math.ceil(drawn / 6)
  for (let r = 0; r < rows; r++) {
    g.parts.push({ pos: [g.footprint[0] / 2 + 2, -g.footprint[1] / 2 + 1.22 + r * (CONTAINER_40FT[1] + 3), 10], size: [1.5, 1.5, 20], color: '#9ca3af' })
  }
  g.footprint[0] += 4
  return {
    type: 'Generator', name, kind: 'thermal', origin: [0, 0], footprint: g.footprint, parts: g.parts,
    color: KIND_COLOR.thermal,
    summary: `${KIND_LABEL.thermal} (${carrier || 'unknown carrier'}) — ${fmt(mw, 'MW')} in ${n} enclosure${n === 1 ? '' : 's'}${each > 1 ? ` (each box = ${each})` : ''}`,
    areaM2: g.footprint[0] * g.footprint[1],
  }
}

function electrolyserObject(name: string, mw: number): SiteObject {
  const n = count(mw, MW_PER_ELECTROLYSER_CONTAINER)
  const { drawn, each } = capped(n)
  const g = gridParts(drawn, CONTAINER_40FT, 4, 3)
  // Compressor / balance-of-plant building alongside.
  g.parts.push({ pos: [0, g.footprint[1] / 2 + 8, 3], size: [Math.max(15, g.footprint[0]), 10, 6], color: '#67e8f9' })
  g.footprint[1] += 16
  return {
    type: 'Link', name, kind: 'electrolyser', origin: [0, 0], footprint: g.footprint, parts: g.parts,
    color: KIND_COLOR.electrolyser,
    summary: `${KIND_LABEL.electrolyser} — ${fmt(mw, 'MW')} in ${n} skid${n === 1 ? '' : 's'}${each > 1 ? ` (each box = ${each})` : ''}`,
    areaM2: g.footprint[0] * g.footprint[1],
  }
}

function loadObject(name: string, carrier: string, mw: number): SiteObject {
  const areaM2 = Math.max(200, Math.abs(mw) * DATAHALL_M2_PER_MW)
  const w = Math.sqrt(areaM2 * 1.6), d = areaM2 / w, h = 12
  return {
    type: 'Load', name, kind: 'load', origin: [0, 0], footprint: [w, d],
    parts: [
      { pos: [0, 0, h / 2], size: [w, d, h] },
      // Rooftop plant strip so it reads as a building, not a slab.
      { pos: [0, d / 4, h + 1.5], size: [w * 0.6, d * 0.2, 3], color: '#fbbf24' },
    ],
    color: KIND_COLOR.load,
    summary: `${KIND_LABEL.load} (${carrier || 'electricity'}) — ${fmt(mw, 'MW')} peak, ~${Math.round(areaM2)} m² hall`,
    areaM2,
  }
}

function transformerObject(name: string, mva: number, vHi: number | null | undefined, vLo: number | null | undefined): SiteObject {
  // Tank grows with the cube root of rating: 10 MVA ≈ 5 m, 400 MVA ≈ 12 m.
  const s = Math.max(1, Math.cbrt(Math.max(mva, 1)))
  const tank: [number, number, number] = [2.3 * s, 1.4 * s, 1.6 * s]
  return {
    type: 'Transformer', name, kind: 'transformer', origin: [0, 0], footprint: [tank[0] + 4, tank[1] + 4],
    parts: [
      { pos: [0, 0, tank[2] / 2], size: tank },
      { pos: [-tank[0] / 2 - 0.6, 0, tank[2] / 2], size: [1.2, tank[1] * 0.8, tank[2] * 0.9], color: '#93c5fd' }, // radiators
      { pos: [tank[0] / 2 + 0.6, 0, tank[2] / 2], size: [1.2, tank[1] * 0.8, tank[2] * 0.9], color: '#93c5fd' },
      { pos: [-tank[0] / 4, 0, tank[2] + 1], size: [0.4, 0.4, 2], color: '#e5e7eb' },  // bushings
      { pos: [0, 0, tank[2] + 1], size: [0.4, 0.4, 2], color: '#e5e7eb' },
      { pos: [tank[0] / 4, 0, tank[2] + 1], size: [0.4, 0.4, 2], color: '#e5e7eb' },
    ],
    color: KIND_COLOR.transformer,
    summary: `${KIND_LABEL.transformer} — ${fmt(mva, 'MVA')}${vHi && vLo ? ` ${vHi}/${vLo} kV` : ''}`,
    areaM2: (tank[0] + 4) * (tank[1] + 4),
  }
}

function feederObject(type: 'Line' | 'Link', name: string, other: string, rating: number, unit: string): SiteObject {
  return {
    type, name, kind: 'feeder', origin: [0, 0], footprint: [6, 4],
    parts: [
      { pos: [0, 0, 0.15], size: [6, 4, 0.3], color: '#94a3b8' },          // bay slab
      { pos: [-2, 0, 3], size: [0.3, 0.3, 6] }, { pos: [2, 0, 3], size: [0.3, 0.3, 6] }, // gantry legs
      { pos: [0, 0, 6], size: [4.6, 0.3, 0.3] },                            // beam
    ],
    color: KIND_COLOR.feeder,
    summary: `${type} to ${other} — ${fmt(rating, unit)}`,
    areaM2: 24,
  }
}

function switchyardObject(bus: string, vNom: number, bays: number): SiteObject {
  const w = Math.max(30, 12 + bays * 8), d = Math.max(20, 10 + Math.min(vNom, 400) / 10)
  return {
    type: 'Bus', name: bus, kind: 'switchyard', origin: [0, 0], footprint: [w, d],
    parts: [
      { pos: [0, 0, 0.15], size: [w, d, 0.3], color: '#cbd5e1' },              // gravel pad
      { pos: [0, d / 4, 5], size: [w - 4, 0.4, 0.4], color: '#e5e7eb' },       // busbar
      { pos: [0, -d / 4, 5], size: [w - 4, 0.4, 0.4], color: '#e5e7eb' },
      { pos: [-w / 2 + 2, 0, 2.5], size: [0.4, d - 4, 5], color: '#e5e7eb' },  // end gantries
      { pos: [w / 2 - 2, 0, 2.5], size: [0.4, d - 4, 5], color: '#e5e7eb' },
    ],
    color: KIND_COLOR.switchyard,
    summary: `${vNom} kV switchyard — bus ${bus}`,
    areaM2: w * d,
  }
}

// ── Classification ────────────────────────────────────────────────────────────

const isH2 = (c: string) => /\b(h2|hydrogen)\b/i.test(c)
const isBattery = (c: string) => /batter|li-?ion|bess/i.test(c)
const isElectrolyser = (c: string) => /electroly/i.test(c)
const isPv = (c: string) => /solar|pv|rooftop/i.test(c)
const isWind = (c: string) => /wind/i.test(c)

// ── Zones and packing ─────────────────────────────────────────────────────────

export interface SiteInput {
  bus: { name: string; v_nom: number }
  generators: Generator[]
  storageUnits: StorageUnit[]
  stores: Store[]
  loads: Load[]
  transformers: Transformer[]
  lines: Line[]
  links: Link[]
}

// Where each kind goes relative to the switchyard, and which way the shelf
// grows. Big things (PV, wind) go south so they never sit between the camera's
// default vantage (south-west, looking north-east) and the switchyard.
type Zone = 'north' | 'east' | 'south' | 'west' | 'northeast'
const ZONE_OF: Record<SiteKind, Zone> = {
  switchyard: 'north', transformer: 'north', feeder: 'north',
  thermal: 'west', electrolyser: 'west',
  bess: 'east', store: 'east', h2store: 'east',
  load: 'northeast',
  pv: 'south', wind: 'south',
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

export interface SiteLayout {
  objects: SiteObject[]
  /** Half-size of the square (about the bus) that contains everything, metres (min 250). */
  halfSizeM: number
  /** Centre of the packed objects' bounds, metres east/north of the bus — where the camera looks. */
  centre: [number, number]
  /** The packed objects' bounds on the tangent plane, metres east/north of the bus. */
  bounds: { x0: number; x1: number; y0: number; y1: number }
  /** Sum of every object's land take, m². */
  totalAreaM2: number
}

/** Build the whole site for one bus. Deterministic for a given input. */
export function buildSiteLayout(input: SiteInput): SiteLayout {
  const bus = input.bus.name
  const objs: SiteObject[] = []

  for (const g of input.generators) {
    if (g.bus !== bus) continue
    const c = g.carrier ?? ''
    if (isPv(c)) objs.push(pvObject(g.name, g.p_nom))
    else if (isWind(c)) objs.push(windObject(g.name, g.p_nom))
    else if (isRenewableCarrier(c)) objs.push(pvObject(g.name, g.p_nom)) // ror/geothermal: shown as a field for now
    else objs.push(thermalObject(g.name, c, g.p_nom))
  }
  for (const s of input.storageUnits) {
    if (s.bus !== bus) continue
    objs.push(bessObject('StorageUnit', s.name, s.p_nom * (s.max_hours || 1), s.p_nom))
  }
  for (const s of input.stores) {
    if (s.bus !== bus) continue
    const c = s.carrier ?? ''
    if (isH2(c)) objs.push(h2StoreObject(s.name, s.e_nom))
    else if (isBattery(c)) objs.push(bessObject('Store', s.name, s.e_nom, null))
    else objs.push(genericStoreObject(s.name, c, s.e_nom))
  }
  for (const l of input.loads) {
    if (l.bus !== bus) continue
    objs.push(loadObject(l.name, l.carrier ?? '', l.p_set))
  }
  for (const t of input.transformers) {
    if (t.bus0 !== bus && t.bus1 !== bus) continue
    objs.push(transformerObject(t.name, t.s_nom, t.v_nom_0, t.v_nom_1))
  }
  for (const l of input.lines) {
    if (l.bus0 !== bus && l.bus1 !== bus) continue
    objs.push(feederObject('Line', l.name, l.bus0 === bus ? l.bus1 : l.bus0, l.s_nom, 'MVA'))
  }
  for (const l of input.links) {
    if (l.bus0 !== bus && l.bus1 !== bus) continue
    if (l.bus0 === bus && isElectrolyser(l.carrier ?? '')) objs.push(electrolyserObject(l.name, l.p_nom))
    else objs.push(feederObject('Link', l.name, l.bus0 === bus ? l.bus1 : l.bus0, l.p_nom, 'MW'))
  }

  const bays = objs.filter(o => o.kind === 'feeder' || o.kind === 'transformer').length
  const yard = switchyardObject(bus, input.bus.v_nom, bays)
  const [yw, yd] = yard.footprint

  // Pack each zone against the switchyard.
  const by = (z: Zone) => objs.filter(o => ZONE_OF[o.kind] === z)
  const M = 12 // margin from the yard
  packZone(by('north'), [-yw / 2, yd / 2 + M], yw, 1)
  packZone(by('west'), [-yw / 2 - M, -yd / 2], 80, 1, -1)
  packZone(by('east'), [yw / 2 + M, -yd / 2], 90, 1)
  packZone(by('northeast'), [yw / 2 + M, yd / 2 + M], 120, 1)
  packZone(by('south'), [-yw / 2, -yd / 2 - M], 600, -1)
  const objects = [yard, ...objs]
  let reach = 250
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity
  for (const o of objects) {
    reach = Math.max(reach, Math.abs(o.origin[0]) + o.footprint[0] / 2, Math.abs(o.origin[1]) + o.footprint[1] / 2)
    x0 = Math.min(x0, o.origin[0] - o.footprint[0] / 2); x1 = Math.max(x1, o.origin[0] + o.footprint[0] / 2)
    y0 = Math.min(y0, o.origin[1] - o.footprint[1] / 2); y1 = Math.max(y1, o.origin[1] + o.footprint[1] / 2)
  }
  return {
    objects,
    centre: [(x0 + x1) / 2, (y0 + y1) / 2],
    bounds: { x0, x1, y0, y1 },
    halfSizeM: Math.ceil(reach * 1.15 / 50) * 50,
    totalAreaM2: objects.reduce((a, o) => a + o.areaM2, 0),
  }
}
