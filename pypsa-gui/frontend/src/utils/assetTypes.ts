// The asset taxonomy every visual layer shares (visual-layers plan 3 S1;
// consumed by plan 1 A2 and plan 2 M5): which asset type a PyPSA component
// is, what it is called, its colour and its icon. The schematic, the map and
// the 3D site view's asset library (site3d/assetLibrary.ts, which adds the
// geometry, zone, sizing and summary per type) all read this one table, so a
// component is the same type, colour and icon in every view.
//
// Main-bundle-safe on purpose: no React, no three, nothing from site3d/
// (guarded by site3d/bundleBoundary.test.ts). Icons are NAMES here;
// utils/assetTypeIcon.tsx maps a name to its component.

import { H2_BUS_RE, HEAT_BUS_RE, GAS_BUS_RE, H2_CARRIER_RE } from './busCarriers'

export type PyPSAClass = 'Bus' | 'Generator' | 'StorageUnit' | 'Store' | 'Load' | 'Transformer' | 'Line' | 'Link'
export type Port = 'bus0' | 'bus1' | 'bus2'

/** Every class the taxonomy classifies; each needs exactly one fallback rule. */
export const PYPSA_CLASSES: readonly PyPSAClass[] = ['Bus', 'Generator', 'StorageUnit', 'Store', 'Load', 'Transformer', 'Line', 'Link']

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

/**
 * An icon name: a palette item id (layout/paletteIcons.tsx draws it) or one
 * of the lucide icons the palette does not have. assetTypeIcon.tsx maps each
 * to a component; the type there is exhaustive, so a name added here without
 * an icon fails to compile.
 */
export type AssetIconName =
  | 'bus' | 'line' | 'transformer'
  | 'thermal' | 'renewable'
  | 'electrolyzer' | 'fuel_cell' | 'power_to_heat' | 'chp'
  | 'battery' | 'psh' | 'caes' | 'flywheel' | 'hydrogen' | 'thermal_storage'
  | 'load_elec' | 'load_h2' | 'load_heat'
  | 'sun' | 'box' | 'factory' | 'waypoints'

export const ASSET_ICON_NAMES: readonly AssetIconName[] = [
  'bus', 'line', 'transformer', 'thermal', 'renewable', 'electrolyzer', 'fuel_cell', 'power_to_heat', 'chp',
  'battery', 'psh', 'caes', 'flywheel', 'hydrogen', 'thermal_storage', 'load_elec', 'load_h2', 'load_heat',
  'sun', 'box', 'factory', 'waypoints',
]

/** The pure half of an asset type: identity, naming, colour, icon, and which components it matches. */
export interface AssetTypeSpec {
  id: string
  label: string | ((carrier: string) => string)
  color: string
  icon: AssetIconName
  match: Match[]
}

export const labelOf = (t: Pick<AssetTypeSpec, 'label'>, carrier: string): string =>
  typeof t.label === 'function' ? t.label(carrier) : t.label

// Colours match the map view's asset-group bubbles so a user moving between
// views reads the same colour as the same thing.
const GENSET_CARRIERS = /^(gas|diesel|oil|biogas|biomass)$/i
const gensetLabel = (c: string) => (GENSET_CARRIERS.test(c) ? `Engine gensets (${c})` : `${c || 'unknown carrier'} — generic plant block`)
/** Bus carriers drawn as a pipe manifold rather than a switchyard (utils/busCarriers.ts families). */
const NON_ELECTRICAL_BUS_RE = new RegExp(`${H2_BUS_RE.source}|${HEAT_BUS_RE.source}|${GAS_BUS_RE.source}`, 'i')
/** Solar PV, not solar thermal: 'solar', 'solar-hsat', 'PV', 'pv utility' … but not 'urban central solar thermal'. */
const SOLAR_PV_RE = /^(?!.*thermal)(?=.*(solar|(^|[^a-z])pv([^a-z]|$)))/i
/** A load carrying H₂, heat or gas (an offtake, not a data hall). */
const OFFTAKE_RE = /(^|[^a-z])(h2|hydrogen)([^a-z]|$)|heat|gas/i

