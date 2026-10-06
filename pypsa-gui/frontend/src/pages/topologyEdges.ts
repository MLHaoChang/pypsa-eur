// ── Link edges for the two canvases (pure) ────────────────────────────────────
// Everything that turns a PyPSA Link row into the edges a canvas draws, with
// no React or React Flow in it so the rule "one edge per set port" can be
// tested over the palette without mounting a canvas (visual-layers plan 1,
// A1). TopologyCanvas builds its React Flow edges from `buildLinkEdges`;
// MapCanvas uses `linkPorts` and the id helpers for its chords.
//
// Edge ids. The main edge keeps its historical id `link-<name>` (bus0 → bus1).
// Each extra port draws as `link-<name>#<port>` (bus0 → bus<port>); the map
// keys its waypoints `link:<name>#<port>` the same way. Every place that
// recovers a component from an edge id goes through `componentNameFromEdgeId`
// so the `#<port>` suffix never leaks into an API path or a selection.
import type { Link } from '../api/types'
import type { WP } from './topologyLayoutStore'

// ── Carrier colours for Link edges ───────────────────────────────────────────
// Moved verbatim from TopologyCanvas so MapCanvas can colour an extra port by
// its far bus's carrier with the same helper the schematic's main link edge
// uses — one table, both canvases. (utils/assetTypes classifies a Bus only as
// switchyard vs manifold, which cannot tell a heat far-bus from an H₂ one.)
export const CARRIER_LINK_COLORS: Record<string, string> = {
  H2: '#7c3aed', electrolysis: '#7c3aed', 'H2 electrolysis': '#7c3aed',
  heat: '#ea580c', heat_pump: '#f97316',
  gas: '#0369a1', CCGT: '#0369a1', OCGT: '#0891b2',
  battery: '#16a34a', BEV: '#16a34a',
  DC: '#d97706', HVDC: '#d97706',
  AC: '#e60012',
}
export const FALLBACK_COLORS = ['#7c3aed', '#ea580c', '#0369a1', '#16a34a', '#d97706', '#ec4899', '#06b6d4', '#84cc16', '#f43f5e']

export function getLinkColor(carrier: string, fallbackIdx: number): string {
  if (CARRIER_LINK_COLORS[carrier]) return CARRIER_LINK_COLORS[carrier]
  for (const [key, col] of Object.entries(CARRIER_LINK_COLORS)) {
    if (carrier.toLowerCase().includes(key.toLowerCase())) return col
  }
  return FALLBACK_COLORS[fallbackIdx % FALLBACK_COLORS.length]
}

// ── Ports ─────────────────────────────────────────────────────────────────────

/** PyPSA's extra-port columns: `bus2`, `bus3`, … (`pypsa.constants.RE_PORTS_GE_2`). */
const PORT_BUS_RE = /^bus((?:[2-9]|[1-9]\d+))$/

export interface LinkPort {
  /** 2 for `bus2` / `efficiency2`, 3 for `bus3`, … */
  port: number
  /** The far bus of this port. */
  bus: string
  /** `efficiency<port>` when the row carries it; PyPSA's default is 1. */
  efficiency: number | undefined
}

/**
 * The set extra ports of a Link row, lowest port first. A port is set when
 * its `bus<i>` is a non-empty string — PyPSA writes '' for an unused port on
 * a frame where another Link has one.
 */
export function linkPorts(link: Record<string, unknown>): LinkPort[] {
  const out: LinkPort[] = []
  for (const key of Object.keys(link)) {
    const m = PORT_BUS_RE.exec(key)
    if (!m) continue
    const bus = link[key]
    if (typeof bus !== 'string' || bus === '') continue
    const port = Number(m[1])
    const eff = link[`efficiency${port}`]
    out.push({ port, bus, efficiency: typeof eff === 'number' && Number.isFinite(eff) ? eff : undefined })
  }
  return out.sort((a, b) => a.port - b.port)
}

// ── Edge ids ──────────────────────────────────────────────────────────────────

export const linkEdgeId = (name: string, port?: number): string =>
  port == null ? `link-${name}` : `link-${name}#${port}`

