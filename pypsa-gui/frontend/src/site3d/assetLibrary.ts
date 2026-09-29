// The 3D site view's asset library (Phase 2 spec §4, plan Tasks 1.1–1.2).
//
// One table says what every PyPSA component looks like on a site: which
// components a type matches (class, carrier, the far port's carrier), how it
// is sized (which parameter, what one unit is worth), which template draws
// it and with what numbers and colours, where it is packed, what it is
// called. `layout.ts` packs whatever this table produces and names no asset
// type itself, so adding or re-tuning a type is a data change.
//
// Pure: no React, no three.js (main-bundle-safe, though only the lazy
// SiteCanvas chunk imports it today).

import type { Geometry, ParamValue, TemplateId } from './templates'
import { TEMPLATE_IDS } from './templates'
import { H2_BUS_RE, HEAT_BUS_RE, GAS_BUS_RE, H2_CARRIER_RE } from '../utils/busCarriers'

export type PyPSAClass = 'Bus' | 'Generator' | 'StorageUnit' | 'Store' | 'Load' | 'Transformer' | 'Line' | 'Link'
export type Port = 'bus0' | 'bus1' | 'bus2'
export type Zone = 'north' | 'west' | 'east' | 'northeast' | 'south' | 'roof'

export interface Match {
  cls: PyPSAClass
  /** The component's carrier (a Bus: the bus's own). Absent = this class's fallback. */
  carrier?: RegExp
  /** Links: the carrier of a named port's bus (an electrolyser's bus1 is H2). */
  farCarrier?: { port: Port; carrier: RegExp }
  /** Three-port Links (CHP). An empty-string bus2 counts as absent. */
  hasBus2?: boolean
  /** Links: the port the equipment stands at; that bus must be a site member for this rule to match. */
  port?: Port
}

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

export interface AssetType {
  id: string
  label: string | ((carrier: string) => string)
  color: string
  zone: Zone
  match: Match[]
  size: Partial<Record<PyPSAClass, SizeRule>>
  geometry: Geometry
  summary: (s: SummaryInfo) => string
  flags?: { bay?: boolean; infrastructure?: boolean }
}

// ── helpers for the summaries ───────────────────────────────────────────────

const fmt = (v: number, unit: string) => `${Number.isInteger(v) ? v : v.toFixed(1)} ${unit}`
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
const eachBox = (each: number, word = 'box') => (each > 1 ? ` (each ${word} = ${each})` : '')
export const labelOf = (t: AssetType, carrier: string): string => (typeof t.label === 'function' ? t.label(carrier) : t.label)

// Colours match the map view's asset-group bubbles so a user moving between
// views reads the same colour as the same thing.
const STEEL = '#e5e7eb'
const GENSET_CARRIERS = /^(gas|diesel|oil|biogas|biomass)$/i
const gensetLabel = (c: string) => (GENSET_CARRIERS.test(c) ? `Engine gensets (${c})` : `${c || 'unknown carrier'} — generic plant block`)
/** Bus carriers drawn as a pipe manifold rather than a switchyard (utils/busCarriers.ts families). */
const NON_ELECTRICAL_BUS_RE = new RegExp(`${H2_BUS_RE.source}|${HEAT_BUS_RE.source}|${GAS_BUS_RE.source}`, 'i')
/** Solar PV, not solar thermal: 'solar', 'solar-hsat', 'PV', 'pv utility' … but not 'urban central solar thermal'. */
const SOLAR_PV_RE = /^(?!.*thermal)(?=.*(solar|(^|[^a-z])pv([^a-z]|$)))/i
/** A load carrying H₂, heat or gas (an offtake, not a data hall). */
const OFFTAKE_RE = /(^|[^a-z])(h2|hydrogen)([^a-z]|$)|heat|gas/i