// Table order is match order: the first type with a matching rule wins, so
// each class's fallback (a rule with no carrier) is the last type matching it.
const TYPES: AssetTypeSpec[] = [
  // ── Generators ────────────────────────────────────────────────────────────
  { id: 'pvRoof', label: 'Rooftop PV', color: '#22c55e', icon: 'sun', match: [{ cls: 'Generator', carrier: /rooftop/i }] },
  { id: 'pv', label: 'PV field', color: '#16a34a', icon: 'sun', match: [{ cls: 'Generator', carrier: SOLAR_PV_RE }] },
  { id: 'wind', label: 'Wind turbines', color: '#15803d', icon: 'renewable', match: [{ cls: 'Generator', carrier: /wind/i }] },
  { id: 'gasTurbine', label: 'Gas turbine plant', color: '#b91c1c', icon: 'thermal', match: [{ cls: 'Generator', carrier: /^(ccgt|ocgt)$/i }] },
  // The Generator fallback: engine gensets for gas, diesel, oil, biogas,
  // biomass; a generic plant block, named by its carrier, for anything else.
  { id: 'thermal', label: gensetLabel, color: '#dc2626', icon: 'thermal', match: [{ cls: 'Generator' }] },

  // ── Storage ───────────────────────────────────────────────────────────────
  { id: 'flywheel', label: 'Flywheels', color: '#a855f7', icon: 'flywheel', match: [{ cls: 'StorageUnit', carrier: /flywheel/i }] },
  { id: 'pumpedHydro', label: 'Pumped hydro', color: '#0284c7', icon: 'psh', match: [{ cls: 'StorageUnit', carrier: /^(hydro|phs|psh|pumped.?hydro)$/i }] },
  { id: 'caes', label: 'Compressed air', color: '#64748b', icon: 'caes', match: [{ cls: 'StorageUnit', carrier: /^(air|caes|compressed.?air)$/i }] },
  { id: 'h2store', label: 'H₂ storage', color: '#0e7490', icon: 'hydrogen', match: [{ cls: 'Store', carrier: H2_CARRIER_RE }, { cls: 'StorageUnit', carrier: H2_CARRIER_RE }] },
  { id: 'thermalStore', label: 'Thermal store', color: '#ea580c', icon: 'thermal_storage', match: [{ cls: 'Store', carrier: /heat/i }, { cls: 'StorageUnit', carrier: /heat|thermal/i }] },
  // The StorageUnit fallback: battery containers (Phase 1 drew every StorageUnit so).
  { id: 'bess', label: 'Battery storage', color: '#7c3aed', icon: 'battery', match: [{ cls: 'Store', carrier: /batter|li-?ion|bess/i }, { cls: 'StorageUnit' }] },
  // The Store fallback: a tank whose volume grows with the energy.
  { id: 'store', label: 'Energy store', color: '#6d28d9', icon: 'box', match: [{ cls: 'Store' }] },

  // ── Conversion (Links) ────────────────────────────────────────────────────
  {
    id: 'electrolyser', label: 'Electrolyser', color: '#0891b2', icon: 'electrolyzer',
    match: [
      { cls: 'Link', carrier: /electroly/i, port: 'bus0' },
      { cls: 'Link', carrier: /^h2$/i, farCarrier: { port: 'bus1', carrier: H2_BUS_RE }, port: 'bus0' },
    ],
  },
  {
    id: 'fuelCell', label: 'Fuel cells', color: '#0d9488', icon: 'fuel_cell',
    match: [
      { cls: 'Link', carrier: /fuel.?cell/i, port: 'bus1' },
      { cls: 'Link', carrier: /^h2$/i, farCarrier: { port: 'bus0', carrier: H2_BUS_RE }, port: 'bus1' },
    ],
  },
  { id: 'heatPump', label: 'Heat pumps / e-boilers', color: '#f97316', icon: 'power_to_heat', match: [{ cls: 'Link', carrier: /heat.?pump|resistive|electric.?boiler|^e-?boiler$/i, port: 'bus0' }] },
  { id: 'chp', label: 'CHP plant', color: '#c2410c', icon: 'chp', match: [{ cls: 'Link', carrier: /^(gas|biogas|.*\bchp\b.*)$/i, hasBus2: true, port: 'bus1' }] },

  // ── Demand ────────────────────────────────────────────────────────────────
  { id: 'offtake', label: 'Offtake (H₂ / heat)', color: '#f59e0b', icon: 'factory', match: [{ cls: 'Load', carrier: OFFTAKE_RE }] },
  // The Load fallback: a data hall, sized from its IT load.
  { id: 'load', label: 'Data hall', color: '#d97706', icon: 'load_elec', match: [{ cls: 'Link', carrier: /data.?cent/i, port: 'bus0' }, { cls: 'Load' }] },

  // ── Infrastructure ────────────────────────────────────────────────────────
  { id: 'transformer', label: 'Transformer', color: '#2563eb', icon: 'transformer', match: [{ cls: 'Transformer' }] },
  // The Line and Link fallbacks: a feeder bay (HVDC links, pipelines, anything unmatched).
  { id: 'feeder', label: 'Feeder bay', color: '#475569', icon: 'line', match: [{ cls: 'Line' }, { cls: 'Link' }] },
  { id: 'manifold', label: 'Pipe manifold', color: '#0f766e', icon: 'waypoints', match: [{ cls: 'Bus', carrier: NON_ELECTRICAL_BUS_RE }] },
  // The Bus fallback: an AC/DC switchyard.
  { id: 'switchyard', label: 'Switchyard', color: '#64748b', icon: 'bus', match: [{ cls: 'Bus' }] },
]

