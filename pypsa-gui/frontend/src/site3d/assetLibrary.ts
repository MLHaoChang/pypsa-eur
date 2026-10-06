// The 3D site view's asset library (Phase 2 spec §4, plan Tasks 1.1–1.2;
// visual-layers plan 3 S1).
//
// One table says what every PyPSA component looks like on a site. WHICH type
// a component is — the match rules, label, colour and icon — lives in
// utils/assetTypes.ts, shared with the schematic and the map so one
// component is one type in every view. This module adds the 3D half per
// type: how it is sized (which parameter, what one unit is worth), which
// template draws it and with what numbers and colours, where it is packed,
// and how it is summarised. `layout.ts` packs whatever this table produces
// and names no asset type itself, so adding or re-tuning a type is a data
// change.
//
// Pure: no React, no three.js (main-bundle-safe, though only the lazy
// SiteCanvas chunk imports it today).

import type { Geometry, ParamValue, TemplateId } from './templates'
import { TEMPLATE_IDS } from './templates'
import { HERO_IDS, type HeroModel } from './heroes'
import {
  ASSET_TYPES, deepFreeze, labelOf, matchType as matchTypeIn, validateAssetTypes,
  type AssetTypeSpec, type MatchComponent, type MatchContext, type MatchResult as MatchResultOf, type PyPSAClass,
} from '../utils/assetTypes'

export type { Match, MatchComponent, MatchContext, Port, PyPSAClass } from '../utils/assetTypes'
export { labelOf, legendFor, type LegendEntry } from '../utils/assetTypes'

export type Zone = 'north' | 'west' | 'east' | 'northeast' | 'south' | 'roof'

/** Which parameter sizes a component of a class, and in what unit. `mwh` = p_nom × max_hours. */
export type SizeParam = 'p_nom' | 'e_nom' | 's_nom' | 'p_set' | 'mwh' | 'none'
export interface SizeRule { param: SizeParam; unit: 'MW' | 'MWh' | 'MVA' | '' }

export interface SummaryInfo {
  cls: PyPSAClass
  name: string
  carrier: string
  /** The size driver's value and unit. */
  amount: number
  unit: string
  /** Real unit count and how many each drawn unit stands for. */
  count: number
  each: number
  areaM2: number
  /** A StorageUnit's power next to its energy. */
  mw?: number
  vNom?: number
  vHi?: number | null
  vLo?: number | null
  /** Branches: the bus at the other end. */
  far?: string
  /** The entry's template numbers (unit ratings, hub height, …), so a summary never hard-codes them. */
  params: Record<string, ParamValue>
}

/**
 * Where an object's origin wants to be (visual-layers plan 3 S2, owner
 * decision O1): in its owner bus's yard zone (the default), on the segment
 * between the owner's yard and the far bus's yard (branches; falls back to
 * `yard` when the far bus is not a site member), or at the far side.
 */
export type PlacementAnchor = 'yard' | 'between' | 'far'
/** Which way the object's front (+north in its own frame) turns: towards its far bus, north, or anywhere. */
export type PlacementOrientation = 'faceFar' | 'north' | 'any'

/**
 * How a type is placed relative to the others. Rules are data: the packer
 * (layout.ts) honours them and names no type itself, the checker
 * (placementCheck.ts) reports where a layout breaks them. Distances are
 * between footprints, metres.
 */
export interface PlacementRule {
  anchor?: PlacementAnchor
  /** Library type ids this type should sit next to (greedy nearest free slot around them, in this order). */
  adjacentTo?: string[]
  /** Minimum distance from any other object's footprint. */
  clearanceM?: number
  /** Minimum distance from objects of the named types (`'*'` = every type), e.g. gensets from halls. */
  keepOutM?: Array<{ from: string[]; m: number }>
  orientation?: PlacementOrientation
}

/** What the 3D view adds to a shared asset type. */
export interface SiteAppearance {
  zone: Zone
  size: Partial<Record<PyPSAClass, SizeRule>>
  geometry: Geometry
  summary: (s: SummaryInfo) => string
  flags?: { bay?: boolean; infrastructure?: boolean }
  /** A hero model that stands in for this type's heroable units (spec §5); absent = parametric only. */
  hero?: HeroModel['id']
  /** How it is placed relative to the other objects; absent = packed in its zone, facing north. */
  placement?: PlacementRule
}

/**
 * The packer's own numbers (S2), here so layout.ts holds none: the gap
 * between packed footprints and the margin around a yard (Phase 1's 8 m and
 * 12 m), how far and in what steps an object is shifted to satisfy a rule
 * before the packer gives up and reports it, and the tolerances the checker
 * allows a `between` object off its segment and a `faceFar` object off its
 * bearing.
 */