const LIB: AssetType[] = [
  // ── Generators ────────────────────────────────────────────────────────────
  {
    id: 'pvRoof', label: 'Rooftop PV', color: '#22c55e', zone: 'roof',
    match: [{ cls: 'Generator', carrier: /rooftop/i }],
    size: { Generator: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'pvRoof', params: { m2PerMwp: 6000, minMw: 0.05, rowLength: 10, tableDepth: 2, tableGap: 1, tiltDeg: 10, canopyHeight: 4, postColor: '#9ca3af' } },
    summary: s => `Rooftop PV — ${fmt(s.amount, 'MW')} (no land take)${eachBox(s.each, 'table')}`,
  },
  {
    id: 'pv', label: 'PV field', color: '#16a34a', zone: 'south',
    match: [{ cls: 'Generator', carrier: SOLAR_PV_RE }],
    size: { Generator: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'pvField', params: { haPerMwp: 2.5, minMw: 0.05, rowLength: 30, rowPitch: 6, tableDepth: 2.2, tableGap: 2, tiltDeg: 25, lift: 1.2 } },
    summary: s => `PV field — ${fmt(s.amount, 'MW')} on ~${(s.areaM2 / 10_000).toFixed(1)} ha${eachBox(s.each, 'table')}`,
  },
  {
    id: 'wind', label: 'Wind turbines', color: '#15803d', zone: 'south',
    match: [{ cls: 'Generator', carrier: /wind/i }],
    size: { Generator: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'turbineArray', params: { per: 5, hubHeight: 100, rotorDiameter: 140, spacingD: 4, perRow: 4, towerDiameter: 4, ratedRpm: 12, towerColor: STEEL, bladeColor: '#f3f4f6' } },
    summary: s => `Wind turbines — ${fmt(s.amount, 'MW')} as ${s.count} × ${s.params.per} MW, ${s.params.hubHeight} m hub, ${s.params.rotorDiameter} m rotor${eachBox(s.each, 'turbine')}`,
  },
  {
    id: 'gasTurbine', label: 'Gas turbine plant', color: '#b91c1c', zone: 'west',
    match: [{ cls: 'Generator', carrier: /^(ccgt|ocgt)$/i }],
    size: { Generator: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'plant', params: { minM2: 600, m2PerMw: 12, hallHeight: 20, extra: 'hrsg', extraHeight: 25, extraColor: '#9ca3af', stackDiameter: 6, stackHeight: 45, stackColor: '#9ca3af' } },
    summary: s => `Gas turbine plant (${s.carrier}) — ${fmt(s.amount, 'MW')}`,
  },
  {
    // The Generator fallback: engine gensets for gas, diesel, oil, biogas,
    // biomass; a generic plant block, named by its carrier, for anything else.
    id: 'thermal', color: '#dc2626', zone: 'west',
    label: gensetLabel,
    match: [{ cls: 'Generator' }],
    size: { Generator: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'unitGrid', params: { unitW: 12.2, unitD: 2.44, unitH: 2.9, per: 2.5, perRow: 6, gap: 3, extra: 'stack', extraColor: '#9ca3af', heroable: true, anchor: 'emissive' } },
    summary: s => `${gensetLabel(s.carrier)} — ${fmt(s.amount, 'MW')} in ${plural(s.count, 'enclosure')}${eachBox(s.each)}`,
  },

  // ── Storage ───────────────────────────────────────────────────────────────
  {
    id: 'flywheel', label: 'Flywheels', color: '#a855f7', zone: 'east',
    match: [{ cls: 'StorageUnit', carrier: /flywheel/i }],
    size: { StorageUnit: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'unitGrid', params: { unitW: 2.5, unitD: 2.5, unitH: 3, per: 0.5, perRow: 10, gap: 1.5, shape: 'cylinder', anchor: 'fill' } },
    summary: s => `Flywheels — ${fmt(s.amount, 'MW')} in ${plural(s.count, 'unit')}${eachBox(s.each)}`,
  },
  {
    id: 'pumpedHydro', label: 'Pumped hydro', color: '#0284c7', zone: 'south',
    match: [{ cls: 'StorageUnit', carrier: /^(hydro|phs|psh|pumped.?hydro)$/i }],
    size: { StorageUnit: { param: 'mwh', unit: 'MWh' } },
    geometry: { template: 'reservoir', params: { minM2: 2500, m2PerMwh: 80, wallHeight: 6, powerhouseW: 20, powerhouseD: 15, powerhouseH: 10, waterColor: '#38bdf8', embankmentColor: '#a8a29e', powerhouseColor: '#64748b' } },
    summary: s => `Pumped hydro — ${fmt(s.amount, 'MWh')}${s.mw != null ? ` / ${fmt(s.mw, 'MW')}` : ''}, upper basin ~${(s.areaM2 / 10_000).toFixed(1)} ha`,
  },
  {
    id: 'caes', label: 'Compressed air', color: '#64748b', zone: 'west',
    match: [{ cls: 'StorageUnit', carrier: /^(air|caes|compressed.?air)$/i }],
    size: { StorageUnit: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'plant', params: { minM2: 400, m2PerMw: 10, hallHeight: 15, extra: 'receivers', mwPerReceiver: 50, extraColor: '#cbd5e1', stackDiameter: 0.01, stackHeight: 0.01, stackColor: '#9ca3af' } },
    summary: s => `Compressed air storage — ${fmt(s.amount, 'MW')}`,
  },
  {
    id: 'h2store', label: 'H₂ storage', color: '#0e7490', zone: 'east',
    match: [{ cls: 'Store', carrier: H2_CARRIER_RE }, { cls: 'StorageUnit', carrier: H2_CARRIER_RE }],
    size: { Store: { param: 'e_nom', unit: 'MWh' }, StorageUnit: { param: 'mwh', unit: 'MWh' } },
    geometry: { template: 'tankArray', params: { diameter: 3, length: 20, axis: 'north', per: 20, perRow: 6, gap: 2, heroable: true } },
    summary: s => `H₂ storage — ${fmt(s.amount, 'MWh')} in ${plural(s.count, 'bullet tank')}${s.each > 1 ? ` (each = ${s.each})` : ''}`,
  },
  {
    id: 'thermalStore', label: 'Thermal store', color: '#ea580c', zone: 'east',
    match: [{ cls: 'Store', carrier: /heat/i }, { cls: 'StorageUnit', carrier: /heat|thermal/i }],
    size: { Store: { param: 'e_nom', unit: 'MWh' }, StorageUnit: { param: 'mwh', unit: 'MWh' } },
    geometry: { template: 'tankArray', params: { diameter: 12, length: 20, axis: 'up', per: 250, perRow: 4, gap: 4 } },
    summary: s => `Thermal store — ${fmt(s.amount, 'MWh')} in ${plural(s.count, 'tank')}${eachBox(s.each, 'tank')}`,
  },
  {
    // The StorageUnit fallback: battery containers (Phase 1 drew every StorageUnit so).
    id: 'bess', label: 'Battery storage', color: '#7c3aed', zone: 'east',
    match: [{ cls: 'Store', carrier: /batter|li-?ion|bess/i }, { cls: 'StorageUnit' }],
    size: { StorageUnit: { param: 'mwh', unit: 'MWh' }, Store: { param: 'e_nom', unit: 'MWh' } },
    geometry: { template: 'unitGrid', params: { unitW: 6.1, unitD: 2.44, unitH: 2.9, per: 4, perRow: 8, gap: 1.5, extra: 'pcs', extraColor: '#a78bfa', heroable: true, anchor: 'fill' } },
    summary: s => `Battery storage — ${fmt(s.amount, 'MWh')}${s.mw != null ? ` / ${fmt(s.mw, 'MW')}` : ''} in ${plural(s.count, 'container')}${eachBox(s.each)}`,
  },
  {
    // The Store fallback: a tank whose volume grows with the energy.
    id: 'store', label: 'Energy store', color: '#6d28d9', zone: 'east',
    match: [{ cls: 'Store' }],
    size: { Store: { param: 'e_nom', unit: 'MWh' } },
    geometry: { template: 'cube', params: { minSide: 4 } },
    summary: s => `Energy store (${s.carrier || 'unknown carrier'}) — ${fmt(s.amount, 'MWh')}`,
  },

  // ── Conversion (Links) ────────────────────────────────────────────────────
  {
    id: 'electrolyser', label: 'Electrolyser', color: '#0891b2', zone: 'west',
    match: [
      { cls: 'Link', carrier: /electroly/i, port: 'bus0' },
      { cls: 'Link', carrier: /^h2$/i, farCarrier: { port: 'bus1', carrier: H2_BUS_RE }, port: 'bus0' },
    ],
    size: { Link: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'unitGrid', params: { unitW: 12.2, unitD: 2.44, unitH: 2.9, per: 5, perRow: 4, gap: 3, extra: 'bop', extraColor: '#67e8f9', heroable: true, anchor: 'emissive' } },
    summary: s => `Electrolyser — ${fmt(s.amount, 'MW')} in ${plural(s.count, 'skid')}${eachBox(s.each)}`,
  },
  {
    id: 'fuelCell', label: 'Fuel cells', color: '#0d9488', zone: 'west',
    match: [
      { cls: 'Link', carrier: /fuel.?cell/i, port: 'bus1' },
      { cls: 'Link', carrier: /^h2$/i, farCarrier: { port: 'bus0', carrier: H2_BUS_RE }, port: 'bus1' },
    ],
    size: { Link: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'unitGrid', params: { unitW: 6.1, unitD: 2.44, unitH: 2.9, per: 2, perRow: 6, gap: 2, heroable: true, anchor: 'emissive' } },
    summary: s => `Fuel cells — ${fmt(s.amount, 'MW')} in ${plural(s.count, 'container')}${eachBox(s.each)}`,
  },
  {
    id: 'heatPump', label: 'Heat pumps / e-boilers', color: '#f97316', zone: 'west',
    match: [{ cls: 'Link', carrier: /heat.?pump|resistive|electric.?boiler|^e-?boiler$/i, port: 'bus0' }],
    size: { Link: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'unitGrid', params: { unitW: 6.1, unitD: 2.44, unitH: 2.9, per: 5, perRow: 6, gap: 2, extra: 'coolers', extraColor: '#cbd5e1', heroable: true, anchor: 'emissive' } },
    summary: s => `${/resistive|boiler/i.test(s.carrier) ? 'Electric boiler' : 'Heat pumps'} (${s.carrier}) — ${fmt(s.amount, 'MW')} in ${plural(s.count, 'skid')}${eachBox(s.each)}`,
  },
  {
    id: 'chp', label: 'CHP plant', color: '#c2410c', zone: 'west',
    match: [{ cls: 'Link', carrier: /^(gas|biogas|.*\bchp\b.*)$/i, hasBus2: true, port: 'bus1' }],
    size: { Link: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'plant', params: { minM2: 300, m2PerMw: 20, hallHeight: 10, extra: 'heatExchanger', extraHeight: 4, extraColor: '#fdba74', stackDiameter: 2, stackHeight: 25, stackColor: '#9ca3af' } },
    summary: s => `CHP plant (${s.carrier}) — ${fmt(s.amount, 'MW')} fuel in`,
  },

  // ── Demand ────────────────────────────────────────────────────────────────
  {
    id: 'offtake', label: 'Offtake (H₂ / heat)', color: '#f59e0b', zone: 'northeast',
    match: [{ cls: 'Load', carrier: OFFTAKE_RE }],
    size: { Load: { param: 'p_set', unit: 'MW' } },
    geometry: { template: 'unitGrid', params: { unitW: 6, unitD: 4, unitH: 3, per: 100, perRow: 4, gap: 2, anchor: 'emissive' } },
    summary: s => `Offtake (${s.carrier}) — ${fmt(s.amount, 'MW')} peak`,
  },
  {
    // The Load fallback: a data hall, sized from its IT load.
    id: 'load', label: 'Data hall', color: '#d97706', zone: 'northeast',
    match: [{ cls: 'Link', carrier: /data.?cent/i, port: 'bus0' }, { cls: 'Load' }],
    size: { Load: { param: 'p_set', unit: 'MW' }, Link: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'hall', params: { minM2: 200, m2PerMw: 800, aspect: 1.6, height: 12, roofColor: '#fbbf24' } },
    summary: s => `Load (${s.carrier || 'electricity'}) — ${fmt(s.amount, 'MW')} peak, ~${Math.round(s.areaM2)} m² hall`,
  },

  // ── Infrastructure ────────────────────────────────────────────────────────
  {
    id: 'transformer', label: 'Transformer', color: '#2563eb', zone: 'north', flags: { bay: true, infrastructure: true },
    match: [{ cls: 'Transformer' }],
    size: { Transformer: { param: 's_nom', unit: 'MVA' } },
    geometry: { template: 'transformer', params: { clearance: 4, radiatorColor: '#93c5fd', bushingColor: STEEL } },
    summary: s => `Transformer — ${fmt(s.amount, 'MVA')}${s.vHi && s.vLo ? ` ${s.vHi}/${s.vLo} kV` : ''}`,
  },
  {
    // The Line and Link fallbacks: a feeder bay (HVDC links, pipelines, anything unmatched).
    id: 'feeder', label: 'Feeder bay', color: '#475569', zone: 'north', flags: { bay: true, infrastructure: true },
    match: [{ cls: 'Line' }, { cls: 'Link' }],
    size: { Line: { param: 's_nom', unit: 'MVA' }, Link: { param: 'p_nom', unit: 'MW' } },
    geometry: { template: 'bay', params: { width: 6, depth: 4, gantryHeight: 6, slabColor: '#94a3b8' } },
    summary: s => `${s.cls} to ${s.far ?? '?'} — ${fmt(s.amount, s.unit)}`,
  },
  {
    id: 'manifold', label: 'Pipe manifold', color: '#0f766e', zone: 'north', flags: { infrastructure: true },
    match: [{ cls: 'Bus', carrier: NON_ELECTRICAL_BUS_RE }],
    size: { Bus: { param: 'none', unit: '' } },
    geometry: { template: 'manifold', params: { minWidth: 20, baseWidth: 12, widthPerBay: 6, depth: 10, pipeDiameter: 0.8, rackHeight: 3, pipeColor: '#fbbf24', padColor: '#d6d3d1', steelColor: STEEL } },
    summary: s => `${s.carrier} manifold — bus ${s.name}`,
  },
  {
    // The Bus fallback: an AC/DC switchyard.
    id: 'switchyard', label: 'Switchyard', color: '#64748b', zone: 'north', flags: { infrastructure: true },
    match: [{ cls: 'Bus' }],
    size: { Bus: { param: 'none', unit: '' } },
    geometry: { template: 'yard', params: { minWidth: 30, baseWidth: 12, widthPerBay: 8, minDepth: 20, baseDepth: 10, depthVoltageCap: 400, kvPerDepthM: 10, padColor: '#cbd5e1', steelColor: STEEL } },
    summary: s => `${s.vNom ?? 0} kV switchyard — bus ${s.name}`,
  },
]