export function deepFreeze<T>(o: T): T {
  if (o && typeof o === 'object' && !(o instanceof RegExp) && !Object.isFrozen(o)) {
    Object.freeze(o)
    for (const v of Object.values(o as Record<string, unknown>)) deepFreeze(v)
  }
  return o
}

// ── validation ──────────────────────────────────────────────────────────────

/** A rule with no carrier, far carrier, bus2 or port requirement: matches every component of its class. */
export const isFallbackRule = (m: Match): boolean => !m.carrier && !m.farCarrier && !m.hasBus2 && !m.port

/**
 * Throws, naming the entry, when the match rules are not usable: a duplicate
 * id, an unknown icon name, a stateful (g/y) carrier regex, or a class with
 * other than exactly one fallback rule, or whose fallback is not the last
 * type matching it. The 3D library runs this on its own table too.
 */
export function validateAssetTypes(types: readonly AssetTypeSpec[]): void {
  const ids = new Set<string>()
  for (const t of types) {
    if (ids.has(t.id)) throw new Error(`asset types: duplicate id '${t.id}'`)
    ids.add(t.id)
    if (!ASSET_ICON_NAMES.includes(t.icon)) throw new Error(`asset types: ${t.id} names an unknown icon '${t.icon}'`)
    for (const m of t.match) {
      for (const re of [m.carrier, m.farCarrier?.carrier]) {
        if (re && (re.global || re.sticky)) throw new Error(`asset types: ${t.id} has a carrier regex with the g or y flag (${re}) — a frozen stateful regex throws on .test`)
      }
    }
  }
  for (const cls of PYPSA_CLASSES) {
    const typesForCls = types.filter(t => t.match.some(m => m.cls === cls))
    const fallbacks = typesForCls.filter(t => t.match.some(m => m.cls === cls && isFallbackRule(m)))
    if (fallbacks.length !== 1) throw new Error(`asset types: class ${cls} needs exactly one fallback rule (a rule with no carrier), found ${fallbacks.length}`)
    if (fallbacks[0] !== typesForCls[typesForCls.length - 1]) throw new Error(`asset types: class ${cls}'s fallback must be the last type matching it (${fallbacks[0].id})`)
  }
}

/** The taxonomy, in match order. Deep-frozen; the 3D library extends each entry with its geometry. */
export const ASSET_TYPES: readonly AssetTypeSpec[] = deepFreeze(TYPES)
validateAssetTypes(ASSET_TYPES)

export const ASSET_TYPE_IDS: readonly string[] = ASSET_TYPES.map(t => t.id)

const BY_ID = new Map(ASSET_TYPES.map(t => [t.id, t]))
/** The type with this id, or undefined (an id a stale document names). */
export const assetTypeOf = (id: string): AssetTypeSpec | undefined => BY_ID.get(id)
/** The icon name for a type id; undefined for an unknown id. */
export const iconNameOf = (id: string): AssetIconName | undefined => BY_ID.get(id)?.icon

// ── matching ────────────────────────────────────────────────────────────────

export interface MatchContext {
  /** The buses in view (a site's members; a canvas: every bus), in order. */
  members: string[]
  /** Every network bus's carrier (not only members'); undefined = unknown, treated as AC. */
  busCarrier: (name: string) => string | undefined
}

export interface MatchResult<T extends Matchable = AssetTypeSpec> {
  type: T
  /** The member bus the object is drawn from. */
  owner: string
  /** Branches: the bus at the other end. */
  far?: string
}

/** A component as the matcher sees it: any PyPSA component row. */
export type MatchComponent = Record<string, unknown> & { name: string }