const PORT_SUFFIX_RE = /#(\d+)$/

/**
 * The component a schematic edge id names: `line-L1` → `L1`, `tr-T1` → `T1`,
 * `link-K1` and `link-K1#2` → `K1`. Only link ids carry a port suffix, so a
 * `#` in a Line's own name is left alone.
 */
export function componentNameFromEdgeId(edgeId: string): string {
  if (edgeId.startsWith('link-')) return edgeId.slice('link-'.length).replace(PORT_SUFFIX_RE, '')
  return edgeId.replace(/^(line-|tr-)/, '')
}

/** The port an extra-port link edge draws (`link-K1#2` → 2); undefined for the main edge. */
export function linkPortFromEdgeId(edgeId: string): number | undefined {
  if (!edgeId.startsWith('link-')) return undefined
  const m = PORT_SUFFIX_RE.exec(edgeId)
  return m ? Number(m[1]) : undefined
}

// ── Derived flow ──────────────────────────────────────────────────────────────

/**
 * `/results/links` serves `p0` only. PyPSA defines the flow at every other
 * port as `p_i = -p0 × efficiency_i` (optimize.py writes exactly that), so a
 * canvas can show an extra port's flow without a second endpoint. The sign
 * follows PyPSA: positive `p_i` is a withdrawal from bus_i, so a CHP burning
 * fuel (p0 > 0) has p2 < 0 — heat leaving the link into the heat bus.
 */
export const derivedPortFlow = (p0: number, efficiency: number | undefined): number =>
  -p0 * (efficiency ?? 1)

// ── Edge building ─────────────────────────────────────────────────────────────

export interface LinkEdgeData extends Record<string, unknown> {
  s_nom?: number
  type: 'link'
  /** The main edge: the Link's carrier. An extra port: the far bus's carrier. */
  carrier?: string
  color: string
  /** Set on an extra-port edge only. */
  port?: number
  efficiency?: number
  waypoints: WP[]
  history: WP[][]
}

export interface LinkEdge {
  id: string
  source: string
  target: string
  type: 'network'
  data: LinkEdgeData
}

export interface BuildLinkEdgesOptions {
  /** Colour of the main edge, from the Link's own carrier (the canvas's carrier → colour map). */
  linkColor: (carrier: string) => string
  /** Carrier of a bus by name; undefined when the bus is not in the network. */
  busCarrier: (bus: string) => string | undefined
  /** Colour of an extra-port edge, from the FAR bus's carrier. Defaults to `linkColor`. */
  portColor?: (farCarrier: string) => string
}

/**
 * One edge per Link (bus0 → bus1) plus one per set extra port (bus0 →
 * bus<i>), unique by id. An extra port whose far bus is not in the network
 * is not drawn — React Flow would drop an edge to a missing node anyway, and
 * noisily. The main edge is unchanged from before multi-port drawing.
 */
export function buildLinkEdges(links: readonly Link[], opts: BuildLinkEdgesOptions): LinkEdge[] {
  const byId = new Map<string, LinkEdge>()
  const portColor = opts.portColor ?? opts.linkColor
  for (const l of links) {
    byId.set(linkEdgeId(l.name), {
      id: linkEdgeId(l.name), source: l.bus0, target: l.bus1, type: 'network',
      data: {
        s_nom: l.p_nom, type: 'link', carrier: l.carrier, color: opts.linkColor(l.carrier),
        waypoints: [], history: [[]],
      },
    })
    for (const p of linkPorts(l as unknown as Record<string, unknown>)) {
      const farCarrier = opts.busCarrier(p.bus)
      if (farCarrier === undefined) continue
      const id = linkEdgeId(l.name, p.port)
      byId.set(id, {
        id, source: l.bus0, target: p.bus, type: 'network',
        data: {
          s_nom: l.p_nom, type: 'link', carrier: farCarrier, color: portColor(farCarrier),
          port: p.port, efficiency: p.efficiency,
          waypoints: [], history: [[]],
        },
      })
    }
  }
  return [...byId.values()]
}