function deepFreeze<T>(o: T): T {
  if (o && typeof o === 'object' && !(o instanceof RegExp) && !Object.isFrozen(o)) {
    Object.freeze(o)
    for (const v of Object.values(o as Record<string, unknown>)) deepFreeze(v)
  }
  return o
}

// ── validation ──────────────────────────────────────────────────────────────

const CLASSES: PyPSAClass[] = ['Bus', 'Generator', 'StorageUnit', 'Store', 'Load', 'Transformer', 'Line', 'Link']
const isFallback = (m: Match) => !m.carrier && !m.farCarrier && !m.hasBus2 && !m.port

/** Throws, naming the entry and field, when the table is not usable. */
export function validateLibrary(lib: readonly AssetType[]): void {
  const ids = new Set<string>()
  for (const t of lib) {
    if (ids.has(t.id)) throw new Error(`asset library: duplicate id '${t.id}'`)
    ids.add(t.id)
    if (!(TEMPLATE_IDS as string[]).includes(t.geometry.template)) throw new Error(`asset library: ${t.id} has unknown template '${t.geometry.template}'`)
    for (const [k, v] of Object.entries(t.geometry.params as Record<string, ParamValue>)) {
      if (typeof v === 'number' && !(Number.isFinite(v) && v > 0)) throw new Error(`asset library: ${t.id}.geometry.params.${k} must be a finite number > 0 (got ${v})`)
    }
    for (const m of t.match) {
      for (const re of [m.carrier, m.farCarrier?.carrier]) {
        if (re && (re.global || re.sticky)) throw new Error(`asset library: ${t.id} has a carrier regex with the g or y flag (${re}) — a frozen stateful regex throws on .test`)
      }
    }
  }
  for (const cls of CLASSES) {
    const typesForCls = lib.filter(t => t.match.some(m => m.cls === cls))
    const fallbacks = typesForCls.filter(t => t.match.some(m => m.cls === cls && isFallback(m)))
    if (fallbacks.length !== 1) throw new Error(`asset library: class ${cls} needs exactly one fallback rule (a rule with no carrier), found ${fallbacks.length}`)
    if (fallbacks[0] !== typesForCls[typesForCls.length - 1]) throw new Error(`asset library: class ${cls}'s fallback must be the last type matching it (${fallbacks[0].id})`)
  }
}

