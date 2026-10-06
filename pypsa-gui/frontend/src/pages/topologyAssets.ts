// ── Asset nodes for the schematic's *individual* mode (pure) ──────────────────
// Visual-layers plan 1, A2: every Generator, Load, StorageUnit, Store and
// every Link that is not a branch between two electrical buses is a node of
// its own, typed by the shared taxonomy (utils/assetTypes.ts), labelled with
// the type icon, the component name and the sizing figure the type names,
// and connected to each of its buses by a dashed asset edge. *Grouped* is the
// four-bubbles-per-bus view the canvas had before.
//
// No React, no React Flow: TopologyCanvas turns these descriptors into its
// nodes and edges, and the rules (which components, which bus, which zone,
// where on the ring, what survives a save) are tested here over a fixture.
import type { Generator, Link, Load, StorageUnit, Store } from '../api/types'
import {
  isAssetNodeType, labelOf, legendFor, matchType, sizingFigure,
  type AssetIconName, type LegendEntry, type MatchContext, type PyPSAClass, type SizingFigure,
} from '../utils/assetTypes'
import { hashStr, type LayoutSatellite } from './topologyLayout'
import type { PersistedNode } from './topologyLayoutStore'

// ── Mode ──────────────────────────────────────────────────────────────────────

export type AssetMode = 'grouped' | 'individual'

/**
 * Up to this many non-bus components a network opens in *individual* mode;
 * above it the grouped bubbles keep a large grid readable. The user's choice,
 * once made, is persisted and overrides this.
 */
export const INDIVIDUAL_MAX_COMPONENTS = 40

export const defaultAssetMode = (nonBusComponents: number): AssetMode =>
  nonBusComponents <= INDIVIDUAL_MAX_COMPONENTS ? 'individual' : 'grouped'

/** Every component that is not a bus: the count the default mode is decided on. */
export function countNonBusComponents(counts: {
  generators: number; loads: number; storageUnits: number; stores: number
  links: number; lines: number; transformers: number
}): number {
  return counts.generators + counts.loads + counts.storageUnits + counts.stores + counts.links + counts.lines + counts.transformers
}

// ── Ids ───────────────────────────────────────────────────────────────────────
// `asset-<Class>:<name>`. Bus nodes are bare bus names and group bubbles are
// `assetgrp-…`, so the prefix cannot collide with either; the class keeps a
// Generator and a Load of the same name apart.

export const ASSET_NODE_PREFIX = 'asset-'
export type AssetClass = 'Generator' | 'Load' | 'StorageUnit' | 'Store' | 'Link'
const ASSET_CLASSES: readonly AssetClass[] = ['Generator', 'Load', 'StorageUnit', 'Store', 'Link']

export const assetNodeId = (cls: AssetClass, name: string): string => `${ASSET_NODE_PREFIX}${cls}:${name}`
export const isAssetNodeId = (id: string): boolean => id.startsWith(ASSET_NODE_PREFIX)

/** The component an asset node id names; null for any other id. */
export function componentFromAssetNodeId(id: string): { cls: AssetClass; name: string } | null {
  if (!isAssetNodeId(id)) return null
  const rest = id.slice(ASSET_NODE_PREFIX.length)
  const colon = rest.indexOf(':')
  if (colon < 0) return null
  const cls = rest.slice(0, colon) as AssetClass
  if (!ASSET_CLASSES.includes(cls)) return null
  return { cls, name: rest.slice(colon + 1) }
}

/** The asset edge from `bus` to the node; a multi-port Link gets one per port, suffixed by the port. */
export const assetEdgeId = (nodeId: string, port?: string): string =>
  port ? `assetedge-${nodeId}#${port}` : `assetedge-${nodeId}`

// ── Descriptors ───────────────────────────────────────────────────────────────

export interface AssetNodeComponents {
  generators: readonly Generator[]
  loads: readonly Load[]
  storageUnits: readonly StorageUnit[]
  stores: readonly Store[]
  links: readonly Link[]
}