export const PACKING = Object.freeze({
  gapM: 8,
  marginM: 12,
  shiftStepM: 4,
  maxShiftSteps: 150,
  betweenToleranceM: 10,
  facingToleranceDeg: 15,
})

/** A library entry: the shared type (id, label, colour, icon, match rules) plus its 3D appearance. */
export type AssetType = AssetTypeSpec & SiteAppearance

// ── helpers for the summaries ───────────────────────────────────────────────

const fmt = (v: number, unit: string) => `${Number.isInteger(v) ? v : v.toFixed(1)} ${unit}`
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
const eachBox = (each: number, word = 'box') => (each > 1 ? ` (each ${word} = ${each})` : '')
const typeLabel = (id: string, carrier: string) => labelOf(ASSET_TYPES.find(t => t.id === id)!, carrier)

const STEEL = '#e5e7eb'

// Keyed by the shared type id; every ASSET_TYPES entry needs one and no key
// may name a type the taxonomy lacks (checked when the library is built).
const APPEARANCE: Record<string, SiteAppearance> = {
  // ── Generators ────────────────────────────────────────────────────────────
  pvRoof: {
    hero: 'pvTable', zone: 'roof',
    size: { Generator: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'pvRoof', params: { m2PerMwp: 6000, minMw: 0.05, rowLength: 10, tableDepth: 2, tableGap: 1, tiltDeg: 10, canopyHeight: 4, postColor: '#9ca3af' } },
    summary: s => `Rooftop PV — ${fmt(s.amount, 'MW')} (no land take)${eachBox(s.each, 'table')}`,
  },
  pv: {
    hero: 'pvTable', zone: 'south',
    size: { Generator: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'pvField', params: { haPerMwp: 2.5, minMw: 0.05, rowLength: 30, rowPitch: 6, tableDepth: 2.2, tableGap: 2, tiltDeg: 25, lift: 1.2 } },
    // 10 m clear of everything: a perimeter service road plus the shadow line of a 12 m hall at a 50° sun (placeholder; S2 plan).
    placement: { clearanceM: 10 },
    summary: s => `PV field — ${fmt(s.amount, 'MW')} on ~${(s.areaM2 / 10_000).toFixed(1)} ha${eachBox(s.each, 'table')}`,
  },
  wind: {
    hero: 'turbine', zone: 'south',
    size: { Generator: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'turbineArray', params: { per: 5, hubHeight: 100, rotorDiameter: 140, spacingD: 4, perRow: 4, towerDiameter: 4, ratedRpm: 12, towerColor: STEEL, bladeColor: '#f3f4f6' } },
    summary: s => `Wind turbines — ${fmt(s.amount, 'MW')} as ${s.count} × ${s.params.per} MW, ${s.params.hubHeight} m hub, ${s.params.rotorDiameter} m rotor${eachBox(s.each, 'turbine')}`,
  },
  gasTurbine: {
    zone: 'west',
    size: { Generator: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'plant', params: { minM2: 600, m2PerMw: 12, hallHeight: 20, extra: 'hrsg', extraHeight: 25, extraColor: '#9ca3af', stackDiameter: 6, stackHeight: 45, stackColor: '#9ca3af' } },
    // 30 m from a data hall: noise and exhaust separation from an occupied building (placeholder; NFPA 37 §4.1.4 asks only 1.5 m of a structure — the owner's norm sets this).
    placement: { keepOutM: [{ from: ['load'], m: 30 }] },
    summary: s => `Gas turbine plant (${s.carrier}) — ${fmt(s.amount, 'MW')}`,
  },
  thermal: {
    // The Generator fallback: engine gensets, or a generic plant block named by its carrier (the shared label says which).
    hero: 'container', zone: 'west',
    size: { Generator: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'unitGrid', params: { unitW: 12.2, unitD: 2.44, unitH: 2.9, per: 2.5, perRow: 6, gap: 3, extra: 'stack', extraColor: '#9ca3af', heroable: true, anchor: 'emissive' } },
    // 30 m from a data hall: genset noise and exhaust separation from an occupied building (placeholder; NFPA 37 §4.1.4 asks only 1.5 m — the owner's norm sets this).
    placement: { keepOutM: [{ from: ['load'], m: 30 }] },
    summary: s => `${typeLabel('thermal', s.carrier)} — ${fmt(s.amount, 'MW')} in ${plural(s.count, 'enclosure')}${eachBox(s.each)}`,
  },

  // ── Storage ───────────────────────────────────────────────────────────────
  flywheel: {
    zone: 'east',
    size: { StorageUnit: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'unitGrid', params: { unitW: 2.5, unitD: 2.5, unitH: 3, per: 0.5, perRow: 10, gap: 1.5, shape: 'cylinder', anchor: 'fill' } },
    summary: s => `Flywheels — ${fmt(s.amount, 'MW')} in ${plural(s.count, 'unit')}${eachBox(s.each)}`,
  },
  pumpedHydro: {
    zone: 'south',
    size: { StorageUnit: { param: 'mwh', unit: 'MWh' } },
    geometry: { template: 'reservoir', params: { minM2: 2500, m2PerMwh: 80, wallHeight: 6, powerhouseW: 20, powerhouseD: 15, powerhouseH: 10, waterColor: '#38bdf8', embankmentColor: '#a8a29e', powerhouseColor: '#64748b' } },
    summary: s => `Pumped hydro — ${fmt(s.amount, 'MWh')}${s.mw != null ? ` / ${fmt(s.mw, 'MW')}` : ''}, upper basin ~${(s.areaM2 / 10_000).toFixed(1)} ha`,
  },
  caes: {
    zone: 'west',
    size: { StorageUnit: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'plant', params: { minM2: 400, m2PerMw: 10, hallHeight: 15, extra: 'receivers', mwPerReceiver: 50, extraColor: '#cbd5e1', stackDiameter: 0.01, stackHeight: 0.01, stackColor: '#9ca3af' } },
    summary: s => `Compressed air storage — ${fmt(s.amount, 'MW')}`,
  },
  h2store: {
    hero: 'tank', zone: 'east',
    size: { Store: { param: 'e_nom', unit: 'MWh' }, StorageUnit: { param: 'mwh', unit: 'MWh' } },
    geometry: { template: 'tankArray', params: { diameter: 3, length: 20, axis: 'north', per: 20, perRow: 6, gap: 2, heroable: true } },
    // 50 m from everything: above the largest bulk gaseous H₂ separation distance in NFPA 2 / EIGA Doc 15 tables (~30 m); deliberately conservative placeholder.
    placement: { keepOutM: [{ from: ['*'], m: 50 }] },
    summary: s => `H₂ storage — ${fmt(s.amount, 'MWh')} in ${plural(s.count, 'bullet tank')}${s.each > 1 ? ` (each = ${s.each})` : ''}`,
  },
  thermalStore: {
    zone: 'east',
    size: { Store: { param: 'e_nom', unit: 'MWh' }, StorageUnit: { param: 'mwh', unit: 'MWh' } },
    geometry: { template: 'tankArray', params: { diameter: 12, length: 20, axis: 'up', per: 250, perRow: 4, gap: 4 } },
    summary: s => `Thermal store — ${fmt(s.amount, 'MWh')} in ${plural(s.count, 'tank')}${eachBox(s.each, 'tank')}`,
  },
  bess: {
    // The StorageUnit fallback: battery containers (Phase 1 drew every StorageUnit so).
    hero: 'container', zone: 'east',
    size: { StorageUnit: { param: 'mwh', unit: 'MWh' }, Store: { param: 'e_nom', unit: 'MWh' } },
    geometry: { template: 'unitGrid', params: { unitW: 6.1, unitD: 2.44, unitH: 2.9, per: 4, perRow: 8, gap: 1.5, extra: 'pcs', extraColor: '#a78bfa', heroable: true, anchor: 'fill' } },
    // Next to its transformer, else the yard: a BESS connects through its own MV transformer and short cable runs (owner decision O1; placeholder order).
    placement: { adjacentTo: ['transformer', 'switchyard'] },
    summary: s => `Battery storage — ${fmt(s.amount, 'MWh')}${s.mw != null ? ` / ${fmt(s.mw, 'MW')}` : ''} in ${plural(s.count, 'container')}${eachBox(s.each)}`,
  },
  store: {
    // The Store fallback: a tank whose volume grows with the energy.
    zone: 'east',
    size: { Store: { param: 'e_nom', unit: 'MWh' } },
    geometry: { template: 'cube', params: { minSide: 4 } },
    summary: s => `Energy store (${s.carrier || 'unknown carrier'}) — ${fmt(s.amount, 'MWh')}`,
  },

  // ── Conversion (Links) ────────────────────────────────────────────────────
  electrolyser: {
    hero: 'container', zone: 'west',
    size: { Link: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'unitGrid', params: { unitW: 12.2, unitD: 2.44, unitH: 2.9, per: 5, perRow: 4, gap: 3, extra: 'bop', extraColor: '#67e8f9', heroable: true, anchor: 'emissive' } },
    summary: s => `Electrolyser — ${fmt(s.amount, 'MW')} in ${plural(s.count, 'skid')}${eachBox(s.each)}`,
  },
  fuelCell: {
    hero: 'container', zone: 'west',
    size: { Link: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'unitGrid', params: { unitW: 6.1, unitD: 2.44, unitH: 2.9, per: 2, perRow: 6, gap: 2, heroable: true, anchor: 'emissive' } },
    summary: s => `Fuel cells — ${fmt(s.amount, 'MW')} in ${plural(s.count, 'container')}${eachBox(s.each)}`,
  },
  heatPump: {
    hero: 'container', zone: 'west',
    size: { Link: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'unitGrid', params: { unitW: 6.1, unitD: 2.44, unitH: 2.9, per: 5, perRow: 6, gap: 2, extra: 'coolers', extraColor: '#cbd5e1', heroable: true, anchor: 'emissive' } },
    summary: s => `${/resistive|boiler/i.test(s.carrier) ? 'Electric boiler' : 'Heat pumps'} (${s.carrier}) — ${fmt(s.amount, 'MW')} in ${plural(s.count, 'skid')}${eachBox(s.each)}`,
  },
  chp: {
    zone: 'west',
    size: { Link: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'plant', params: { minM2: 300, m2PerMw: 20, hallHeight: 10, extra: 'heatExchanger', extraHeight: 4, extraColor: '#fdba74', stackDiameter: 2, stackHeight: 25, stackColor: '#9ca3af' } },
    summary: s => `CHP plant (${s.carrier}) — ${fmt(s.amount, 'MW')} fuel in`,
  },

  // ── Demand ────────────────────────────────────────────────────────────────
  offtake: {
    zone: 'northeast',
    size: { Load: { param: 'p_set', unit: 'MW' } },
    geometry: { template: 'unitGrid', params: { unitW: 6, unitD: 4, unitH: 3, per: 100, perRow: 4, gap: 2, anchor: 'emissive' } },
    summary: s => `Offtake (${s.carrier}) — ${fmt(s.amount, 'MW')} peak`,
  },
  load: {
    // The Load fallback: a data hall, sized from its IT load.
    hero: 'hall', zone: 'northeast',
    size: { Load: { param: 'p_set', unit: 'MW' }, Link: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'hall', params: { minM2: 200, m2PerMw: 800, aspect: 1.6, height: 12, roofColor: '#fbbf24' } },
    summary: s => `Load (${s.carrier || 'electricity'}) — ${fmt(s.amount, 'MW')} peak, ~${Math.round(s.areaM2)} m² hall`,
  },

  // ── Infrastructure ────────────────────────────────────────────────────────
  transformer: {
    zone: 'north', flags: { bay: true, infrastructure: true },
    size: { Transformer: { param: 's_nom', unit: 'MVA' } },
    geometry: { template: 'transformer', params: { clearance: 4, radiatorColor: '#93c5fd', bushingColor: STEEL } },
    // Midway between its two buses' yards, front (the +north flow side) towards the far bus — a transformer stands where its two voltage levels meet (owner decision O1, 2026-10-06).
    placement: { anchor: 'between', orientation: 'faceFar' },
    summary: s => `Transformer — ${fmt(s.amount, 'MVA')}${s.vHi && s.vLo ? ` ${s.vHi}/${s.vLo} kV` : ''}`,
  },
  feeder: {
    // The Line and Link fallbacks: a feeder bay (HVDC links, pipelines, anything unmatched).
    zone: 'north', flags: { bay: true, infrastructure: true },
    size: { Line: { param: 's_nom', unit: 'MVA' }, Link: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'bay', params: { width: 6, depth: 4, gantryHeight: 6, slabColor: '#94a3b8' } },
    // In its own yard, the bay's gantry turned towards the bus the line leaves for (owner decision O1; the far bus must be a member for the bearing to be known).
    placement: { anchor: 'yard', orientation: 'faceFar' },
    summary: s => `${s.cls} to ${s.far ?? '?'} — ${fmt(s.amount, s.unit)}`,
  },
  manifold: {
    zone: 'north', flags: { infrastructure: true },
    size: { Bus: { param: 'none', unit: '' } },
    geometry: { template: 'manifold', params: { minWidth: 20, baseWidth: 12, widthPerBay: 6, depth: 10, pipeDiameter: 0.8, rackHeight: 3, pipeColor: '#fbbf24', padColor: '#d6d3d1', steelColor: STEEL } },
    summary: s => `${s.carrier} manifold — bus ${s.name}`,
  },
  switchyard: {
    // The Bus fallback: an AC/DC switchyard.
    zone: 'north', flags: { infrastructure: true },
    size: { Bus: { param: 'none', unit: '' } },
    geometry: { template: 'yard', params: { minWidth: 30, baseWidth: 12, widthPerBay: 8, minDepth: 20, baseDepth: 10, depthVoltageCap: 400, kvPerDepthM: 10, padColor: '#cbd5e1', steelColor: STEEL } },
    summary: s => `${s.vNom ?? 0} kV switchyard — bus ${s.name}`,
  },
}