export const DEFAULT_LIBRARY: readonly AssetType[] = deepFreeze(LIB)
validateLibrary(DEFAULT_LIBRARY)

// ── matching ────────────────────────────────────────────────────────────────

export interface MatchContext {
  /** Site member buses, in site order. */
  members: string[]
  /** Every network bus's carrier (not only members'); undefined = unknown, treated as AC. */
  busCarrier: (name: string) => string | undefined
}

export interface MatchResult {
  type: AssetType
  /** The member bus the object is drawn from. */
  owner: string
  /** Branches: the bus at the other end. */
  far?: string
}

/** A component as the matcher sees it: any PyPSA component row. */
export type MatchComponent = Record<string, unknown> & { name: string }

const portsOf = (cls: PyPSAClass, c: MatchComponent): string[] => {
  if (cls === 'Bus') return [c.name]
  if (cls === 'Line' || cls === 'Transformer') return [c.bus0 as string, c.bus1 as string]
  if (cls === 'Link') return [c.bus0 as string, c.bus1 as string, (c.bus2 as string) || ''].filter(Boolean)
  return [c.bus as string]
}

const portBus = (c: MatchComponent, port: Port): string => ((c[port] as string | undefined) ?? '') || ''

/**
 * The asset type a component is drawn as, and the member bus it is drawn
 * from. The first type (table order) with a matching rule wins. A rule that
 * names a port only matches when that port's bus is a site member (an
 * electrolyser stands at its electrical side; seen only from its H₂ side it
 * is a feeder to it). Otherwise the owner is the first member among the
 * component's ports, bus0 first. Null when no port is a member.
 */