export interface AssetNodeDescriptor {
  id: string
  cls: AssetClass
  name: string
  /** The bus the node is anchored to (a Link: the port the equipment stands at, per the type's rule). */
  bus: string
  /** Every bus the component connects to, `bus` first; a multi-port Link lists each set port. */
  buses: { bus: string; port: string }[]
  typeId: string
  /** The type's label for this carrier ("Battery storage", "Engine gensets (gas)"). */
  typeLabel: string
  color: string
  icon: AssetIconName
  carrier: string
  sizing: SizingFigure | null
  /** StorageUnits: hours of storage, so an effective p_nom can be shown in MWh. */
  maxHours?: number
}

const linkPortBuses = (l: Link): { bus: string; port: string }[] => {
  const out: { bus: string; port: string }[] = []
  for (const [k, v] of Object.entries(l as unknown as Record<string, unknown>)) {
    if (/^bus\d+$/.test(k) && typeof v === 'string' && v) out.push({ bus: v, port: k })
  }
  return out.sort((a, b) => Number(a.port.slice(3)) - Number(b.port.slice(3)))
}

/**
 * One descriptor per Generator, Load, StorageUnit and Store whose bus is in
 * the network, and per Link whose type is an asset (electrolyser, heat pump,
 * CHP, data-centre link, …; a feeder between two electrical buses stays an
 * edge). Input order is kept within each class; classes in the order above.
 */
export function buildAssetNodes(comps: AssetNodeComponents, ctx: MatchContext): AssetNodeDescriptor[] {
  const out: AssetNodeDescriptor[] = []
  const members = new Set(ctx.members)
  const push = (cls: AssetClass, comp: Record<string, unknown> & { name: string }, buses: { bus: string; port: string }[]) => {
    const m = matchType(cls as PyPSAClass, comp, ctx)
    if (!m) return
    if (cls === 'Link' && !isAssetNodeType(m.type.id)) return
    const carrier = (comp.carrier as string | undefined) ?? ''
    const ordered = [...buses.filter(b => b.bus === m.owner), ...buses.filter(b => b.bus !== m.owner && members.has(b.bus))]
    out.push({
      id: assetNodeId(cls, comp.name), cls, name: comp.name,
      bus: m.owner, buses: ordered,
      typeId: m.type.id, typeLabel: labelOf(m.type, carrier), color: m.type.color, icon: m.type.icon,
      carrier, sizing: sizingFigure(cls, comp, m.type.id),
      ...(cls === 'StorageUnit' ? { maxHours: Number(comp.max_hours) || 0 } : {}),
    })
  }
  const single = (cls: AssetClass, xs: readonly { name: string; bus: string }[]) => {
    for (const c of xs) push(cls, c as unknown as Record<string, unknown> & { name: string }, [{ bus: c.bus, port: 'bus' }])
  }
  single('Generator', comps.generators)
  single('Load', comps.loads)
  single('StorageUnit', comps.storageUnits)
  single('Store', comps.stores)
  for (const l of comps.links) push('Link', l as unknown as Record<string, unknown> & { name: string }, linkPortBuses(l))
  return out
}

/** The Links drawn as asset nodes — the canvas draws no edge for these. */
export const assetNodeLinkNames = (descs: readonly AssetNodeDescriptor[]): Set<string> =>
  new Set(descs.filter(d => d.cls === 'Link').map(d => d.name))

export interface AssetEdgeDescriptor { id: string; source: string; target: string; color: string }

/** The dashed connector from each of a node's buses to the node (the node is the target, as for group bubbles). */
export function buildAssetEdges(descs: readonly AssetNodeDescriptor[]): AssetEdgeDescriptor[] {
  const out: AssetEdgeDescriptor[] = []
  for (const d of descs) {
    const multi = d.buses.length > 1
    for (const b of d.buses) out.push({ id: assetEdgeId(d.id, multi ? b.port : undefined), source: b.bus, target: d.id, color: d.color })
  }
  return out
}

/** Legend rows for the types present (shared labels, colours, icons). */
export const assetLegend = (descs: readonly AssetNodeDescriptor[]): LegendEntry[] =>
  legendFor(descs.map(d => ({ kind: d.typeId, carrier: d.carrier })))

// ── Placement ─────────────────────────────────────────────────────────────────
// Around its bus, by zone: the words the 3D packer uses (site3d/layout.ts),
// so the two layers agree on where things are roughly — generation north,
// loads south, storage east, conversion west. Within a zone the nodes sit on
// an arc, up to four per ring, in name order; a per-name jitter keeps two
// identical layouts from drawing a perfect lattice. Deterministic.

