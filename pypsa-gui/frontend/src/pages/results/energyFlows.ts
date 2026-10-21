// The energy-flow result shaped for the Results → Energy flow tab (A1): the
// three-column Sankey, the per-link breakdown for the tooltip and the CSV.
// Pure functions, so every mapping is tested without a DOM.
import type { EnergyFlowRow } from '../../api/energyFlows'

export type FlowColumn = 'source' | 'via' | 'destination'

export interface FlowNode { name: string; column: FlowColumn; color: string }
export interface FlowLink {
  source: number; target: number; value: number
  /** The (source, via, destination) rows this link carries, largest first. */
  rows: EnergyFlowRow[]
  /** The colour of the source that supplies most of this link. */
  color: string
}
export interface FlowSankey { nodes: FlowNode[]; links: FlowLink[] }

// Literal hex on purpose: `var(--…)` does not resolve inside SVG attributes.
const SOURCE_COLORS: Record<string, string> = {
  'Grid import': '#6b7280', solar: '#f59e0b', wind: '#0ea5e9', gas: '#a16207',
  diesel: '#7c2d12', hydro: '#2563eb',
}
const PALETTE = ['#16a34a', '#9333ea', '#db2777', '#0d9488', '#ca8a04', '#4f46e5', '#ea580c']
const VIA_COLOR = '#475569'
const LOSS_COLOR = '#dc2626'
const EXPORT_COLOR = '#0891b2'
const DEST_COLOR = '#2563eb'

export function sourceColor(name: string, index: number): string {
  return SOURCE_COLORS[name.toLowerCase()] ?? SOURCE_COLORS[name]
    ?? PALETTE[index % PALETTE.length]
}

export function destinationColor(name: string): string {
  return name === 'Losses' ? LOSS_COLOR : name === 'Export' ? EXPORT_COLOR : DEST_COLOR
}

/** MWh with a unit prefix that keeps three significant figures. */
export function fmtMwh(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return 'not established'
  const a = Math.abs(v)
  if (a >= 1e6) return `${(v / 1e6).toLocaleString(undefined, { maximumFractionDigits: 2 })} TWh`
  if (a >= 1e3) return `${(v / 1e3).toLocaleString(undefined, { maximumFractionDigits: 2 })} GWh`
  return `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })} MWh`
}

/** Source → Via → Destination as a Sankey: every row passes through its via
 *  node ("Direct" or a storage), so "Solar via Battery" and "Grid via Battery"
 *  are two links into the Battery node. Nodes are ordered by throughput. */
export function sankeyData(rows: EnergyFlowRow[]): FlowSankey {
  const live = rows.filter(r => r.mwh > 0)
  const total = (col: FlowColumn) => {
    const m = new Map<string, number>()
    for (const r of live) m.set(r[col], (m.get(r[col]) ?? 0) + r.mwh)
    return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k]) => k)
  }
  const nodes: FlowNode[] = []
  const at = new Map<string, number>()
  const add = (column: FlowColumn, name: string, color: string) => {
    at.set(`${column}:${name}`, nodes.length)
    nodes.push({ name, column, color })
  }
  total('source').forEach((s, i) => add('source', s, sourceColor(s, i)))
  total('via').forEach(v => add('via', v, VIA_COLOR))
  total('destination').forEach(d => add('destination', d, destinationColor(d)))

  const links = new Map<string, FlowLink>()
  const bump = (from: string, to: string, r: EnergyFlowRow) => {
    const key = `${from}\u0000${to}`
    const link = links.get(key) ?? { source: at.get(from)!, target: at.get(to)!, value: 0, rows: [], color: '' }
    link.value += r.mwh
    link.rows.push(r)
    links.set(key, link)
  }
  for (const r of live) {
    bump(`source:${r.source}`, `via:${r.via}`, r)
    bump(`via:${r.via}`, `destination:${r.destination}`, r)
  }
  const out = [...links.values()]
  const colorOf = new Map(nodes.filter(n => n.column === 'source').map(n => [n.name, n.color]))
  out.forEach(l => {
    l.rows.sort((a, b) => b.mwh - a.mwh)
    // The dominant source by energy, not by row: one source can split over many rows.
    const bySource = new Map<string, number>()
    for (const r of l.rows) bySource.set(r.source, (bySource.get(r.source) ?? 0) + r.mwh)
    const top = [...bySource.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0][0]
    l.color = colorOf.get(top) ?? VIA_COLOR
  })
  return { nodes, links: out }
}

export const CSV_HEADER = ['Source', 'Via', 'Destination', 'MWh']

/** The chart's rows, in the order and with the values the server's CSV has. */
export function csvRows(rows: EnergyFlowRow[]): Array<Array<string | number>> {
  return rows.map(r => [r.source, r.via, r.destination, Number(r.mwh.toFixed(6))])
}

export const periodLabel = (p: number | null | undefined) => (p == null ? 'Modelled year' : String(p))

/** A share, or null when there is no total to share (never a made-up 0 %). */
export function share(part: number, whole: number): number | null {
  return whole > 0 ? part / whole : null
}