export function matchType(cls: string, comp: MatchComponent, ctx: MatchContext, lib: readonly AssetType[] = DEFAULT_LIBRARY): MatchResult | null {
  const c = cls as PyPSAClass
  const members = new Set(ctx.members)
  const ports = portsOf(c, comp)
  const firstMember = ports.find(p => members.has(p))
  if (!firstMember) return null
  const carrier = c === 'Bus' ? (ctx.busCarrier(comp.name) ?? (comp.carrier as string | undefined) ?? 'AC') : ((comp.carrier as string | undefined) ?? '')
  const hasBus2 = c === 'Link' && !!((comp.bus2 as string | undefined) ?? '')
  for (const t of lib) {
    for (const m of t.match) {
      if (m.cls !== c) continue
      if (m.carrier && !m.carrier.test(carrier)) continue
      if (m.hasBus2 && !hasBus2) continue
      if (m.farCarrier) {
        const far = portBus(comp, m.farCarrier.port)
        if (!far || !m.farCarrier.carrier.test(ctx.busCarrier(far) ?? 'AC')) continue
      }
      let owner = firstMember
      if (m.port) {
        const at = portBus(comp, m.port)
        if (!members.has(at)) continue
        owner = at
      }
      const far = (c === 'Line' || c === 'Transformer' || c === 'Link') ? ports.find(p => p !== owner) : undefined
      return { type: t, owner, far }
    }
  }
  return null
}

// ── legend ──────────────────────────────────────────────────────────────────

export interface LegendEntry { id: string; label: string; color: string }

/** One entry per type present, in library order; a type labelled by carrier lists the carriers present. */
export function legendFor(objects: { kind: string; carrier?: string }[], lib: readonly AssetType[] = DEFAULT_LIBRARY): LegendEntry[] {
  const out: LegendEntry[] = []
  for (const t of lib) {
    const here = objects.filter(o => o.kind === t.id)
    if (!here.length) continue
    const carriers = [...new Set(here.map(o => o.carrier ?? ''))]
    const label = typeof t.label === 'function' ? carriers.map(c => t.label instanceof Function ? t.label(c) : '').join(', ') : t.label
    out.push({ id: t.id, label, color: t.color })
  }
  return out
}

export type { TemplateId }