/** Anything with match rules: the shared spec, or the 3D library's richer entry. */
export interface Matchable { match: readonly Match[] }

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
 * names a port only matches when that port's bus is a member (an
 * electrolyser stands at its electrical side; seen only from its H₂ side it
 * is a feeder to it). Otherwise the owner is the first member among the
 * component's ports, bus0 first. Null when no port is a member.
 */
export function matchType<T extends Matchable = AssetTypeSpec>(
  cls: string, comp: MatchComponent, ctx: MatchContext, lib: readonly T[] = ASSET_TYPES as unknown as readonly T[],
): MatchResult<T> | null {
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

export interface LegendEntry { id: string; label: string; color: string; icon: AssetIconName }

/** One entry per type present, in table order; a type labelled by carrier lists the carriers present. */
export function legendFor(objects: readonly { kind: string; carrier?: string }[], lib: readonly AssetTypeSpec[] = ASSET_TYPES): LegendEntry[] {
  const out: LegendEntry[] = []
  for (const t of lib) {
    const here = objects.filter(o => o.kind === t.id)
    if (!here.length) continue
    const carriers = [...new Set(here.map(o => o.carrier ?? ''))]
    const label = typeof t.label === 'function' ? carriers.map(c => labelOf(t, c)).join(', ') : t.label
    out.push({ id: t.id, label, color: t.color, icon: t.icon })
  }
  return out
}

// ── sizing figure ───────────────────────────────────────────────────────────
// The one number a type is named by — "40 MWh", "12 MW" — for a node label
// (plan 1 A2). Mirrors the 3D library's `size` rules (site3d/assetLibrary.ts
// `SiteAppearance.size`, read by site3d/sizing.ts) without importing that
// table into the main bundle: generators and conversion Links by `p_nom`,
// loads by `p_set`, Stores by `e_nom`, StorageUnits by the energy they hold
// (`p_nom × max_hours`) except the two the library sizes by power (flywheels,
// compressed air). Installed sizes, as the 3D view's default; the overlay
// replaces the figure with the period-effective capacity when it is on.

export type SizingUnit = 'MW' | 'MWh' | 'MVA'
export interface SizingFigure { amount: number; unit: SizingUnit }

/** StorageUnit types the 3D library sizes by power, not energy. */
const POWER_SIZED_STORAGE = new Set(['flywheel', 'caes'])

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)

/** The sizing figure of a component of `cls` drawn as `typeId`; null for a class without one (Bus). */
export function sizingFigure(cls: string, comp: Record<string, unknown>, typeId?: string): SizingFigure | null {
  switch (cls as PyPSAClass) {
    case 'Generator': case 'Link': return { amount: num(comp.p_nom), unit: 'MW' }
    case 'Load': return { amount: Math.abs(num(comp.p_set_peak) || num(comp.p_set)), unit: 'MW' }
    case 'Store': return { amount: num(comp.e_nom), unit: 'MWh' }
    case 'StorageUnit': {
      const p = num(comp.p_nom), h = num(comp.max_hours)
      if (typeId && POWER_SIZED_STORAGE.has(typeId)) return { amount: p, unit: 'MW' }
      return h > 0 ? { amount: p * h, unit: 'MWh' } : { amount: p, unit: 'MW' }
    }
    case 'Line': case 'Transformer': return { amount: num(comp.s_nom), unit: 'MVA' }
    default: return null
  }
}

/** "40 MWh", "12.5 MW", "1.2 GW": integers plain, otherwise one decimal; ≥ 1000 MW/MWh in G. */
export function formatSizing(s: SizingFigure): string {
  const { amount, unit } = s
  const big = Math.abs(amount) >= 1000 && unit !== 'MVA'
  const v = big ? amount / 1000 : amount
  const u = big ? unit.replace(/^M/, 'G') : unit
  return `${Number.isInteger(v) ? v : v.toFixed(1)} ${u}`
}

// ── asset nodes ─────────────────────────────────────────────────────────────
// Which types are a thing the user designs (drawn as a node of its own in the
// schematic's *individual* mode) rather than infrastructure that stays an edge
// or a bus: a Link between two electrical buses (`feeder`) is a branch; a
// transformer is a branch; buses are buses.
export const ASSET_NODE_EXCLUDED_TYPES: ReadonlySet<string> = new Set(['feeder', 'transformer', 'switchyard', 'manifold'])

/** True when a component drawn as this type is an asset node (not a branch or a bus). */
export const isAssetNodeType = (typeId: string): boolean => !ASSET_NODE_EXCLUDED_TYPES.has(typeId)