export type CanvasZone = 'north' | 'south' | 'east' | 'west'

const ZONE_BY_TYPE: Record<string, CanvasZone> = {
  pvRoof: 'north', pv: 'north', wind: 'north', gasTurbine: 'north', thermal: 'north',
  flywheel: 'east', pumpedHydro: 'east', caes: 'east', h2store: 'east', thermalStore: 'east', bess: 'east', store: 'east',
  electrolyser: 'west', fuelCell: 'west', heatPump: 'west', chp: 'west',
  offtake: 'south', load: 'south',
}
const ZONE_BY_CLASS: Record<AssetClass, CanvasZone> = { Generator: 'north', Load: 'south', StorageUnit: 'east', Store: 'east', Link: 'west' }

export const zoneOf = (d: Pick<AssetNodeDescriptor, 'typeId' | 'cls'>): CanvasZone => ZONE_BY_TYPE[d.typeId] ?? ZONE_BY_CLASS[d.cls]

/** Screen angles (y grows downward): north is up. */
const ZONE_ANGLE_DEG: Record<CanvasZone, number> = { north: -90, east: 0, south: 90, west: 180 }
const SECTOR_HALF_DEG = 42
export const RING_RADIUS = 190
const RING_STEP = 105
const RING_CAPACITY = 4
const JITTER_PX = 6

/**
 * The offset of each node from its bus, for every node in `descs` (all buses
 * at once; nodes are grouped by bus and zone internally).
 */
export function ringOffsets(descs: readonly AssetNodeDescriptor[]): Map<string, { dx: number; dy: number }> {
  const out = new Map<string, { dx: number; dy: number }>()
  const groups = new Map<string, AssetNodeDescriptor[]>()
  for (const d of descs) {
    const k = `${d.bus}\u0000${zoneOf(d)}`
    const g = groups.get(k) ?? []
    g.push(d)
    groups.set(k, g)
  }
  for (const [k, g] of groups) {
    const zone = k.slice(k.indexOf('\u0000') + 1) as CanvasZone
    const centre = ZONE_ANGLE_DEG[zone]
    const sorted = [...g].sort((a, b) => a.name.localeCompare(b.name))
    sorted.forEach((d, i) => {
      const ring = Math.floor(i / RING_CAPACITY)
      const inRing = Math.min(RING_CAPACITY, sorted.length - ring * RING_CAPACITY)
      const slot = i - ring * RING_CAPACITY
      const angle = inRing === 1 ? centre : centre - SECTOR_HALF_DEG + (2 * SECTOR_HALF_DEG * slot) / (inRing - 1)
      const r = RING_RADIUS + ring * RING_STEP
      const h = hashStr(d.id)
      const jx = ((h & 0xff) / 255 - 0.5) * 2 * JITTER_PX
      const jy = (((h >>> 8) & 0xff) / 255 - 0.5) * 2 * JITTER_PX
      const rad = (angle * Math.PI) / 180
      out.set(d.id, { dx: Math.cos(rad) * r + jx, dy: Math.sin(rad) * r + jy })
    })
  }
  return out
}

/** The nodes as layout satellites (for `runLayout`). */
export function assetSatellites(descs: readonly AssetNodeDescriptor[]): LayoutSatellite[] {
  const offs = ringOffsets(descs)
  return descs.map(d => ({ id: d.id, busId: d.bus, ...offs.get(d.id)! }))
}

// ── Persistence ───────────────────────────────────────────────────────────────

/**
 * The asset-node positions worth saving: every cached `asset-…` position
 * whose component still exists. A node whose component is gone is dropped
 * HERE, on save — never on read, so a layout document is not rewritten by
 * merely opening it (the orphan rule the 3D sidecar uses). Positions of nodes
 * not currently drawn (grouped mode) are kept, so switching modes loses nothing.
 */
export function liveAssetPositions(
  positions: Readonly<Record<string, { x: number; y: number }>>,
  liveNodeIds: ReadonlySet<string>,
): PersistedNode[] {
  const out: PersistedNode[] = []
  for (const [id, p] of Object.entries(positions)) {
    if (!isAssetNodeId(id) || !liveNodeIds.has(id)) continue
    out.push({ id, canvasX: p.x, canvasY: p.y })
  }
  return out
}