/** The shared types, each with its 3D appearance, in the shared (match) order. */
function buildLibrary(types: readonly AssetTypeSpec[], appearance: Record<string, SiteAppearance>): AssetType[] {
  const ids = new Set(types.map(t => t.id))
  for (const id of Object.keys(appearance)) {
    if (!ids.has(id)) throw new Error(`asset library: appearance for '${id}', which utils/assetTypes.ts does not define`)
  }
  return types.map(t => {
    const a = appearance[t.id]
    if (!a) throw new Error(`asset library: type '${t.id}' has no 3D appearance`)
    return { ...t, ...a }
  })
}

// ── validation ──────────────────────────────────────────────────────────────

const PLACEMENT_ANCHORS: readonly PlacementAnchor[] = ['yard', 'between', 'far']
const PLACEMENT_ORIENTATIONS: readonly PlacementOrientation[] = ['faceFar', 'north', 'any']

/** Throws, naming the entry and field, when the table is not usable (the shared match-rule checks, then the 3D ones). */
export function validateLibrary(lib: readonly AssetType[]): void {
  validateAssetTypes(lib)
  const ids = new Set(lib.map(t => t.id))
  for (const t of lib) {
    if (!(TEMPLATE_IDS as string[]).includes(t.geometry.template)) throw new Error(`asset library: ${t.id} has unknown template '${t.geometry.template}'`)
    if (t.hero !== undefined && !(HERO_IDS as string[]).includes(t.hero)) throw new Error(`asset library: ${t.id} names an unknown hero model '${t.hero}'`)
    for (const [k, v] of Object.entries(t.geometry.params as Record<string, ParamValue>)) {
      if (typeof v === 'number' && !(Number.isFinite(v) && v > 0)) throw new Error(`asset library: ${t.id}.geometry.params.${k} must be a finite number > 0 (got ${v})`)
    }
    const p = t.placement
    if (!p) continue
    if (p.anchor !== undefined && !PLACEMENT_ANCHORS.includes(p.anchor)) throw new Error(`asset library: ${t.id}.placement.anchor '${p.anchor}' is not one of ${PLACEMENT_ANCHORS.join(', ')}`)
    if (p.orientation !== undefined && !PLACEMENT_ORIENTATIONS.includes(p.orientation)) throw new Error(`asset library: ${t.id}.placement.orientation '${p.orientation}' is not one of ${PLACEMENT_ORIENTATIONS.join(', ')}`)
    for (const id of p.adjacentTo ?? []) {
      if (!ids.has(id)) throw new Error(`asset library: ${t.id}.placement.adjacentTo names '${id}', which the library does not define`)
    }
    if (p.clearanceM !== undefined && !(Number.isFinite(p.clearanceM) && p.clearanceM >= 0)) throw new Error(`asset library: ${t.id}.placement.clearanceM must be a finite number ≥ 0 (got ${p.clearanceM})`)
    for (const [i, k] of (p.keepOutM ?? []).entries()) {
      for (const id of k.from) {
        if (id !== '*' && !ids.has(id)) throw new Error(`asset library: ${t.id}.placement.keepOutM[${i}].from names '${id}', which the library does not define`)
      }
      if (!(Number.isFinite(k.m) && k.m >= 0)) throw new Error(`asset library: ${t.id}.placement.keepOutM[${i}].m must be a finite number ≥ 0 (got ${k.m})`)
    }
  }
}

export const DEFAULT_LIBRARY: readonly AssetType[] = deepFreeze(buildLibrary(ASSET_TYPES, APPEARANCE))
validateLibrary(DEFAULT_LIBRARY)

// ── matching ────────────────────────────────────────────────────────────────

export type MatchResult = MatchResultOf<AssetType>

/** `utils/assetTypes.matchType` against this library (same rules, the entry carries its 3D appearance). */
export function matchType(cls: string, comp: MatchComponent, ctx: MatchContext, lib: readonly AssetType[] = DEFAULT_LIBRARY): MatchResult | null {
  return matchTypeIn(cls, comp, ctx, lib)
}

export type { TemplateId }
